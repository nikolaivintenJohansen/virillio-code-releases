#!/usr/bin/env python3
"""Fetch the exact original Bun executable used for source code generation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request
import zipfile


ARCHIVE = "bun-darwin-aarch64.zip"
URL = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/" + ARCHIVE
ARCHIVE_SHA256 = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
ARCHIVE_SIZE = 23586433
BINARY = "bun-darwin-aarch64/bun"
BINARY_SHA256 = "e0c90ec15d33363e6b70713d56bc3b2c7585c17f40a0fe0f8fd9305901d4e233"
BINARY_SIZE = 63096576
CHUNK_SIZE = 1024 * 1024


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(file):
    temporary = file.with_name("." + file.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Original Bun partial download already exists")
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
                raise ValueError("Original Bun archive redirected to an unexpected host")
            while data := response.read(CHUNK_SIZE):
                written += len(data)
                hasher.update(data)
                output.write(data)
        if written != ARCHIVE_SIZE or hasher.hexdigest() != ARCHIVE_SHA256:
            raise ValueError("Original Bun archive did not match its pinned hash or size")
        os.replace(temporary, file)
        return host
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    download_dir = args.download_dir.resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    archive = download_dir / ARCHIVE
    final_host = None
    if archive.exists() and (archive.is_symlink() or archive.stat().st_size != ARCHIVE_SIZE or digest(archive) != ARCHIVE_SHA256):
        raise ValueError("Existing original Bun archive did not match its pinned hash or size")
    if not archive.exists():
        final_host = download(archive)
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a fresh original Bun output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        names = sorted(info.filename for info in source.infolist())
        if names != ["bun-darwin-aarch64/", BINARY]:
            raise ValueError("Original Bun archive contained an unexpected layout")
        info = source.getinfo(BINARY)
        if info.file_size != BINARY_SIZE:
            raise ValueError("Original Bun binary size did not match the pinned value")
        hasher = hashlib.sha256()
        with source.open(info) as input, output.open("xb") as destination:
            while data := input.read(CHUNK_SIZE):
                hasher.update(data)
                destination.write(data)
    output.chmod(0o755)
    archive.unlink()
    if output.stat().st_size != BINARY_SIZE or hasher.hexdigest() != BINARY_SHA256:
        raise ValueError("Original Bun executable did not match its pinned hash or size")
    version = subprocess.check_output([str(output), "--version"], text=True).strip()
    revision = subprocess.check_output([str(output), "--revision"], text=True).strip()
    if version != "1.3.14" or revision != "1.3.14+0d9b296af":
        raise ValueError("Original Bun executable did not identify as the pinned release")
    report = {
        "schemaVersion": 1,
        "archive": {"url": URL, "finalHost": final_host, "size": ARCHIVE_SIZE, "sha256": ARCHIVE_SHA256},
        "executable": {"size": BINARY_SIZE, "sha256": BINARY_SHA256, "version": version, "revision": revision},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["executable"]))


if __name__ == "__main__":
    main()
