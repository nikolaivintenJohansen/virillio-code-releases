#!/usr/bin/env python3
"""Write fail-closed evidence for the public ARM64 Bun replacement runtime."""

import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tarfile


BUN_REVISION = "0d9b296af33f2b851fcbf4df3e9ec89751734ba4"
BUILDER_REPOSITORY = "nikolaivintenJohansen/virillio-code-releases"
EXPECTED_SOURCE_FILES = {
    "LICENSE",
    "README.md",
    "build-inputs.json",
    "extract-llvm.py",
    "fetch-inputs.py",
    "fetch-node-headers.py",
    "fetch-original-bun.py",
    "fetch-webkit.py",
    "fetch-zig.py",
    "finish-rebuild.py",
    "rebuild.py",
    "runtime-probe.c",
    "runtime-probe.ts",
    "sources.json",
    "webkit-source-provenance.json",
    "write-provenance.py",
}
BOOTSTRAP = {
    "llvm": {
        "url": "https://github.com/llvm/llvm-project/releases/download/llvmorg-21.1.8/LLVM-21.1.8-macOS-ARM64.tar.xz",
        "sha256": "b95bdd32a33a81ee4d40363aaeb26728a26783fcef26a4d80f65457433ea4669",
        "size": 1503047352,
        "version": "clang version 21.1.8",
    },
    "originalBun": {
        "url": "https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-darwin-aarch64.zip",
        "sha256": "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620",
        "size": 23586433,
        "binarySHA256": "e0c90ec15d33363e6b70713d56bc3b2c7585c17f40a0fe0f8fd9305901d4e233",
        "binarySize": 63096576,
        "version": "1.3.14",
        "revision": "1.3.14+0d9b296af",
    },
    "zig": {
        "url": "https://github.com/oven-sh/zig/releases/download/autobuild-04e7f6ac1e009525bc00934f20199c68f04e0a24/bootstrap-aarch64-macos-none-ReleaseSafe.zip",
        "sha256": "b4eaff25ff3b665a48629c4f08ac8bc5f7da8d925307aec8bf43d2cd77355882",
        "size": 97479085,
        "version": "0.15.2",
    },
    "nodeHeaders": {
        "url": "https://nodejs.org/dist/v24.3.0/node-v24.3.0-headers.tar.gz",
        "sha256": "045e9bf477cd5db0ec67f8c1a63ba7f784dedfe2c581e3d0ed09b88e9115dd07",
        "size": 8747815,
        "version": "24.3.0",
        "removed": ["include/node/openssl", "include/node/uv", "include/node/uv.h"],
    },
}


def fail(message):
    raise ValueError(message)


def regular(file, label):
    if file.is_symlink() or not file.is_file():
        fail(f"{label} must be a regular file")
    return file


def digest(file):
    regular(file, str(file))
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(file, label):
    try:
        return json.loads(regular(file, label).read_text())
    except json.JSONDecodeError as error:
        fail(f"{label} was not valid JSON: {error.msg}")


def require(value, message):
    if not value:
        fail(message)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command(arguments):
    result = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        fail("Command failed: " + " ".join(arguments) + "\n" + result.stdout[-2000:])
    return result.stdout.strip()


def source_inputs(sources, build_inputs):
    expected = []
    skipped = []
    for item in sources["sources"]:
        if "url" in item:
            expected.append(item)
            continue
        skipped.append({"name": item["name"], "reason": "verified separately from its public git checkout"})
    for key in ("dependencyArchives", "rustCrates", "standardLibrarySources", "standardLibraryCrates"):
        expected.extend(build_inputs.get(key, []))
    rows = {}
    for item in expected:
        required = {key: item.get(key) for key in ("file", "url", "size", "sha256")}
        require(all(required.values()), "Pinned source inputs are incomplete")
        previous = rows.setdefault(required["file"], required)
        require(previous == required, "Pinned source inputs contain conflicting archive identities")
    return rows, skipped


