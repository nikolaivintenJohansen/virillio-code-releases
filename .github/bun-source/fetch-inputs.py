#!/usr/bin/env python3
"""Fetch the public, SHA-pinned inputs for the Bun replacement build."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import urllib.parse
import urllib.request


CHUNK_SIZE = 1024 * 1024


def destination_for(root, item):
    relative = PurePosixPath(item["file"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Unsafe input archive path: " + item["file"])
    destination = root.joinpath(*relative.parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("Input archive escapes the selected directory")
    return destination


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(file, item):
    return file.is_file() and not file.is_symlink() and file.stat().st_size == item["size"] and digest(file) == item["sha256"]


def download(file, item):
    parsed = urllib.parse.urlparse(item["url"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Public input URLs must use HTTPS")
    allowed_hosts = {parsed.hostname}
    if parsed.hostname in {"github.com", "codeload.github.com"}:
        allowed_hosts.update({"github.com", "codeload.github.com"})
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name("." + file.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Partial input already exists: " + temporary.name)
    request = urllib.request.Request(item["url"], headers={"User-Agent": "virillio-public-runtime-builder/1"})
    written = 0
    hasher = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as output:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in allowed_hosts:
                raise ValueError("Input redirected to an unexpected host")
            while data := response.read(CHUNK_SIZE):
                written += len(data)
                hasher.update(data)
                output.write(data)
        if written != item["size"] or hasher.hexdigest() != item["sha256"]:
            raise ValueError("Downloaded input did not match its pinned hash or size: " + item["file"])
        os.replace(temporary, file)
        return final.hostname
    finally:
        temporary.unlink(missing_ok=True)


def selected_items(sources, inputs):
    selected = []
    skipped = []
    for item in sources["sources"]:
        if "url" in item:
            selected.append(item)
        else:
            skipped.append({"name": item["name"], "reason": "verified separately from its public git checkout"})
    for key in ("dependencyArchives", "rustCrates", "standardLibrarySources", "standardLibraryCrates"):
        selected.extend(inputs.get(key, []))
    seen = {}
    for item in selected:
        file = item["file"]
        identity = (item["url"], item["size"], item["sha256"])
        if file in seen and seen[file] != identity:
            raise ValueError("Conflicting public input identity: " + file)
        seen[file] = identity
    return selected, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--build-inputs", type=Path, default=Path(__file__).with_name("build-inputs.json"))
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text())
    inputs = json.loads(args.build_inputs.read_text())
    if sources.get("bunRevision") != inputs.get("bunRevision"):
        raise ValueError("Public source manifests disagree about the Bun revision")
    root = args.archives.resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected, skipped = selected_items(sources, inputs)
    results = []
    for item in selected:
        file = destination_for(root, item)
        reused = file.exists()
        if reused and not verify(file, item):
            raise ValueError("Existing input does not match its pinned hash or size: " + item["file"])
        if not reused:
            final_host = download(file, item)
        if reused:
            final_host = None
        results.append({"file": item["file"], "url": item["url"], "finalHost": final_host, "size": item["size"], "sha256": item["sha256"], "reused": reused})
    report = {
        "schemaVersion": 1,
        "bunRevision": sources["bunRevision"],
        "fetched": results,
        "skipped": skipped,
        "allVerified": all(verify(destination_for(root, item), item) for item in selected),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verifiedInputs": len(results), "skipped": len(skipped)}))


if __name__ == "__main__":
    main()
