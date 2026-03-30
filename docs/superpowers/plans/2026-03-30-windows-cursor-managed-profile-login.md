# Windows Cursor Managed Profile Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Windows-safe Cursor dashboard login flow that uses a tool-managed Chromium profile instead of trying to read cookies from the user's default Chrome/Edge profile.

**Architecture:** Keep the existing local-log-first and dashboard-token fallback behavior, but replace the current Windows Chromium manual-only branch with a new `managed-profile` login mode. The login flow launches Chrome/Edge with a dedicated `--user-data-dir`, waits for a `WorkosCursorSessionToken` to appear in that profile's cookie DB, validates it against Cursor dashboard, then persists it to `.env`.

**Tech Stack:** Python 3, argparse, subprocess, browser-cookie3, existing Cursor dashboard validation flow, pytest

---

## File Map

- Modify: `src/llm_usage/cursor_login.py`
  - Add managed-profile browser launch helpers
  - Add cookie reads scoped to an explicit Chromium user-data-dir
  - Add Windows-only browser executable resolution and profile path helpers
- Modify: `src/llm_usage/main.py`
  - Add `--cursor-login-mode`
  - Route Windows Chromium `auto` mode to `managed-profile`
  - Keep `manual` as fallback
- Modify: `README.md`
  - Document new login mode and updated Windows flow
- Modify: `docs/ADAPTERS.md`
  - Document Windows managed-profile behavior for Cursor dashboard auth
- Modify: `src/llm_usage/resources/bootstrap.env`
  - Add comments for the new login mode if needed by runtime docs/examples
- Modify: `tests/test_cursor_login_module.py`
  - Add focused unit tests for managed-profile helpers
- Modify: `tests/test_cursor_login.py`
  - Add integration-ish tests for CLI/login routing behavior in `main.py`

## Design Constraints

- Do not remove the existing `CURSOR_WEB_SESSION_TOKEN` reuse path.
- Do not remove the manual token paste fallback.
- Do not attempt to auto-read cookies from the user's default Chrome/Edge profile on Windows.
- Keep non-Windows behavior unchanged unless a shared helper simplification is clearly harmless.
- Keep `--cursor-login-user-data-dir` meaningful in managed-profile mode.

## Proposed CLI Contract

- `--cursor-login-mode auto|managed-profile|manual`
  - `auto`:
    - Windows + `default`/`chrome`/`chromium`/`edge`/`msedge` => `managed-profile`
    - everything else => existing browser-cookie flow
  - `managed-profile`:
    - launch a dedicated Chromium profile and scan only that profile's cookies
  - `manual`:
    - open login page and prompt user to paste `CURSOR_WEB_SESSION_TOKEN`

Default remains `auto`.

## Managed Profile Behavior

- Default managed profile path on Windows:
  - `%LOCALAPPDATA%/llm-usage/cursor-login/chrome-profile`
  - `%LOCALAPPDATA%/llm-usage/cursor-login/edge-profile`
  - exact final path can be adjusted during implementation, but must be stable and browser-specific
- If `--cursor-login-user-data-dir` is provided, use that path instead.
- Launch browser with:
  - `--user-data-dir=<managed-dir>`
  - `--no-first-run`
  - `--new-window`
  - target URL `https://cursor.com/dashboard/usage`
- Poll the managed profile's cookie DB files until token appears or timeout hits.
- Validate token using existing `_validate_cursor_session_token()`.
- Save `CURSOR_WEB_SESSION_TOKEN` and optional `CURSOR_WEB_WORKOS_ID` to `.env`.

## Task 1: Add Failing Tests For Login Mode Routing

**Files:**
- Modify: `tests/test_cursor_login.py`
- Modify: `src/llm_usage/main.py`

- [ ] **Step 1: Add a failing test for Windows Chromium `auto` mode selecting managed-profile**

```python
def test_maybe_capture_cursor_token_windows_chromium_auto_uses_managed_profile(monkeypatch):
    monkeypatch.setattr(main.os, "name", "nt")
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_clear_saved_cursor_token", lambda: None)

    class _Collector:
        def probe(self):
            return False, "cursor dashboard unavailable"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())

    calls = []

    def _fake_fetch(*, timeout_sec, browser, user_data_dir, login_mode="auto"):
        calls.append(
            {
                "timeout_sec": timeout_sec,
                "browser": browser,
                "user_data_dir": user_data_dir,
                "login_mode": login_mode,
            }
        )
        return "token-from-browser"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_fetch)
    monkeypatch.setattr(main, "_prompt_for_manual_cursor_token", lambda *args, **kwargs: None)

    warning = main._maybe_capture_cursor_token(
        timeout_sec=60,
        browser="chrome",
        user_data_dir="",
        login_mode="auto",
    )

    assert warning is None
    assert calls == [
        {
            "timeout_sec": 60,
            "browser": "chrome",
            "user_data_dir": "",
            "login_mode": "managed-profile",
        }
    ]
```