def validate_inputs(report, sources, build_inputs):
    require(
        set(report) == {"schemaVersion", "bunRevision", "fetched", "skipped", "allVerified"}
        and report.get("schemaVersion") == 1
        and report.get("bunRevision") == BUN_REVISION,
        "Input report identity is invalid",
    )
    require(report.get("allVerified") is True, "Input report did not verify every downloaded input")
    expected, skipped = source_inputs(sources, build_inputs)
    rows = report.get("fetched")
    require(isinstance(rows, list) and len(rows) == len(expected) == 104, "Input report did not contain all 104 public inputs")
    actual = {}
    for row in rows:
        require(
            isinstance(row, dict)
            and set(row) == {"file", "url", "finalHost", "size", "sha256", "reused"}
            and isinstance(row.get("file"), str),
            "Input report contained an invalid input row",
        )
        require(row["file"] not in actual, "Input report contained a duplicate input row")
        require(row.get("finalHost") is None or isinstance(row.get("finalHost"), str), "Input report leaked an invalid redirect record")
        require(isinstance(row.get("reused"), bool), "Input report omitted download reuse state")
        actual[row["file"]] = {key: row.get(key) for key in ("file", "url", "size", "sha256")}
    require(actual == expected, "Input report does not match the pinned public source manifests")
    require(report.get("skipped") == skipped, "Input report skipped an unexpected source")
    return {"verifiedInputCount": len(rows), "inputReportSHA256": None}


def validate_webkit(report, sources, provenance):
    source = sources["sources"][1]
    manifest = dict(report)
    manifest_hash = manifest.pop("manifestSHA256", None)
    require(manifest_hash == canonical_hash(manifest), "WebKit content report has an invalid manifest hash")
    require(
        set(report) == {"schemaVersion", "repository", "revision", "gitTree", "includedRoots", "excludedRoots", "files", "legacyArchive", "manifestSHA256"}
        and report.get("schemaVersion") == 1
        and report.get("repository") == source["repository"]
        and report.get("revision") == source["revision"]
        and isinstance(report.get("gitTree"), str)
        and re.fullmatch(r"[0-9a-f]{40}", report["gitTree"]) is not None
        and report.get("includedRoots") == provenance["includedRoots"]
        and report.get("excludedRoots") == provenance["excludedRoots"]
        and report.get("legacyArchive")
        == {"size": source["size"], "sha256": source["sha256"], "reproduced": False}
        and isinstance(report.get("files"), list),
        "WebKit content report does not prove the pinned public sparse checkout",
    )
    return {
        "revision": report["revision"],
        "gitTree": report["gitTree"],
        "contentManifestSHA256": manifest_hash,
        "contentEntries": len(report["files"]),
        "legacyArchiveReproduced": False,
    }


def validate_archive(report, expected, label):
    archive = report.get("archive")
    require(
        isinstance(archive, dict)
        and set(archive) == {"url", "finalHost", "size", "sha256"}
        and archive.get("url") == expected["url"]
        and archive.get("sha256") == expected["sha256"]
        and archive.get("size") == expected["size"]
        and (archive.get("finalHost") is None or isinstance(archive.get("finalHost"), str)),
        f"{label} report does not match its pinned archive",
    )


