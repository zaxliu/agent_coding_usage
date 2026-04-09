# Remote Output-Budget Pagination

## Goal

Stabilize remote collection on restrictive bastion-host SSH links that truncate large stdout payloads by splitting remote collection into multiple pages, each capped to a conservative stdout budget.

Primary target flow:

- remote collection still runs over SSH only
- stdout and stderr remain the only transport channels
- the remote host may truncate stdout around 1 MB total
- the collector must finish a large 30-day collection by issuing multiple smaller remote calls instead of one oversized response

## Scope

This change applies to the Python CLI implementation under `src/llm_usage`.

In scope:

- add paginated remote collection for `RemoteFileCollector`
- keep the existing chunked stdout protocol for each individual page
- cap each remote response to an output byte budget smaller than the observed bastion-host truncation threshold
- add remote cursor/resume support so the local side can continue from the middle of a file batch
- merge events and warnings from multiple remote pages into one local `CollectOutput`

Out of scope:

- Node CLI parity
- changing the transport away from SSH stdout/stderr
- remote Cursor collection
- probe pagination
- exact adaptive auto-tuning of budget size based on prior runs

## Design

### Pagination trigger

The current chunked stdout protocol fixes single-line JSON framing, but it still fails when the overall stdout stream is truncated by the bastion host. The solution is to limit how much payload the remote script emits in one run.

The remote collect script should stop building the current page before the encoded result becomes too large.

Use a conservative default page budget:

- `REMOTE_STDOUT_PAGE_BUDGET_BYTES = 600 * 1024`

This is a budget for the raw JSON result payload before chunk framing overhead. The actual transmitted stdout will be larger because:

- JSON is base64-encoded for chunk transport
- each chunk has protocol header text
- SSH/bastion layers may add incidental stdout noise

The budget should therefore stay well below the observed truncation point.

### Cursor model

Each remote page returns:

- `events`
- `warnings`
- `next_cursor`

`next_cursor` is either `null` when the collection is complete, or a dict with enough information to resume scanning deterministically:

- `job_index`: which remote job in `jobs` is active
- `pattern_index`: which glob pattern within that job is active
- `file_index`: which expanded file within the current pattern is active
- `line_index`: for `.jsonl` files, the next 0-based line index to read
- `done`: optional boolean only for internal script flow; not required if `null` means complete

This cursor must be opaque to users but stable between page calls during one collection run.

### Remote paging algorithm

The remote script already walks jobs, patterns, files, and JSON/JSONL records in a deterministic order. Pagination should preserve that order.

High-level flow per page:

1. Start from either the beginning or the provided cursor.
2. Expand files in the same deterministic order already used by the collector.
3. Parse records exactly as today.
4. Before appending each event, estimate the next page size if that event were included.
5. If appending the next event would exceed the page budget:
   - return the current page immediately
   - include a `next_cursor` pointing to the same source location so the next call resumes correctly
6. If the scan completes without hitting the budget:
   - return the final page with `next_cursor = null`

Warnings should be emitted once, in the page where they are discovered. They do not need cross-page deduplication beyond preserving current scan semantics.

### Output size estimation

The remote script should estimate page size using the actual JSON event dicts that will be returned, not approximate token counts or file sizes.

Recommended approach:

- maintain `events` as today
- maintain a running `estimated_payload_bytes`
- for each candidate event, compute `len(json.dumps(event, separators=(",", ":")).encode("utf-8"))`
- include a small fixed allowance for commas, list brackets, wrapper keys, warnings, and `next_cursor`

This keeps the logic simple and conservative. Exact byte perfection is unnecessary; staying below the limit is what matters.

### Local collection loop

`RemoteFileCollector.collect()` should no longer assume one remote collect call is enough.

Instead:

1. start with `cursor = None`
2. call the remote collect script
3. decode one page using the existing chunked stdout decoder
4. append page events to the aggregate list
5. append page warnings to the aggregate warning list
6. if `next_cursor` is not null, call the remote script again with that cursor
7. stop when `next_cursor` is null

This loop should keep existing filtering behavior:

- local `start/end` window filtering remains correct
- `source_host_hash` stays attached as today

### Payload contract

The remote collect page payload shape becomes:

```json
{
  "events": [...],
  "warnings": [...],
  "next_cursor": null
}
```

or:

```json
{
  "events": [...],
  "warnings": [...],
  "next_cursor": {
    "job_index": 0,
    "pattern_index": 1,
    "file_index": 17,
    "line_index": 243
  }
}
```

The local side must validate:

- `events` is a list
- `warnings` is a list if present
- `next_cursor` is either `null` or a dict with integer fields

Invalid cursor payloads should surface a clear remote pagination warning.

### Compatibility and rollout

This design changes only the Python remote collection path.

Compatibility rules:

- keep the chunked stdout decoder unchanged as the transport for each page
- keep legacy direct JSON extraction fallback for older or non-paginated outputs where the chunk prefix is absent
- do not attempt mixed pagination compatibility with older pagers; pagination is an internal client/script contract within the same code version

### Failure handling

If a page returns chunked corruption:

- fail that collection with the current explicit chunked error message
- keep stdout/stderr debug previews for diagnosis

If a page returns an invalid cursor:

- fail with a clear warning such as `remote pagination returned invalid cursor`

If the same cursor repeats without progress:

- fail fast with a clear warning such as `remote pagination cursor did not advance`

This avoids infinite loops on broken remote scripts or corrupted responses.

## Testing

Add or update tests for:

- remote page payload containing `next_cursor = null` on completion
- multi-page collection where page 1 returns a cursor and page 2 completes
- JSONL resume from a non-zero `line_index`
- page-size budgeting stopping before oversized stdout is produced
- invalid cursor payload handling
- repeated cursor / no-progress protection
- preservation of existing direct JSON compatibility when chunk pagination is not involved

## Recommendation

Implement this as a minimal pagination layer on top of the newly added chunked stdout transport.

Do not redesign remote parsing again. The current parser and chunked transport are good enough; the missing piece is limiting each remote response to a bastion-safe size and looping locally until all pages are collected.
