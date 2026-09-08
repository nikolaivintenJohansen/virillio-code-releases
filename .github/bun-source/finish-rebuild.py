#!/usr/bin/env python3
"""Bind the prepared rebuild receipt to the freshly built runtime."""

import argparse
import hashlib
import json
from pathlib import Path


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    runtime = args.runtime.resolve()
    if runtime.is_symlink() or not runtime.is_file() or not runtime.is_relative_to(workspace):
        raise ValueError("Built runtime must be a regular file inside the selected workspace")
    receipt_file = workspace / "rebuild-receipt.json"
    receipt = json.loads(receipt_file.read_text())
    if receipt.get("buildCompleted") not in (False, True) or receipt.get("libraryProbeApplied") is not True:
        raise ValueError("Rebuild receipt was not prepared for the library probe")
    runtime_hash = digest(runtime)
    runtime_size = runtime.stat().st_size
    if receipt.get("buildCompleted") is True and (
        receipt.get("runtimeSHA256") != runtime_hash or receipt.get("runtimeSize") not in (None, runtime_size)
    ):
        raise ValueError("Completed rebuild receipt does not match the selected runtime")
    receipt.update({"buildCompleted": True, "runtimeSHA256": runtime_hash, "runtimeSize": runtime_size})
    receipt_file.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"runtimeSHA256": receipt["runtimeSHA256"], "runtimeSize": receipt["runtimeSize"]}))


if __name__ == "__main__":
    main()