def validate_bootstrap(llvm, original_bun, zig, zig_path, node_headers, node_headers_dir):
    validate_archive(llvm, BOOTSTRAP["llvm"], "LLVM")
    require(
        set(llvm) == {"schemaVersion", "archive", "llvmRoot", "extractedFiles", "clangVersion", "toolSHA256"}
        and llvm.get("schemaVersion") == 1
        and llvm.get("llvmRoot") == "LLVM-21.1.8-macOS-ARM64"
        and llvm.get("clangVersion") == BOOTSTRAP["llvm"]["version"]
        and isinstance(llvm.get("toolSHA256"), dict)
        and set(llvm["toolSHA256"]) == {"clang-21", "llvm-ar", "llvm-objcopy", "dsymutil"},
        "LLVM report is incomplete",
    )
    validate_archive(original_bun, BOOTSTRAP["originalBun"], "Original Bun")
    executable = original_bun.get("executable")
    require(
        set(original_bun) == {"schemaVersion", "archive", "executable"}
        and original_bun.get("schemaVersion") == 1
        and isinstance(executable, dict)
        and executable
        == {
            "size": BOOTSTRAP["originalBun"]["binarySize"],
            "sha256": BOOTSTRAP["originalBun"]["binarySHA256"],
            "version": BOOTSTRAP["originalBun"]["version"],
            "revision": BOOTSTRAP["originalBun"]["revision"],
        },
        "Original Bun report is incomplete",
    )
    validate_archive(zig, BOOTSTRAP["zig"], "Zig")
    require(
        set(zig) == {"schemaVersion", "archive", "version", "zigSHA256", "zigSize"}
        and zig.get("schemaVersion") == 1
        and zig.get("version") == BOOTSTRAP["zig"]["version"]
        and isinstance(zig.get("zigSHA256"), str)
        and isinstance(zig.get("zigSize"), int),
        "Zig report is incomplete",
    )
    executable = zig_path.resolve() / "zig"
    require(
        executable.is_file()
        and not executable.is_symlink()
        and digest(executable) == zig["zigSHA256"]
        and executable.stat().st_size == zig["zigSize"]
        and command([str(executable), "version"]) == BOOTSTRAP["zig"]["version"],
        "The build-time Zig executable does not match its verified report",
    )
    validate_archive(node_headers, BOOTSTRAP["nodeHeaders"], "Node header")
    require(
        set(node_headers) == {"schemaVersion", "archive", "version", "identity", "removed", "nodeHeaderSHA256", "nodeHeaderSize"}
        and node_headers.get("schemaVersion") == 1
        and node_headers.get("version") == BOOTSTRAP["nodeHeaders"]["version"]
        and node_headers.get("identity") == BOOTSTRAP["nodeHeaders"]["version"] + "\n"
        and node_headers.get("removed") == BOOTSTRAP["nodeHeaders"]["removed"]
        and isinstance(node_headers.get("nodeHeaderSHA256"), str)
        and isinstance(node_headers.get("nodeHeaderSize"), int),
        "Node header report is incomplete",
    )
    node_headers_dir = node_headers_dir.resolve()
    node_header = node_headers_dir / "include/node/node.h"
    require(
        node_headers_dir.is_dir()
        and not node_headers_dir.is_symlink()
        and (node_headers_dir / ".identity").read_text() == BOOTSTRAP["nodeHeaders"]["version"] + "\n"
        and not any((node_headers_dir / path).exists() or (node_headers_dir / path).is_symlink() for path in BOOTSTRAP["nodeHeaders"]["removed"])
        and digest(node_header) == node_headers["nodeHeaderSHA256"]
        and node_header.stat().st_size == node_headers["nodeHeaderSize"],
        "Prepared Node header cache does not match its verified report",
    )


def source_member_digest(archive, root, relative):
    regular(archive, "Pinned Bun source archive")
    with tarfile.open(archive) as source:
        member = source.getmember(root + "/" + relative)
        require(member.isfile(), "Pinned Bun source member was not a file: " + relative)
        stream = source.extractfile(member)
        require(stream is not None, "Pinned Bun source member could not be read: " + relative)
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_member_text(archive, root, relative):
    regular(archive, "Pinned Bun source archive")
    with tarfile.open(archive) as source:
        member = source.getmember(root + "/" + relative)
        require(member.isfile(), "Pinned Bun source member was not a file: " + relative)
        stream = source.extractfile(member)
        require(stream is not None, "Pinned Bun source member could not be read: " + relative)
        return stream.read().decode()


def replace_source_once(source, before, after):
    require(source.count(before) == 1, "Pinned Bun source did not match its audited low-memory patch")
    return source.replace(before, after)


def prepared_file(workspace, relative):
    result = workspace.joinpath(*relative.split("/"))
    require(result.resolve().is_relative_to(workspace), "Prepared source file escaped its workspace")
    return regular(result, "Prepared source file " + relative)


