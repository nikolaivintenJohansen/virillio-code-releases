#!/usr/bin/env python3
"""Prepare and rebuild the pinned LGPL runtime; no Virillio source is an input.

SPDX-License-Identifier: MIT
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile


def verified_archive(item, archives):
    relative = Path(item["file"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe source archive path")
    archive = archives / relative
    if archive.is_symlink() or not archive.resolve().is_relative_to(archives.resolve()):
        raise ValueError("Source archive escapes its directory")
    if archive.stat().st_size != item["size"]:
        raise ValueError(f"Source archive size mismatch: {item['file']}")
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != item["sha256"]:
        raise ValueError(f"Source archive hash mismatch: {item['file']}")
    return archive


def unpack(item, archives, destination):
    archive = verified_archive(item, archives)
    with tarfile.open(archive) as source:
        for entry in source:
            if entry.name != item["root"] and not entry.name.startswith(item["root"] + "/"):
                raise ValueError(f"Unexpected archive root: {entry.name}")
        source.extractall(destination, filter="data")


def seed_build_inputs(inputs, manifest, archives, workspace, bun):
    if inputs["bunRevision"] != manifest["bunRevision"] or inputs["target"] != "aarch64-apple-darwin":
        raise ValueError("Build inputs do not match the pinned runtime and target")
    cache = workspace / "build-cache/tarballs"
    cache.mkdir(parents=True)
    for item in inputs["dependencyArchives"]:
        expected = item["name"] + "-" + hashlib.sha256(item["url"].encode()).hexdigest()[:16] + ".tar.gz"
        if Path(item["file"]).name != expected:
            raise ValueError("Dependency cache identity does not match its source URL")
        for patch in item["patches"]:
            file = bun / patch["file"]
            if not file.resolve().is_relative_to(bun) or hashlib.sha256(file.read_bytes()).hexdigest() != patch["sha256"]:
                raise ValueError("Pinned dependency patch does not match")
        shutil.copyfile(verified_archive(item, archives), cache / expected)
    vendor = workspace / "cargo-vendor"
    vendor.mkdir()
    crates = {}
    for item in inputs["rustCrates"] + inputs.get("standardLibraryCrates", []):
        root = item["name"] + "-" + item["version"]
        if root in crates:
            if crates[root] != item["sha256"]:
                raise ValueError("Conflicting preserved Rust crate")
            continue
        crates[root] = item["sha256"]
        unpack({**item, "root": root}, archives, vendor)
        directory = vendor / root
        checksums = {
            file.relative_to(directory).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(directory.rglob("*")) if file.is_file() and file.name != ".cargo-checksum.json"
        }
        (directory / ".cargo-checksum.json").write_text(json.dumps({"package": item["sha256"], "files": checksums}) + "\n")
    cargo = workspace / "cargo-home"
    cargo.mkdir()
    (cargo / "config.toml").write_text(
        '[source.crates-io]\nreplace-with = "preserved-sources"\n'
        '[source.preserved-sources]\ndirectory = ' + json.dumps(str(vendor)) + '\n'
    )


def replace_once(file, before, after):
    source = file.read_text()
    if source.count(before) != 1:
        raise ValueError(f"Pinned source does not match patch: {file.name}")
    file.write_text(source.replace(before, after))


def webkit_files(directory, allowed):
    files = []

    def visit(current):
        for item in sorted(current.iterdir()):
            relative = item.relative_to(directory)
            if relative.parts[0] == ".git":
                continue
            if relative.parts[0] not in allowed:
                raise ValueError("Public WebKit checkout included an unallowlisted path: " + relative.as_posix())
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                files.append({"path": relative.as_posix(), "mode": stat.S_IMODE(info.st_mode), "kind": "symlink", "target": os.readlink(item)})
                continue
            if stat.S_ISDIR(info.st_mode):
                visit(item)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Public WebKit checkout contained an unsupported filesystem entry")
            with item.open("rb") as stream:
                files.append(
                    {
                        "path": relative.as_posix(),
                        "mode": stat.S_IMODE(info.st_mode),
                        "kind": "file",
                        "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                        "size": info.st_size,
                    }
                )

    visit(directory)
    return files


def checked_webkit_report(source, report_file, source_item):
    report = json.loads(report_file.read_text())
    manifest_hash = report.pop("manifestSHA256", None)
    expected_hash = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    provenance = json.loads(Path(__file__).with_name("webkit-source-provenance.json").read_text())
    if (
        manifest_hash != expected_hash
        or report.get("repository") != source_item.get("repository")
        or report.get("revision") != source_item.get("revision")
        or report.get("includedRoots") != provenance.get("includedRoots")
        or report.get("excludedRoots") != provenance.get("excludedRoots")
        or report.get("legacyArchive") != {"size": source_item.get("size"), "sha256": source_item.get("sha256"), "reproduced": False}
        or not isinstance(report.get("files"), list)
        or webkit_files(source, set(provenance["includedRoots"])) != report["files"]
    ):
        raise ValueError("Public WebKit report does not match the pinned source identity")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()
    if revision != source_item["revision"] or status:
        raise ValueError("Public WebKit checkout no longer matches its pinned revision")
    return manifest_hash


def copy_webkit(source, destination, report_file, source_item):
    source = source.resolve()
    if source.is_symlink() or not source.is_dir():
        raise ValueError("WebKit checkout must be a real directory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("WebKit destination already exists")
    manifest_hash = checked_webkit_report(source, report_file, source_item)
    shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    return manifest_hash


def prepare(manifest, archives, workspace, probe, webkit_dir=None, webkit_report=None):
    # A fresh output directory prevents stale libraries from proving a false rebuild.
    if workspace.exists():
        raise ValueError("Choose a new workspace directory; existing paths are never overwritten")
    workspace.mkdir(parents=True)
    sources = manifest["sources"]
    if len(sources) != 3 or sources[1].get("root") != "WebKit":
        raise ValueError("Expected the pinned Bun, WebKit, and TinyCC source set")
    for item in ((sources[0], sources[2]) if webkit_dir else sources):
        unpack(item, archives, workspace)
    bun = workspace / sources[0]["root"]
    vendor = bun / "vendor"
    vendor.mkdir(exist_ok=True)
    if webkit_dir:
        webkit_manifest_hash = copy_webkit(webkit_dir, vendor / "WebKit", webkit_report, sources[1])
    else:
        shutil.move(workspace / "WebKit", vendor / "WebKit")
        webkit_manifest_hash = None
    shutil.move(workspace / sources[2]["root"], vendor / "tinycc")
    # Local source mode deliberately preserves the user's TinyCC modifications.
    replace_once(
        bun / "scripts/build/deps/tinycc.ts",
        'import type { Dependency, DirectBuild } from "../source.ts";',
        'import { resolve } from "node:path";\nimport type { Dependency, DirectBuild } from "../source.ts";',
    )
    replace_once(
        bun / "scripts/build/deps/tinycc.ts",
        'source: () => ({\n    kind: "github-archive",\n    repo: "oven-sh/tinycc",\n    commit: TINYCC_COMMIT,\n  }),',
        'source: () => ({\n    kind: "local",\n    path: resolve(import.meta.dir, "../../../vendor/tinycc"),\n    hint: "Extract the pinned TinyCC archive into vendor/tinycc before building.",\n  }),',
    )
    # Bun's version header otherwise omits a local source's required TinyCC macro.
    replace_once(bun / "scripts/build/deps/tinycc.ts", "const TINYCC_COMMIT =", "export const TINYCC_COMMIT =")
    replace_once(
        bun / "scripts/build/depVersionsHeader.ts",
        'import { allDeps } from "./deps/index.ts";',
        'import { allDeps } from "./deps/index.ts";\nimport { TINYCC_COMMIT } from "./deps/tinycc.ts";',
    )
    replace_once(
        bun / "scripts/build/depVersionsHeader.ts",
        '    } else if (id !== undefined) {',
        '    } else if (dep.name === "tinycc") {\n      versions.push([dep.versionMacro, TINYCC_COMMIT]);\n    } else if (id !== undefined) {',
    )
    # This is Bun's original patches/tinycc/tcc.h.patch, applied to the local tree.
    replace_once(
        vendor / "tinycc/tcc.h",
        '#define _DARWIN_C_SOURCE\n#include "config.h"',
        '#define _DARWIN_C_SOURCE\n#if __has_include("config.h")\n#include "config.h"\n#endif',
    )
    # The public runner is deliberately memory constrained. Keep Zig's outer
    # build scheduler to one worker in addition to Bun's upstream CI codegen
    # setting and ZIG_PARALLEL_SEMA=1.
    replace_once(
        bun / "scripts/build/zig.ts",
        'command: `${stream} ${consoleMode ? "--console" : "--zig-progress"} --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build $step $args`,',
        'command: `${stream} ${consoleMode ? "--console" : "--zig-progress"} --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build -j1 $step $args`,',
    )
    replace_once(
        bun / "scripts/build/zig.ts",
        'command: `${stream} --console --stamp=$out --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build $step $args`,',
        'command: `${stream} --console --stamp=$out --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build -j1 $step $args`,',
    )
    # CMake evaluates every OR argument. Keep operands unexpanded so an empty
    # global-property value cannot disappear while CMake parses the condition.
    replace_once(
        vendor / "WebKit/Source/cmake/WebKitMacros.cmake",
        'if ((NOT _linked_into) OR (${framework} STREQUAL ${_linked_into}) OR (NOT ${_linked_into} IN_LIST ${_target}_FRAMEWORKS))',
        'if (NOT _linked_into OR framework STREQUAL _linked_into OR NOT _linked_into IN_LIST ${_target}_FRAMEWORKS)',
    )
    if probe:
        file = vendor / "WebKit/Source/JavaScriptCore/runtime/LiteralParser.h"
        source = file.read_text()
        if source.count("JSON Parse error:") != 3:
            raise ValueError("Pinned JavaScriptCore probe source does not match")
        file.write_text(source.replace("JSON Parse error:", "Virillio LGPL rebuild probe:"))
        before = '    cstr_printf(cs, "#define __TINYC__ 9%.2s\\n", &TCC_VERSION[4]);'
        replace_once(
            vendor / "tinycc/tccpp.c",
            before,
            before + '\n    cstr_printf(cs, "#define __VIRILLIO_LGPL_REBUILD_PROBE__ 20260906\\n");',
        )
    receipt = {
        "bunRevision": manifest["bunRevision"],
        "libraryProbeApplied": probe,
        "webkitCmakeCompatibilityPatchApplied": True,
        "webkitInput": "public-checkout" if webkit_dir else "archive",
        "webkitManifestSHA256": webkit_manifest_hash,
        "buildCompleted": False,
    }
    (workspace / "rebuild-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return bun, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bun", type=Path, required=True, help="Original Bun 1.3.14 executable used for code generation")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--build-inputs", type=Path, default=Path(__file__).with_name("build-inputs.json"))
    parser.add_argument("--webkit-dir", type=Path, help="Verified public WebKit checkout used instead of the legacy filtered archive")
    parser.add_argument("--webkit-report", type=Path, help="Content report emitted by fetch-webkit.py")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--probe", action="store_true", help="Apply observable library changes for the replacement test")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or later is required for safe source archive extraction")
    if platform.system() != "Darwin" or platform.machine() not in ("arm64", "aarch64"):
        parser.error("This tested recipe currently targets native macOS ARM64 only")
    if args.jobs < 1 or args.jobs > 32:
        parser.error("--jobs must be between 1 and 32")
    if bool(args.webkit_dir) != bool(args.webkit_report):
        parser.error("--webkit-dir and --webkit-report must be supplied together")
    manifest = json.loads(args.manifest.read_text())
    version = subprocess.check_output([str(args.bun.resolve()), "--version"], text=True).strip()
    if version != "1.3.14":
        parser.error("Code generation requires the pinned Bun 1.3.14 executable")
    bun, receipt = prepare(
        manifest,
        args.archives.resolve(),
        args.workspace.resolve(),
        args.probe,
        args.webkit_dir.resolve() if args.webkit_dir else None,
        args.webkit_report.resolve() if args.webkit_report else None,
    )
    seed_build_inputs(json.loads(args.build_inputs.read_text()), manifest, args.archives.resolve(), args.workspace.resolve(), bun)
    print(f"Prepared pinned sources at {bun}", flush=True)
    if args.prepare_only:
        return
    env = os.environ.copy()
    env["PATH"] = str(args.bun.resolve().parent) + os.pathsep + env.get("PATH", "")
    env["GIT_SHA"] = manifest["bunRevision"]
    env["SDKROOT"] = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(args.jobs)
    env["CARGO_HOME"] = str(args.workspace.resolve() / "cargo-home")
    subprocess.run([str(args.bun.resolve()), "install", "--frozen-lockfile"], cwd=bun, env=env, check=True)
    subprocess.run(
        [str(args.bun.resolve()), "scripts/build.ts", "--profile=release-local", "--build-dir=build/release-local", "--canary=off", f"--cache-dir={args.workspace.resolve() / 'build-cache'}", f"-j{args.jobs}"],
        cwd=bun, env=env, check=True,
    )
    binary = bun / "build/release-local/bun"
    with binary.open("rb") as stream:
        receipt.update({"buildCompleted": True, "runtimeSHA256": hashlib.file_digest(stream, "sha256").hexdigest()})
    (args.workspace.resolve() / "rebuild-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Built replacement runtime: {binary}")


if __name__ == "__main__":
    main()
