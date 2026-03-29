# Hash Contract

These values are protocol-level identifiers shared by the Python and Node implementations.

- `user_hash = sha256("${username}|${salt}")`
- `source_host_hash = sha256("${username}|${source_label}|${salt}")`
- `row_key = sha256("${user_hash}|${source_host_hash}|${date_local}|${tool}|${identity}")`
- `identity = trim(session_fingerprint)` when present, otherwise `model:${model}`

All payload strings must be encoded as UTF-8 and emitted as lowercase hexadecimal digests.