def validate_prepared_source(workspace, archives, sources, zig_path):
    bun = workspace / sources["sources"][0]["root"]
    require(bun.is_dir() and not bun.is_symlink(), "Prepared Bun source tree is missing")
    bun_archive = archives / sources["sources"][0]["file"]
    for relative in ("bun.lock", "package.json"):
        require(
            digest(prepared_file(bun, relative)) == source_member_digest(bun_archive, sources["sources"][0]["root"], relative),
            "Prepared Bun source unexpectedly changed outside the audited patch set: " + relative,
        )
    tinycc_dependency = prepared_file(bun, "scripts/build/deps/tinycc.ts").read_text()
    version_header = prepared_file(bun, "scripts/build/depVersionsHeader.ts").read_text()
    literal_parser = prepared_file(bun, "vendor/WebKit/Source/JavaScriptCore/runtime/LiteralParser.h").read_text()
    tinycc_header = prepared_file(bun, "vendor/tinycc/tcc.h").read_text()
    tinycc_preprocessor = prepared_file(bun, "vendor/tinycc/tccpp.c").read_text()
    webkit_macros = prepared_file(bun, "vendor/WebKit/Source/cmake/WebKitMacros.cmake").read_text()
    js_buffer = prepared_file(bun, "src/jsc/bindings/JSBuffer.cpp").read_text()
    zig_build = prepared_file(bun, "scripts/build/zig.ts").read_text()
    upstream_zig_build = source_member_text(bun_archive, sources["sources"][0]["root"], "scripts/build/zig.ts")
    expected_zig_build = replace_source_once(
        upstream_zig_build,
        'command: `${stream} ${consoleMode ? "--console" : "--zig-progress"} --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build $step $args`,',
        'command: `${stream} ${consoleMode ? "--console" : "--zig-progress"} --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build -j1 $step $args`,',
    )
    expected_zig_build = replace_source_once(
        expected_zig_build,
        'command: `${stream} --console --stamp=$out --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build $step $args`,',
        'command: `${stream} --console --stamp=$out --env=ZIG_LOCAL_CACHE_DIR=$zig_local_cache --env=ZIG_GLOBAL_CACHE_DIR=$zig_global_cache${parallelSema} $zig build -j1 $step $args`,',
    )
    expected_js_buffer = replace_source_once(
        source_member_text(bun_archive, sources["sources"][0]["root"], "src/jsc/bindings/JSBuffer.cpp"),
        "    &JSC::JSUint8Array::s_info,",
        "    JSC::JSUint8Array::info(),",
    )
    require(
        'kind: "local"' in tinycc_dependency
        and "vendor/tinycc" in tinycc_dependency
        and "export const TINYCC_COMMIT" in tinycc_dependency
        and 'import { TINYCC_COMMIT } from "./deps/tinycc.ts";' in version_header
        and 'dep.name === "tinycc"' in version_header
        and literal_parser.count("Virillio LGPL rebuild probe:") == 3
        and '#if __has_include("config.h")' in tinycc_header
        and "__VIRILLIO_LGPL_REBUILD_PROBE__ 20260906" in tinycc_preprocessor
        and webkit_macros.count('if (NOT _linked_into OR framework STREQUAL _linked_into OR NOT _linked_into IN_LIST ${_target}_FRAMEWORKS)') == 1
        and 'if ((NOT _linked_into) OR (${framework} STREQUAL ${_linked_into}) OR (NOT ${_linked_into} IN_LIST ${_target}_FRAMEWORKS))' not in webkit_macros
        and 'if ((NOT _linked_into) OR ("${framework}" STREQUAL "${_linked_into}") OR (NOT "${_linked_into}" IN_LIST ${_target}_FRAMEWORKS))' not in webkit_macros
        and js_buffer == expected_js_buffer
        and zig_build == expected_zig_build,
        "Prepared source tree did not retain the audited local-library modifications",
    )
    ninja = prepared_file(bun, "build/release-local/build.ninja").read_text()
    require(
        "-Dllvm_codegen_threads=1" in ninja
        and "ZIG_PARALLEL_SEMA=1" in ninja
        and "$zig build -j1 $step $args" in ninja
        and str((zig_path.resolve() / "zig")) in ninja,
        "Build configuration did not retain the constrained verified Zig setup",
    )
    paths = (
        "scripts/build/deps/tinycc.ts",
        "scripts/build/depVersionsHeader.ts",
        "vendor/WebKit/Source/JavaScriptCore/runtime/LiteralParser.h",
        "vendor/WebKit/Source/cmake/WebKitMacros.cmake",
        "src/jsc/bindings/JSBuffer.cpp",
        "vendor/tinycc/tcc.h",
        "vendor/tinycc/tccpp.c",
        "bun.lock",
        "package.json",
        "scripts/build/zig.ts",
        "build/release-local/build.ninja",
    )
    return {path: digest(prepared_file(bun, path)) for path in paths}


