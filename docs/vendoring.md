# Protocol provenance

The authoritative UNO engine/contract lives in `UNO/src/triad_uno`. SDK and
benchmark images carry independent copies of protocol.py and JSON schemas;
participants never need the trusted engine installed. `VENDOR.json` records
normalized UTF-8 SHA-256 per source file for event-runtime-v2. Hashes identify
reviewable working-tree changes without inventing a Git commit SHA.

From the outer workspace run `python UNO/tools/sync_sdk.py`, then
`python UNO/tools/sync_sdk.py --check` and the SDK compatibility tests. The check
compares the actual authoritative files, copies and recorded hashes. A historical
Git-based vendor manifest is still understood by the compatibility test.
Upstream added a separate `local_evaluator/VENDOR.json` and
`tools/vendor_local_evaluator.py` during release integration. That snapshot records
the actual merged organizer commit and source/bundle hashes; both SDK and benchmark
snapshots are refreshed from the same clean commit. See local-testing.md for refresh
and verification commands. This snapshot remains outside participant Docker images.
Snapshot paths use forward slashes and hashes use canonical UTF-8/LF text;
Git attributes keep bundled files at LF on Windows as well as Linux.
Record released organizer and SDK Git commits alongside image digests.
