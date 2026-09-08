#!/usr/bin/env python3
"""Fetch and reduce the pinned public LLVM toolchain for the ARM build."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile
import urllib.parse
import urllib.request


ARCHIVE = "LLVM-21.1.8-macOS-ARM64.tar.xz"
URL = "https://github.com/llvm/llvm-project/releases/download/llvmorg-21.1.8/" + ARCHIVE
SHA256 = "b95bdd32a33a81ee4d40363aaeb26728a26783fcef26a4d80f65457433ea4669"
SIZE = 1503047352
ROOT = "LLVM-21.1.8-macOS-ARM64"
CHUNK_SIZE = 1024 * 1024
BINARIES = ("clang-21", "llvm-ar", "llvm-objcopy", "dsymutil")
LINKS = {"clang": "clang-21", "clang++": "clang", "llvm-ranlib": "llvm-ar", "llvm-strip": "llvm-objcopy"}


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(file):
    temporary = file.with_name("." + file.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("LLVM partial download already exists")
    written = 0
    hasher = hashlib.sha256()
    request = urllib.request.Request(URL, headers={"User-Agent": "virillio-public-runtime-builder/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as output:
            parsed = urllib.parse.urlparse(response.geturl())
            host = parsed.hostname
            if parsed.scheme != "https" or host is None or not (
                host == "github.com" or host.endswith(".githubusercontent.com")
            ):
                raise ValueError("LLVM archive redirected to an unexpected host")
            while data := response.read(CHUNK_SIZE):
                written += len(data)
                hasher.update(data)
                output.write(data)
        if written != SIZE or hasher.hexdigest() != SHA256:
            raise ValueError("LLVM download did not match its published hash or size")
        os.replace(temporary, file)
        return host
    finally:
        temporary.unlink(missing_ok=True)


def destination(root, member):
    name = PurePosixPath(member.name)
    if name.is_absolute() or name.parts[0] != ROOT or ".." in name.parts:
        raise ValueError("Unexpected LLVM archive member")
    relative = PurePosixPath(*name.parts[1:])
    result = root.joinpath(*relative.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("LLVM archive member escapes its destination")
    return result


def extract(archive, root):
    root.mkdir(parents=True)
    files = set()
    resource_prefix = ROOT + "/lib/clang/21/"
    wanted = {ROOT + "/bin/" + name for name in BINARIES}
    with tarfile.open(archive, mode="r|xz") as source:
        for member in source:
            if member.name not in wanted and not member.name.startswith(resource_prefix):
                continue
            target = destination(root, member)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isreg():
                raise ValueError("Pinned LLVM subset contained an unsupported non-file entry")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("Pinned LLVM archive member could not be read")
            with target.open("xb") as output:
                while data := stream.read(CHUNK_SIZE):
                    output.write(data)
            target.chmod(stat.S_IMODE(member.mode))
            files.add(target.relative_to(root).as_posix())
    required = {"bin/" + name for name in BINARIES}
    if not required.issubset(files):
        raise ValueError("Pinned LLVM archive did not contain every required tool")
    for name, target in LINKS.items():
        link = root / "bin" / name
        link.symlink_to(target)
    return files


def output(arguments):
    return subprocess.check_output(arguments, text=True, stderr=subprocess.STDOUT).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    download_dir = args.download_dir.resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    archive = download_dir / ARCHIVE
    final_host = None
    if archive.exists() and (archive.is_symlink() or archive.stat().st_size != SIZE or digest(archive) != SHA256):
        raise ValueError("Existing LLVM download did not match its pinned hash or size")
    if not archive.exists():
        final_host = download(archive)
    destination_root = args.destination.resolve()
    if destination_root.exists() or destination_root.is_symlink():
        raise ValueError("Choose a fresh LLVM destination directory")
    toolchain = destination_root / ROOT
    files = extract(archive, toolchain)
    archive.unlink()
    clang = toolchain / "bin/clang"
    version = output([str(clang), "--version"])
    if "clang version 21.1.8" not in version:
        raise ValueError("Extracted LLVM compiler did not report version 21.1.8")
    for name in ("clang", "clang++", "llvm-ar", "llvm-ranlib", "llvm-strip", "dsymutil"):
        executable = toolchain / "bin" / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("LLVM tool is missing or non-executable: " + name)
    report = {
        "schemaVersion": 1,
        "archive": {"url": URL, "finalHost": final_host, "size": SIZE, "sha256": SHA256},
        "llvmRoot": ROOT,
        "extractedFiles": len(files),
        "clangVersion": version.splitlines()[0],
        "toolSHA256": {name: digest(toolchain / "bin" / name) for name in BINARIES},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"extractedFiles": len(files), "clang": report["clangVersion"]}))


if __name__ == "__main__":
    main()