def validate_receipt(receipt, runtime_hash, runtime_size, webkit_manifest_hash):
    require(
        receipt.get("bunRevision") == BUN_REVISION
        and receipt.get("libraryProbeApplied") is True
        and receipt.get("webkitCmakeCompatibilityPatchApplied") is True
        and receipt.get("typedArrayClassInfoCompatibilityPatchApplied") is True
        and receipt.get("buildCompleted") is True
        and receipt.get("webkitInput") == "public-checkout"
        and receipt.get("webkitManifestSHA256") == webkit_manifest_hash
        and receipt.get("runtimeSHA256") == runtime_hash
        and receipt.get("runtimeSize") == runtime_size,
        "Rebuild receipt does not bind the final runtime to the verified source build",
    )


def validate_probe(probe):
    require(
        set(probe) == {"bunVersion", "bunRevision", "platform", "arch", "jscMessage", "tinyccValue", "tinyccError", "modifiedJavaScriptCoreVerified", "modifiedTinyCCVerified"}
        and probe.get("bunVersion") == BOOTSTRAP["originalBun"]["version"]
        and probe.get("bunRevision") == BUN_REVISION
        and probe.get("platform") == "darwin"
        and probe.get("arch") == "arm64"
        and isinstance(probe.get("jscMessage"), str)
        and probe["jscMessage"].startswith("Virillio LGPL rebuild probe:")
        and probe.get("tinyccValue") == 20260906
        and probe.get("tinyccError") == ""
        and probe.get("modifiedJavaScriptCoreVerified") is True
        and probe.get("modifiedTinyCCVerified") is True,
        "Runtime probe did not verify both modified library paths",
    )


def hash_recipe(source_dir, workflow):
    files = {file.name for file in source_dir.iterdir() if file.is_file() or file.is_symlink()}
    require(files == EXPECTED_SOURCE_FILES, "Public runtime recipe contains an unexpected source file set")
    workflow_text = regular(workflow, "Workflow").read_text()
    unset_lines = [line.strip() for line in workflow_text.splitlines() if line.strip().startswith("unset ")]
    require(
        workflow_text.count("export CMAKE_BUILD_PARALLEL_LEVEL=1") == 1
        and workflow_text.count("export CARGO_BUILD_JOBS=1") == 1
        and workflow_text.count("--target=bun --target=check -j1") == 1,
        "Public workflow did not retain every required low-memory build constraint",
    )
    require(
        workflow_text.count("export BUN_CONFIG_REGISTRY=https://registry.npmjs.org") == 1
        and len(unset_lines) == 1
        and all(
            token in unset_lines[0]
            for token in (
                "BUN_CONFIG_REGISTRY",
                "NPM_CONFIG_REGISTRY",
                "npm_config_registry",
                "BUN_CONFIG_TOKEN",
                "NPM_CONFIG_TOKEN",
                "npm_config_token",
                "NPM_TOKEN",
                "npm_token",
            )
        ),
        "Public workflow did not retain its public registry and token isolation",
    )
    hashes = {name: digest(source_dir / name) for name in sorted(EXPECTED_SOURCE_FILES)}
    hashes["workflow/rebuild-bun-arm64.yml"] = digest(workflow)
    return hashes


def tool_versions(zig_path):
    return {
        "architecture": command(["uname", "-m"]),
        "macOS": command(["sw_vers", "-productVersion"]),
        "xcode": command(["xcodebuild", "-version"]).splitlines(),
        "cmake": command(["cmake", "--version"]).splitlines()[0],
        "ninja": command(["ninja", "--version"]),
        "perl": command(["perl", "-e", "print $^V"]),
        "rustc": command(["rustc", "--version"]),
        "cargo": command(["cargo", "--version"]),
        "clang": command(["clang", "--version"]).splitlines()[0],
        "zig": command([str(zig_path.resolve() / "zig"), "version"]),
    }