- [ ] **Step 2: Run the new routing test and verify it fails**

Run: `pytest tests/test_cursor_login.py::test_maybe_capture_cursor_token_windows_chromium_auto_uses_managed_profile -v`

Expected: FAIL because `_maybe_capture_cursor_token()` and `_capture_and_save_cursor_token()` do not yet accept `login_mode`.

- [ ] **Step 3: Extend `main.py` function signatures to carry `login_mode`**

```python
def _capture_and_save_cursor_token(
    timeout_sec: int,
    browser: str,
    user_data_dir: str,
    *,
    login_mode: str = "auto",
) -> str:
    token = fetch_cursor_session_token_via_browser(
        timeout_sec=timeout_sec,
        browser=browser,
        user_data_dir=user_data_dir,
        login_mode=login_mode,
    )
    workos_id = fetch_cursor_workos_id_from_local_browsers(browser=browser)
    _save_cursor_web_credentials(token, workos_id or "")
    return token
```

- [ ] **Step 4: Add a helper that resolves effective Windows login mode**

```python
def _resolve_cursor_login_mode(login_mode: str, browser: str) -> str:
    normalized_mode = (login_mode or "auto").strip().lower() or "auto"
    normalized_browser = (browser or "default").strip().lower()
    if normalized_mode != "auto":
        return normalized_mode
    if os.name == "nt" and normalized_browser in {"default", "chrome", "chromium", "edge", "msedge"}:
        return "managed-profile"
    return "auto"
```

- [ ] **Step 5: Update `_maybe_capture_cursor_token()` to use resolved mode**

```python
effective_login_mode = _resolve_cursor_login_mode(login_mode, browser)

if effective_login_mode == "manual":
    if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=False):
        return None
    print("warn: continuing with local cursor sources")
    return probe_warning
```

- [ ] **Step 6: Re-run the routing test and verify it passes**

Run: `pytest tests/test_cursor_login.py::test_maybe_capture_cursor_token_windows_chromium_auto_uses_managed_profile -v`

Expected: PASS

- [ ] **Step 7: Commit the routing changes**

```bash
git add tests/test_cursor_login.py src/llm_usage/main.py
git commit -m "feat: route Windows Cursor login to managed profile mode"
```

## Task 2: Add Failing Tests For Managed Profile Cookie Capture

**Files:**
- Modify: `tests/test_cursor_login_module.py`
- Modify: `src/llm_usage/cursor_login.py`

- [ ] **Step 1: Add a failing test for explicit Chromium profile cookie scanning**

```python
def test_fetch_cursor_session_token_via_browser_managed_profile_reads_explicit_profile(monkeypatch):
    calls = []

    monkeypatch.setattr(
        cursor_login,
        "_open_url_in_system_browser",
        lambda url, browser="default", user_data_dir=None: calls.append(
            {"url": url, "browser": browser, "user_data_dir": user_data_dir}
        ),
    )
    monkeypatch.setattr(
        cursor_login,
        "_read_raw_cursor_session_token_candidates_from_managed_profile",
        lambda browser, user_data_dir: ["token-abc"],
    )
    monkeypatch.setattr(cursor_login, "_find_valid_token", lambda candidates: candidates[0])

    token = cursor_login.fetch_cursor_session_token_via_browser(
        timeout_sec=30,
        browser="chrome",
        user_data_dir="C:/tmp/cursor-profile",
        login_mode="managed-profile",
    )

    assert token == "token-abc"
    assert calls == [
        {
            "url": "https://cursor.com/dashboard/usage",
            "browser": "chrome",
            "user_data_dir": "C:/tmp/cursor-profile",
        }
    ]
```

- [ ] **Step 2: Add a failing test for default managed profile path resolution on Windows**

```python
def test_default_managed_profile_dir_uses_localappdata(monkeypatch):
    monkeypatch.setattr(cursor_login.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")

    path = cursor_login._default_managed_profile_dir("chrome")

    assert path.endswith(r"llm-usage\cursor-login\chrome-profile")
```

- [ ] **Step 3: Run the managed-profile tests and verify they fail**

