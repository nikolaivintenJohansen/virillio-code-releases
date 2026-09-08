# Public Bun runtime rebuild

This directory contains the public, source-only recipe used by the
rebuild-bun-arm64 workflow. It rebuilds the ARM64 Bun 1.3.14 runtime from
pinned public inputs and records evidence for the two library-source probe
changes.

The workflow intentionally contains no Virillio application source, signing
certificate, notarization credential, release token, or installer. It runs
only after a manual dispatch on GitHub's standard public macOS ARM64 runner.
Every archive fetched by these helpers is checked against a pinned SHA-256 and
size before it is used. The public WebKit source is checked out at a pinned
commit, restricted to the documented source roots, and recorded as a content
manifest. It is not claimed to reproduce the legacy filtered WebKit archive
byte for byte.

The recipe applies one recorded build-only WebKit CMake compatibility patch:
it quotes a possibly empty framework-link property so current CMake can parse
the source's intended condition. The public input manifest records the
unmodified checkout and the rebuild provenance records the transformed file.

The output artifact is a replacement-runtime candidate with its receipt,
probe report, source-input reports, provenance, and checksums. It is not a
Virillio Code installer. Application relinking, signing, notarization,
installer verification, and release approval remain separate steps.

The build uses a locked Bun install and a date-pinned Rust toolchain. Those
public package and toolchain downloads are disclosed in the provenance, so
the workflow does not claim a fully offline or hermetic build.

The helper scripts are provided under the MIT License below. Bun, WebKit,
TinyCC, Node.js, LLVM, Zig, Rust, and all other dependencies remain subject
to their own licenses.