def copy_evidence(source, destination):
    regular(source, "Evidence report")
    shutil.copyfile(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--zig-path", type=Path, required=True)
    parser.add_argument("--node-headers-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--builder-root", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--webkit-report", type=Path, required=True)
    parser.add_argument("--llvm-report", type=Path, required=True)
    parser.add_argument("--original-bun-report", type=Path, required=True)
    parser.add_argument("--zig-report", type=Path, required=True)
    parser.add_argument("--node-headers-report", type=Path, required=True)
    parser.add_argument("--probe-report", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()

    runtime = regular(args.runtime.resolve(), "Rebuilt runtime")
    workspace = args.workspace.resolve()
    archives = args.archives.resolve()
    source_dir = args.source_dir.resolve()
    builder_root = args.builder_root.resolve()
    workflow = regular(args.workflow.resolve(), "Workflow")
    artifact = args.artifact_dir.resolve()
    require(runtime.is_relative_to(workspace), "Rebuilt runtime must remain inside its fresh build workspace")
    require(archives.is_dir() and not archives.is_symlink(), "Verified archive directory is missing")
    require(source_dir.is_dir() and not source_dir.is_symlink(), "Public runtime source directory is missing")
    require(builder_root.is_dir() and not builder_root.is_symlink(), "Public builder checkout is missing")
    require(not artifact.exists() and not artifact.is_symlink(), "Artifact directory must be fresh")

    sources = read_json(source_dir / "sources.json", "Source manifest")
    build_inputs = read_json(source_dir / "build-inputs.json", "Build input manifest")
    webkit_provenance = read_json(source_dir / "webkit-source-provenance.json", "WebKit source manifest")
    require(
        sources.get("bunRevision") == BUN_REVISION
        and sources.get("target") == "aarch64-apple-darwin"
        and build_inputs.get("bunRevision") == BUN_REVISION
        and build_inputs.get("target") == "aarch64-apple-darwin"
        and isinstance(sources.get("sources"), list)
        and len(sources["sources"]) == 3,
        "Public source manifests do not identify the expected ARM64 Bun revision",
    )

    reports = {
        "inputs.json": (args.input_report.resolve(), read_json(args.input_report.resolve(), "Input report")),
        "webkit.json": (args.webkit_report.resolve(), read_json(args.webkit_report.resolve(), "WebKit report")),
        "llvm.json": (args.llvm_report.resolve(), read_json(args.llvm_report.resolve(), "LLVM report")),
        "original-bun.json": (
            args.original_bun_report.resolve(),
            read_json(args.original_bun_report.resolve(), "Original Bun report"),
        ),
        "zig.json": (args.zig_report.resolve(), read_json(args.zig_report.resolve(), "Zig report")),
        "node-headers.json": (
            args.node_headers_report.resolve(),
            read_json(args.node_headers_report.resolve(), "Node header report"),
        ),
        "runtime-probe.json": (args.probe_report.resolve(), read_json(args.probe_report.resolve(), "Runtime probe report")),
    }
    input_summary = validate_inputs(reports["inputs.json"][1], sources, build_inputs)
    webkit_summary = validate_webkit(reports["webkit.json"][1], sources, webkit_provenance)
    validate_bootstrap(
        reports["llvm.json"][1],
        reports["original-bun.json"][1],
        reports["zig.json"][1],
        args.zig_path,
        reports["node-headers.json"][1],
        args.node_headers_dir,
    )
    runtime_hash = digest(runtime)
    runtime_size = runtime.stat().st_size
    receipt_file = workspace / "rebuild-receipt.json"
    receipt = read_json(receipt_file, "Rebuild receipt")
    validate_receipt(receipt, runtime_hash, runtime_size, webkit_summary["contentManifestSHA256"])
    validate_probe(reports["runtime-probe.json"][1])
    patch_hashes = validate_prepared_source(workspace, archives, sources, args.zig_path)
    recipe_hashes = hash_recipe(source_dir, workflow)
    commit = command(["git", "-C", str(builder_root), "rev-parse", "HEAD"])
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "Public builder checkout did not resolve to a commit")
    require(command(["git", "-C", str(builder_root), "status", "--porcelain"]) == "", "Public builder checkout is not clean")

    artifact.mkdir(parents=True)
    provenance_dir = artifact / "provenance"
    provenance_dir.mkdir()
    shutil.copyfile(runtime, artifact / "bun")
    shutil.copyfile(receipt_file, artifact / "rebuild-receipt.json")
    for name, (file, _) in reports.items():
        copy_evidence(file, provenance_dir / name)

    logical = {
        "bun": digest(artifact / "bun"),
        "rebuild-receipt.json": digest(artifact / "rebuild-receipt.json"),
        **{"provenance/" + name: digest(provenance_dir / name) for name in sorted(reports)},
    }
    input_summary["inputReportSHA256"] = logical["provenance/inputs.json"]
    document = {
        "schemaVersion": 1,
        "kind": "virillio-public-bun-arm64-runtime",
        "runtime": {"sha256": runtime_hash, "size": runtime_size, "version": "1.3.14", "revision": BUN_REVISION},
        "source": {
            "bunRevision": BUN_REVISION,
            "target": "aarch64-apple-darwin",
            "publicBuilderRepository": BUILDER_REPOSITORY,
            "publicBuilderCommit": commit,
            "publicWebKit": webkit_summary,
            "publicInputs": input_summary,
            "recipeSHA256": recipe_hashes,
            "preparedPatchFootprintSHA256": patch_hashes,
        },
        "bootstrap": {
            "originalBun": reports["original-bun.json"][1]["executable"],
            "llvm": {"clangVersion": reports["llvm.json"][1]["clangVersion"], "toolSHA256": reports["llvm.json"][1]["toolSHA256"]},
            "zig": {
                "version": reports["zig.json"][1]["version"],
                "sha256": reports["zig.json"][1]["zigSHA256"],
                "size": reports["zig.json"][1]["zigSize"],
            },
            "nodeHeaders": {
                "version": reports["node-headers.json"][1]["version"],
                "nodeHeaderSHA256": reports["node-headers.json"][1]["nodeHeaderSHA256"],
                "nodeHeaderSize": reports["node-headers.json"][1]["nodeHeaderSize"],
            },
        },
        "build": {
            "profile": "release-local",
            "ciConfiguration": True,
            "ninjaTargets": ["bun", "check"],
            "ninjaJobs": 1,
            "zigBuildJobs": 1,
            "cmakeBuildParallelLevel": 1,
            "cargoBuildJobs": 1,
            "webkitCmakeCompatibilityPatchApplied": True,
            "typedArrayClassInfoCompatibilityPatchApplied": True,
            "defaultDsymTargetBuilt": False,
            "codegenBun": reports["original-bun.json"][1]["executable"],
            "toolVersions": tool_versions(args.zig_path),
            "networkInputs": [
                "rustup installed the pinned nightly toolchain and rust-src from public Rust distribution servers",
                "bun install --frozen-lockfile resolved packages declared by the pinned Bun lockfile from public package infrastructure",
            ],
            "hermetic": False,
        },
        "runtimeProbe": reports["runtime-probe.json"][1],
        "logicalArtifactsSHA256": logical,
        "scope": {
            "runtimeBuildVerified": True,
            "applicationRelinked": False,
            "codesigned": False,
            "notarized": False,
            "installerVerified": False,
            "distributionApproved": False,
            "legacyWebKitArchiveByteReproduced": False,
        },
    }
    provenance_file = provenance_dir / "rebuild-provenance.json"
    provenance_file.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    logical["provenance/rebuild-provenance.json"] = digest(provenance_file)
    sums = "".join(f"{logical[name]}  {name}\n" for name in sorted(logical))
    (artifact / "SHA256SUMS").write_text(sums)
    print(json.dumps({"runtimeSHA256": runtime_hash, "artifactFiles": len(logical), "builderCommit": commit}))


if __name__ == "__main__":
    main()