Run: `pytest tests/test_cursor_login_module.py -k managed_profile -v`

Expected: FAIL because managed-profile helpers do not exist.

- [ ] **Step 4: Add explicit login mode parameter and branching in `fetch_cursor_session_token_via_browser()`**

```python
def fetch_cursor_session_token_via_browser(
    usage_url: str = "https://cursor.com/dashboard/usage",
    timeout_sec: int = 600,
    browser: str = "default",
    user_data_dir: str | None = None,
    login_mode: str = "auto",
) -> str:
    resolved_mode = (login_mode or "auto").strip().lower() or "auto"
    if resolved_mode == "managed-profile":
        return _fetch_cursor_session_token_via_managed_profile(
            usage_url=usage_url,
            timeout_sec=timeout_sec,
            browser=browser,
            user_data_dir=user_data_dir,
        )
```

- [ ] **Step 5: Implement `_default_managed_profile_dir()` and `_fetch_cursor_session_token_via_managed_profile()`**

```python
def _default_managed_profile_dir(browser: str) -> str:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    root = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    normalized = _normalize_browser_name(browser)
    slug = "edge-profile" if normalized == "msedge" else "chrome-profile"
    return str(root / "llm-usage" / "cursor-login" / slug)
```

```python
def _fetch_cursor_session_token_via_managed_profile(
    usage_url: str,
    timeout_sec: int,
    browser: str,
    user_data_dir: str | None,
) -> str:
    managed_dir = (user_data_dir or "").strip() or _default_managed_profile_dir(browser)
    Path(managed_dir).mkdir(parents=True, exist_ok=True)
    _open_url_in_system_browser(usage_url, browser=browser, user_data_dir=managed_dir)

    deadline = time.monotonic() + max(30, timeout_sec)
    while time.monotonic() < deadline:
        candidates = _read_raw_cursor_session_token_candidates_from_managed_profile(
            browser=browser,
            user_data_dir=managed_dir,
        )
        token = _find_valid_token(candidates)
        if token:
            return token
        time.sleep(2)
    raise RuntimeError("timed out waiting for Cursor session cookie in managed browser profile")
```

- [ ] **Step 6: Implement cookie reads scoped to explicit user-data-dir**

```python
def _read_raw_cursor_session_token_candidates_from_managed_profile(
    browser: str,
    user_data_dir: str,
) -> list[str]:
    return _collect_candidate_tokens_from_chromium_profile(
        browser=browser,
        user_data_dir=user_data_dir,
        cookie_name=TOKEN_COOKIE_NAME,
    )
```

```python
def _collect_candidate_tokens_from_chromium_profile(
    browser: str,
    user_data_dir: str,
    cookie_name: str,
) -> list[str]:
    import browser_cookie3

    loader = _cookie_loader(browser_cookie3, _normalize_browser_name(browser))
    if loader is None:
        return []
    out: list[str] = []
    for cookie_file in _chromium_cookie_files_from_user_data_dir(user_data_dir):
        try:
            cookies = loader(cookie_file=cookie_file, domain_name=CURSOR_DOMAIN)
        except Exception:
            continue
        for value in _extract_cookie_values_from_cookie_iterable(cookies, cookie_name):
            if value not in out:
                out.append(value)
    return out
```

- [ ] **Step 7: Re-run the managed-profile tests and verify they pass**

Run: `pytest tests/test_cursor_login_module.py -k managed_profile -v`

Expected: PASS

- [ ] **Step 8: Commit the managed-profile helper changes**

```bash
git add tests/test_cursor_login_module.py src/llm_usage/cursor_login.py
git commit -m "feat: add managed browser profile Cursor login flow"
```

## Task 3: Launch Browser With Managed `--user-data-dir`

**Files:**
- Modify: `src/llm_usage/cursor_login.py`
- Test: `tests/test_cursor_login_module.py`

- [ ] **Step 1: Add a failing test for Windows Chrome launch command**

```python
def test_open_url_in_system_browser_windows_chrome_managed_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(cursor_login.os, "name", "nt")
    monkeypatch.setattr(cursor_login.sys, "platform", "win32")
    monkeypatch.setattr(cursor_login, "_windows_browser_command", lambda browser: [r"C:\Chrome\chrome.exe"])
    monkeypatch.setattr(cursor_login.subprocess, "Popen", lambda cmd: calls.append(cmd))

    cursor_login._open_url_in_system_browser(
        "https://cursor.com/dashboard/usage",
        browser="chrome",
        user_data_dir=r"C:\tmp\cursor-profile",
    )

    assert calls == [[
        r"C:\Chrome\chrome.exe",
        "--user-data-dir=C:\\tmp\\cursor-profile",
        "--no-first-run",
        "--new-window",
        "https://cursor.com/dashboard/usage",
    ]]
```

