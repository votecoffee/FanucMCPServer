# Exact captured and proposed bytes

Use `read_captured_file` with a retained read's robot/path/hash for exact base64
byte ranges when local files are inaccessible. Reassemble and verify the full
hash before editing. `stage_file` saves up to 1 MiB of proposed exact bytes with
a required SHA-256 and returns a generated `source_file` for preparation. Preserve
encoding and line endings; neither tool contacts or writes a controller.
