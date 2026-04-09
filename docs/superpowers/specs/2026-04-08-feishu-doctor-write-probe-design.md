# Feishu Doctor Write Probe

## Summary

The current `feishu doctor` flow can verify:

- authentication
- target resolution
- table field readability

It cannot reliably verify whether the configured app or bot token can actually write records to the target Bitable table.

An earlier attempt used the document sharing setting (`link_share_entity`) as a proxy for write access. That is not correct, because link-sharing policy and API write capability are separate concerns. The result is false warnings when sync works but doctor still claims permissions are wrong.

This change replaces that inference with a real write probe.

## Goal

Make `feishu doctor` verify actual API writeability by performing the smallest possible write operation against the configured table and then deleting the probe record immediately.

## Non-Goals

- changing normal sync behavior
- enforcing document sharing policy
- introducing a new persistent doctor metadata table
- changing Feishu target selection rules

## Recommended Approach

### Option A: Create a minimal probe record, then delete it

Use the same authenticated client family already used for Feishu sync. During doctor:

1. create a single lightweight probe record
2. capture the returned `record_id`
3. delete that record immediately

Pros:

- validates real write access
- validates real delete access for cleanup
- avoids false positives from unrelated sharing settings
- stays close to the real sync path

Cons:

- performs a short-lived write in the business table
- requires careful cleanup and clear probe record naming

### Option B: Create a probe record without deleting it

Pros:

- simplest implementation

Cons:

- pollutes customer data
- makes doctor non-idempotent from a data perspective

### Option C: Continue using metadata-only permission checks

Pros:

- no table writes

Cons:

- repeats the original category error
- still cannot prove real API writeability

Recommendation: Option A.

## Design

### Probe Record Shape

The probe should use a minimal payload that matches required writable table columns with the least side effects possible.

Rules:

- include only fields already expected by the existing upload contract
- use a clearly synthetic `row_key` prefix such as `__llm_usage_doctor_probe__`
- use a current timestamp so concurrent doctor runs do not collide
- keep other field values simple and recognizable as probe data

The probe record should be easy to identify if cleanup ever fails.

### Client Behavior

Add an explicit write-probe method in the Feishu client layer rather than open-coding raw requests inside `doctor`.

Rules:

- create one record with the minimal probe fields
- require the API to return a `record_id`
- delete the same record immediately
- raise a precise error if create fails
- raise a precise error if delete fails after create succeeds

This keeps the logic testable and reusable from both Python and Node implementations if parity is needed later.

### Doctor Output

`feishu doctor` should report writeability based on the probe result.

Rules:

- if field inspection passes and probe create/delete succeeds, doctor reports success
- if create fails with a Feishu permission error, doctor reports write permission failure
- if delete fails after create succeeds, doctor reports cleanup failure with the probe identifier
- doctor must not use `link_share_entity` as a writeability verdict

### Failure Handling

The failure modes need to be distinct.

Cases:

- authentication failure: existing auth error path
- schema/field readability failure: existing schema path
- write probe create failure: report that API write access is missing or blocked
- write probe delete failure: report partial success plus cleanup warning

Delete failure is important because it leaves an identifiable probe record behind.

## Testing

### Automated

Add tests for:

- successful create and delete probe path
- create failure surfaces as doctor write probe failure
- delete failure surfaces as cleanup failure
- doctor no longer warns based on `link_share_entity`

### Manual

Run `feishu doctor` against:

- a target with valid write access
- a target with readable but non-writable credentials if available

Confirm that success and failure messages match the real API outcome, not the sharing-link configuration.

## Acceptance Criteria

- `feishu doctor` uses a real create-then-delete probe to determine writeability
- doctor no longer treats `link_share_entity` as the source of truth for write permission
- a successful doctor run leaves no persistent probe record behind
- failures differentiate between create failure and delete cleanup failure