- [ ] **Step 2: Run the browser-launch test and verify it fails**

Run: `pytest tests/test_cursor_login_module.py::test_open_url_in_system_browser_windows_chrome_managed_profile -v`

Expected: FAIL because `_open_url_in_system_browser()` does not accept `user_data_dir`.

- [ ] **Step 3: Extend `_open_url_in_system_browser()` to accept optional `user_data_dir`**

```python
def _open_url_in_system_browser(url: str, browser: str = "default", user_data_dir: str | None = None) -> None:
    browser = _normalize_browser_name(browser)
    if sys.platform == "win32" and user_data_dir and browser in {"chrome", "chromium", "msedge"}:
        command = _windows_browser_command(browser)
        if command:
            subprocess.Popen(
                [
                    *command,
                    f"--user-data-dir={user_data_dir}",
                    "--no-first-run",
                    "--new-window",
                    url,
                ]
            )
            return
```

- [ ] **Step 4: Implement `_windows_browser_command()`**

```python
def _windows_browser_command(browser: str) -> list[str] | None:
    candidates = {
        "chrome": [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ],
        "msedge": [
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ],
        "chromium": [
            os.path.expandvars(r"%ProgramFiles%\Chromium\Application\chrome.exe"),
        ],
    }
    for candidate in candidates.get(_normalize_browser_name(browser), []):
        if candidate and os.path.isfile(candidate):
            return [candidate]
    return None
```

- [ ] **Step 5: Re-run the browser-launch test and verify it passes**

Run: `pytest tests/test_cursor_login_module.py::test_open_url_in_system_browser_windows_chrome_managed_profile -v`

Expected: PASS

- [ ] **Step 6: Commit the browser launch changes**

```bash
git add tests/test_cursor_login_module.py src/llm_usage/cursor_login.py
git commit -m "feat: launch managed Chromium profile for Cursor login"
```

## Task 4: Wire CLI Arguments And Runtime Fallbacks

**Files:**
- Modify: `src/llm_usage/main.py`
- Modify: `tests/test_cursor_login.py`

- [ ] **Step 1: Add a failing parser test for `--cursor-login-mode`**

```python
def test_collect_parser_accepts_cursor_login_mode():
    parser = main.build_parser()
    args = parser.parse_args(["collect", "--cursor-login-mode", "managed-profile"])
    assert args.cursor_login_mode == "managed-profile"
```

- [ ] **Step 2: Run the parser test and verify it fails**

Run: `pytest tests/test_cursor_login.py::test_collect_parser_accepts_cursor_login_mode -v`

Expected: FAIL because the CLI option does not exist.

- [ ] **Step 3: Add parser option to both `collect` and `sync`**

```python
parser_collect.add_argument(
    "--cursor-login-mode",
    default="auto",
    choices=["auto", "managed-profile", "manual"],
    help="Cursor dashboard login mode",
)
```

- [ ] **Step 4: Pass `cursor_login_mode` through all `_maybe_capture_cursor_token()` call sites**

```python
cursor_probe_warning = _maybe_capture_cursor_token(
    timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
    browser=getattr(args, "cursor_login_browser", "default"),
    user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
    login_mode=getattr(args, "cursor_login_mode", "auto"),
)
```

- [ ] **Step 5: Add fallback rules in `_maybe_capture_cursor_token()`**

```python
if effective_login_mode == "managed-profile":
    try:
        _capture_and_save_cursor_token(
            timeout_sec=timeout_sec,
            browser=browser,
            user_data_dir=user_data_dir,
            login_mode=effective_login_mode,
        )
        print("info: refreshed CURSOR_WEB_SESSION_TOKEN and saved to .env")
        return None
    except RuntimeError as exc:
        print(f"warn: managed-profile cursor login failed: {exc}")
        if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=True):
            return None
        print("warn: continuing with local cursor sources")
        return probe_warning
```

- [ ] **Step 6: Re-run parser and routing tests and verify they pass**

Run: `pytest tests/test_cursor_login.py -k 'cursor_login_mode or managed_profile or maybe_capture_cursor_token' -v`

Expected: PASS

- [ ] **Step 7: Commit the CLI plumbing**

```bash
git add tests/test_cursor_login.py src/llm_usage/main.py
git commit -m "feat: add Cursor login mode CLI option"
```

