#!/usr/bin/env python3
"""Fetch SHA-pinned Node.js headers so Bun never performs an unchecked fetch."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.parse
import urllib.request


VERSION = "24.3.0"
ARCHIVE = f"node-v{VERSION}-headers.tar.gz"
URL = f"https://nodejs.org/dist/v{VERSION}/{ARCHIVE}"
SHA256 = "045e9bf477cd5db0ec67f8c1a63ba7f784dedfe2c581e3d0ed09b88e9115dd07"
SIZE = 8747815
ROOT = f"node-v{VERSION}"
REMOVED = ("include/node/openssl", "include/node/uv", "include/node/uv.h")
CHUNK_SIZE = 1024 * 1024


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(file):
    temporary = file.with_name("." + file.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Node header partial download already exists")
    written = 0
    hasher = hashlib.sha256()
    request = urllib.request.Request(URL, headers={"User-Agent": "virillio-public-runtime-builder/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as output:
            parsed = urllib.parse.urlparse(response.geturl())
            if parsed.scheme != "https" or parsed.hostname != "nodejs.org":
                raise ValueError("Node header archive redirected to an unexpected host")
            while data := response.read(CHUNK_SIZE):
                written += len(data)
                hasher.update(data)
                output.write(data)
        if written != SIZE or hasher.hexdigest() != SHA256:
            raise ValueError("Node header archive did not match its pinned hash or size")
        os.replace(temporary, file)
        return parsed.hostname
    finally:
        temporary.unlink(missing_ok=True)


def extract(archive, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a fresh Node header destination directory")
    staging = destination.with_name("." + destination.name + ".staging")
    if staging.exists() or staging.is_symlink():
        raise ValueError("Node header staging directory already exists")
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive) as source:
            members = source.getmembers()
            if not members:
                raise ValueError("Node header archive was empty")
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or path.parts[0] != ROOT:
                    raise ValueError("Node header archive contained an unsafe or unexpected path")
                if not member.isdir() and not member.isfile():
                    raise ValueError("Node header archive contained an unsupported filesystem entry")
            source.extractall(staging, filter="data")
        extracted = staging / ROOT
        if not extracted.is_dir() or extracted.is_symlink() or len(list(staging.iterdir())) != 1:
            raise ValueError("Node header archive did not have the expected single root")
        for relative in REMOVED:
            target = extracted.joinpath(*relative.split("/"))
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        required = extracted / "include/node/node.h"
        if not required.is_file() or required.is_symlink():
            raise ValueError("Node header archive did not contain node.h")
        (extracted / ".identity").write_text(VERSION + "\n")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(extracted, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
        raise ValueError("Existing Node header archive did not match its pinned hash or size")
    if not archive.exists():
        final_host = download(archive)
    destination = args.destination.resolve()
    extract(archive, destination)
    archive.unlink()
    identity = destination / ".identity"
    node_header = destination / "include/node/node.h"
    if identity.read_text() != VERSION + "\n" or any((destination / path).exists() for path in REMOVED):
        raise ValueError("Prepared Node header cache did not match Bun's expected layout")
    report = {
        "schemaVersion": 1,
        "archive": {"url": URL, "finalHost": final_host, "size": SIZE, "sha256": SHA256},
        "version": VERSION,
        "identity": identity.read_text(),
        "removed": list(REMOVED),
        "nodeHeaderSHA256": digest(node_header),
        "nodeHeaderSize": node_header.stat().st_size,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"version": VERSION, "nodeHeaderSHA256": report["nodeHeaderSHA256"]}))


if __name__ == "__main__":
    main()
