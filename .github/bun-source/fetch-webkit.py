#!/usr/bin/env python3
"""Check out the pinned public WebKit source subset and record its content."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


def command(arguments, directory=None):
    result = subprocess.run(arguments, cwd=directory, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError("Command failed: " + " ".join(arguments) + "\n" + result.stderr[-2000:])
    return result.stdout.strip()


def file_hash(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record_tree(directory, allowed):
    files = []

    def visit(current):
        for item in sorted(current.iterdir()):
            relative = item.relative_to(directory)
            if relative.parts[0] == ".git":
                continue
            if relative.parts[0] not in allowed:
                raise ValueError("WebKit checkout included an unallowlisted path: " + relative.as_posix())
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                files.append({"path": relative.as_posix(), "mode": stat.S_IMODE(info.st_mode), "kind": "symlink", "target": os.readlink(item)})
                continue
            if stat.S_ISDIR(info.st_mode):
                visit(item)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("WebKit checkout contained an unsupported filesystem entry")
            files.append({"path": relative.as_posix(), "mode": stat.S_IMODE(info.st_mode), "kind": "file", "sha256": file_hash(item), "size": info.st_size})

    visit(directory)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--provenance", type=Path, default=Path(__file__).with_name("webkit-source-provenance.json"))
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text())
    source = sources["sources"][1]
    provenance = json.loads(args.provenance.read_text())
    if source.get("name") != "WebKit / JavaScriptCore" or source.get("revision") != provenance.get("revision"):
        raise ValueError("WebKit source manifests disagree")
    repository = source.get("repository")
    revision = provenance["revision"]
    if repository != "https://github.com/oven-sh/WebKit" or len(revision) != 40:
        raise ValueError("Unexpected WebKit public source identity")
    checkout = args.checkout.resolve()
    if checkout.exists() or checkout.is_symlink():
        raise ValueError("Choose a fresh WebKit checkout directory")
    checkout.mkdir(parents=True)
    roots = provenance["includedRoots"]
    if any(not isinstance(root, str) or not root or "/" in root or root in (".", "..") for root in roots):
        raise ValueError("WebKit include roots must be top-level names")
    patterns = ["/" + root for root in roots]
    command(["git", "init", "--quiet", str(checkout)])
    command(["git", "-C", str(checkout), "remote", "add", "origin", repository])
    command(["git", "-C", str(checkout), "sparse-checkout", "init", "--no-cone"])
    command(["git", "-C", str(checkout), "sparse-checkout", "set", "--no-cone", *patterns])
    command(["git", "-C", str(checkout), "-c", "protocol.version=2", "fetch", "--depth=1", "--filter=blob:none", "origin", revision])
    command(["git", "-C", str(checkout), "-c", "advice.detachedHead=false", "checkout", "--detach", "FETCH_HEAD"])
    actual_revision = command(["git", "-C", str(checkout), "rev-parse", "HEAD"])
    if actual_revision != revision:
        raise ValueError("Public WebKit checkout did not resolve to the pinned revision")
    if command(["git", "-C", str(checkout), "status", "--porcelain"]):
        raise ValueError("Public WebKit checkout is not clean")
    command(["git", "-C", str(checkout), "diff", "--quiet"])
    command(["git", "-C", str(checkout), "diff", "--cached", "--quiet"])
    allowed = set(roots)
    for root in provenance["excludedRoots"]:
        if root != ".git" and (checkout / root).exists():
            raise ValueError("Public WebKit checkout included an excluded root: " + root)
    files = record_tree(checkout, allowed)
    payload = {
        "schemaVersion": 1,
        "repository": repository,
        "revision": actual_revision,
        "gitTree": command(["git", "-C", str(checkout), "rev-parse", "HEAD^{tree}"]),
        "includedRoots": roots,
        "excludedRoots": provenance["excludedRoots"],
        "files": files,
        "legacyArchive": {"size": source["size"], "sha256": source["sha256"], "reproduced": False},
    }
    payload["manifestSHA256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"revision": actual_revision, "files": len(files), "treeManifestSHA256": payload["manifestSHA256"]}))


if __name__ == "__main__":
    main()