## Task 5: Update Docs And Runtime Notes

**Files:**
- Modify: `README.md`
- Modify: `docs/ADAPTERS.md`
- Modify: `src/llm_usage/resources/bootstrap.env`

- [ ] **Step 1: Document the new login mode in `README.md` collect and sync sections**

```md
- `--cursor-login-mode`：Cursor 登录模式。`auto` 为默认；Windows Chromium 浏览器下会自动使用 `managed-profile`；也可显式选择 `manual`
- `--cursor-login-user-data-dir`：在 `managed-profile` 模式下作为专用浏览器 profile 目录；未指定时工具会使用默认受控目录
```

- [ ] **Step 2: Replace the old Windows manual-only wording**

```md
Windows 下使用 `default` / `chrome` / `chromium` / `edge` / `msedge` 时，默认不会扫描系统浏览器默认 profile 的 cookie。
程序会优先使用受控浏览器 profile 登录流程；若失败，再回退到手动粘贴 `WorkosCursorSessionToken`。
```

- [ ] **Step 3: Update `docs/ADAPTERS.md` Cursor entry**

```md
- `cursor`: local globs by default; if `CURSOR_WEB_SESSION_TOKEN` is set, uses Cursor dashboard web API. On Windows Chromium browsers, `collect/sync` prefers a tool-managed browser profile login flow instead of scanning the user's default browser cookies; manual token paste remains the fallback.
```

- [ ] **Step 4: Add `.env` comments if needed**

```dotenv
# Cursor dashboard auth is usually captured interactively.
# On Windows Chromium browsers, auto login uses a managed browser profile
# instead of scanning the default browser cookie store.
```

- [ ] **Step 5: Run a targeted doc sanity check**

Run: `rg -n "cursor-login-mode|managed-profile|手动粘贴|managed browser profile" README.md docs/ADAPTERS.md src/llm_usage/resources/bootstrap.env`

Expected: matching lines in all three files

- [ ] **Step 6: Commit the doc changes**

```bash
git add README.md docs/ADAPTERS.md src/llm_usage/resources/bootstrap.env
git commit -m "docs: document Windows managed profile Cursor login"
```

## Task 6: Full Verification

**Files:**
- Modify: none
- Test: `tests/test_cursor_login.py`
- Test: `tests/test_cursor_login_module.py`

- [ ] **Step 1: Run focused Cursor login tests**

Run: `pytest tests/test_cursor_login.py tests/test_cursor_login_module.py -v`

Expected: PASS

- [ ] **Step 2: Run broader dashboard/login regression tests if present**

Run: `pytest tests/test_cursor_dashboard.py -v`

Expected: PASS

- [ ] **Step 3: Run a parser smoke test**

Run: `python3 -m llm_usage.main collect --help`

Expected: help text includes `--cursor-login-mode`

- [ ] **Step 4: Manual Windows QA checklist**

```text
1. On Windows, run:
   llm-usage collect --cursor-login-browser chrome --cursor-login-mode managed-profile
2. Confirm Chrome opens with a fresh managed profile.
3. Login to https://cursor.com/dashboard/usage in that window.
4. Wait for tool to save CURSOR_WEB_SESSION_TOKEN into .env.
5. Re-run the same command and confirm token reuse without re-login.
6. Delete/expire token, rerun, and confirm fallback to managed-profile login and then manual paste if needed.
```

- [ ] **Step 5: Commit verification-only updates if any test-driven fixes were required**

```bash
git add src/llm_usage/cursor_login.py src/llm_usage/main.py tests/test_cursor_login.py tests/test_cursor_login_module.py README.md docs/ADAPTERS.md src/llm_usage/resources/bootstrap.env
git commit -m "test: verify managed profile Cursor login flow"
```

## Self-Review

- Spec coverage:
  - Windows Chromium no longer depends on default-profile cookie scanning: covered by Tasks 1-4
  - Managed profile login flow: covered by Tasks 2-3
  - Manual fallback retained: covered by Task 4
  - Docs updated: covered by Task 5
  - Verification and regression coverage: covered by Task 6
- Placeholder scan:
  - No `TODO`/`TBD` placeholders remain
  - All tasks include concrete files and commands
- Type consistency:
  - `login_mode` flows consistently through parser -> `_maybe_capture_cursor_token()` -> `_capture_and_save_cursor_token()` -> `fetch_cursor_session_token_via_browser()`

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-03-30-windows-cursor-managed-profile-login.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
