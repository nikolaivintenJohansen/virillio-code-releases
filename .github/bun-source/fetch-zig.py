#!/usr/bin/env python3
"""Fetch the SHA-pinned Zig compiler required by the public Bun rebuild."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import urllib.parse
import urllib.request
import zipfile


ARCHIVE = "bootstrap-aarch64-macos-none-ReleaseSafe.zip"
URL = "https://github.com/oven-sh/zig/releases/download/autobuild-04e7f6ac1e009525bc00934f20199c68f04e0a24/" + ARCHIVE
SHA256 = "b4eaff25ff3b665a48629c4f08ac8bc5f7da8d925307aec8bf43d2cd77355882"
SIZE = 97479085
ROOT = "bootstrap-aarch64-macos-none"
CHUNK_SIZE = 1024 * 1024


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(file):
    temporary = file.with_name("." + file.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Zig partial download already exists")
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
                raise ValueError("Zig archive redirected to an unexpected host")
            while data := response.read(CHUNK_SIZE):
                written += len(data)
                hasher.update(data)
                output.write(data)
        if written != SIZE or hasher.hexdigest() != SHA256:
            raise ValueError("Zig archive did not match its pinned hash or size")
        os.replace(temporary, file)
        return host
    finally:
        temporary.unlink(missing_ok=True)


def extract(archive, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a fresh Zig destination directory")
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if not names or any(
            PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or not name.startswith(ROOT + "/")
            for name in names
        ):
            raise ValueError("Zig archive contained an unsafe or unexpected path")
        for item in source.infolist():
            relative = PurePosixPath(item.filename).relative_to(ROOT)
            target = destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Zig archive member escapes its destination")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as input, target.open("xb") as output:
                while data := input.read(CHUNK_SIZE):
                    output.write(data)
            target.chmod((item.external_attr >> 16) & 0o777 or 0o644)


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
        raise ValueError("Existing Zig archive did not match its pinned hash or size")
    if not archive.exists():
        final_host = download(archive)
    destination = args.destination.resolve()
    extract(archive, destination)
    archive.unlink()
    executable = destination / "zig"
    if not executable.is_file() or not os.access(executable, os.X_OK) or not (destination / "lib").is_dir():
        raise ValueError("Extracted Zig compiler is incomplete")
    version = subprocess.check_output([str(executable), "version"], text=True).strip()
    if version != "0.15.2":
        raise ValueError("Extracted Zig compiler did not report the expected version")
    report = {
        "schemaVersion": 1,
        "archive": {"url": URL, "finalHost": final_host, "size": SIZE, "sha256": SHA256},
        "version": version,
        "zigSHA256": digest(executable),
        "zigSize": executable.stat().st_size,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"version": version, "zigSHA256": report["zigSHA256"]}))


if __name__ == "__main__":
    main()
