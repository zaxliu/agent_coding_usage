# Remote SSH Password Collection via sshpass

## Goal

Stabilize remote collection for bastion-host login flows that require password authentication and do not support public keys, while preserving the current SSH transport and remote parsing flow.

Primary target flow:

- destination format like `user@user@dev_server@blj.horizon.cc`
- non-standard SSH port such as `2222`
- password authentication through `sshpass`

## Scope

This change applies to the Python CLI implementation under `src/llm_usage`.

In scope:

- enable remote probe and collection through `sshpass -e`
- support password sources from existing `SSHPASS` environment variable
- support interactive password prompt during remote selection when running in interactive mode
- ensure prompted passwords are injected only for the current process execution
- keep existing remote selection, SSH transport fallback, and collector behavior unchanged when password mode is not enabled

Out of scope:

- Node CLI parity
- `SSH_ASKPASS`, `expect`, or `plink`
- storing passwords in `.env`, runtime state, or reports

## Design

### Remote config model

Add a boolean remote setting:

- `REMOTE_<ALIAS>_USE_SSHPASS=1`

This flag means the remote should be launched through `sshpass -e ssh ...`.

The config stores only whether password mode is required. It never stores the password itself.

`RemoteHostConfig` gains a `use_sshpass: bool` field. Temporary remotes created through interaction can also set this field.

### Password sources

There are two supported password sources:

1. Existing process environment:
   - if `REMOTE_<ALIAS>_USE_SSHPASS=1` and `SSHPASS` is already set, reuse it
2. Interactive prompt:
   - when the user adds or selects a remote that uses `sshpass`, and `SSHPASS` is not already available for this run, prompt once for the password
   - store the password only in an in-memory runtime map for the active command

The prompted password must not be written to:

- `.env`
- `runtime_state.json`
- logs, warnings, debug output, or exceptions

### SSH command launch

Introduce a small SSH launcher abstraction used by both:

- `probe_remote_ssh()`
- `RemoteFileCollector`

Behavior:

- normal mode: execute `ssh ...`
- password mode: execute `sshpass -e ssh ...` and pass `SSHPASS` through the subprocess environment

The existing SSH options, connection sharing, timeout behavior, stdout/stderr handling, and stdin/uploaded-script fallbacks remain unchanged.

### Interaction flow

When adding a temporary remote interactively:

1. prompt for host, user, and port
2. ask whether the remote requires password login through `sshpass`
3. if yes and `SSHPASS` is absent for this run, prompt for password using hidden input when possible
4. run SSH connectivity probe with the injected password

For configured remotes selected from the list:

- if a selected remote has `use_sshpass=true` and no password has been supplied for the run, prompt before probe/collect starts

### Persistence rules

Persisted:

- remote alias
- host
- user
- port
- source label
- remote path globs
- `use_sshpass`

Not persisted:

- password
- any derived secret cache

### Failure handling

If `use_sshpass=true` and `sshpass` is unavailable:

- probe and collect should fail with a clear warning such as `sshpass not found`

If password authentication fails:

- surface the SSH error message without echoing the supplied password

If the run is non-interactive and password mode is required but no password is available:

- fail fast with a clear warning such as `missing SSHPASS for password-based remote`

## Testing

Add or update tests for:

- env parsing and persistence of `use_sshpass`
- `probe_remote_ssh()` using `sshpass -e` and subprocess env injection
- `RemoteFileCollector` launching SSH through `sshpass -e`
- interactive selection and temporary remote creation capturing `use_sshpass`
- prompted passwords not being written to `.env` or runtime state
- non-password remotes keeping existing behavior

## Recommendation

Implement this as a minimal Python-only extension first. Do not refactor remote transport more broadly in the same change. The purpose is to make bastion-password flows reliable now, while keeping the code path small and testable.
