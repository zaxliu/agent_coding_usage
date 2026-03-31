from __future__ import annotations

import glob
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Optional

import requests

TOKEN_COOKIE_NAME = "WorkosCursorSessionToken"
WORKOS_ID_COOKIE_NAME = "workos_id"
CURSOR_DOMAIN = "cursor.com"
CURSOR_BASE_URL = "https://cursor.com"


def fetch_cursor_session_token_via_browser(
    usage_url: str = "https://cursor.com/dashboard/usage",
    timeout_sec: int = 600,
    browser: str = "default",
    user_data_dir: Optional[str] = None,
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

    timeout_sec = max(30, timeout_sec)
    deadline = time.monotonic() + timeout_sec
    requested_browser = (browser or "default").strip().lower()
    resolved_browser = _resolve_browser_choice(requested_browser)
    strict_browser = requested_browser != "default"
    initial_candidates = _read_raw_cursor_session_token_candidates_from_local_browsers(
        resolved_browser,
        strict=strict_browser,
    )

    # First try to reuse existing session cookies from local browser profiles.
    token = _read_cursor_session_token_from_local_browsers(
        resolved_browser,
        strict=strict_browser,
    )
    if token:
        return token

    _open_url_in_system_browser(usage_url, browser=resolved_browser)
    if requested_browser == "default":
        print(f"info: system default browser resolved to {resolved_browser}")
    else:
        print(f"info: using selected browser: {resolved_browser}")
    if user_data_dir and user_data_dir.strip():
        print(
            "warn: --cursor-login-user-data-dir is ignored in system-browser mode; "
            "using native browser profile cookies instead."
        )
    print("info: no valid Cursor session token found. please login in your normal browser window.")
    print(f"info: waiting up to {timeout_sec}s for Cursor session cookie...")
    printed_marks: set[int] = set()

    while time.monotonic() < deadline:
        raw_candidates = _read_raw_cursor_session_token_candidates_from_local_browsers(
            resolved_browser,
            strict=strict_browser,
        )
        token = _find_valid_token(raw_candidates)
        if token:
            return token
        fallback_token = _select_login_fallback_token(raw_candidates, baseline=initial_candidates)
        if fallback_token:
            print(
                "warn: detected a new local Cursor session cookie, but online validation did not pass. "
                "continuing with the newest browser cookie."
            )
            return fallback_token
        elapsed = int(timeout_sec - max(0.0, deadline - time.monotonic()))
        mark = elapsed // 15
        if mark > 0 and mark not in printed_marks:
            printed_marks.add(mark)
            print(
                "info: still waiting for browser cookie. "
                "confirm login completed in the selected browser tab and refresh /dashboard/usage once."
            )
        time.sleep(2)

    final_candidates = _read_raw_cursor_session_token_candidates_from_local_browsers(
        resolved_browser,
        strict=strict_browser,
    )
    if final_candidates:
        print(
            "warn: timed out waiting for online validation, but found a local Cursor session cookie. "
            "continuing with the newest browser cookie."
        )
        return final_candidates[0]
    for line in _cookie_visibility_diagnostics():
        print(line)
    raise RuntimeError(
        "timed out waiting for a valid WorkosCursorSessionToken in local browser cookies. "
        "confirm login is completed in your normal browser and retry."
    )


def fetch_cursor_workos_id_from_local_browsers(browser: str = "default") -> Optional[str]:
    requested_browser = (browser or "default").strip().lower()
    resolved_browser = _resolve_browser_choice(requested_browser)
    strict_browser = requested_browser != "default"
    return _read_cookie_value_from_local_browsers(
        preferred_browser=resolved_browser,
        cookie_name=WORKOS_ID_COOKIE_NAME,
        strict=strict_browser,
    )


def open_cursor_dashboard_login_page(browser: str = "default") -> None:
    _open_url_in_system_browser(f"{CURSOR_BASE_URL}/dashboard/usage", browser=browser)


def resolve_cursor_login_browser_choice(browser: str = "default") -> str:
    requested_browser = (browser or "default").strip().lower()
    return _resolve_browser_choice(requested_browser)


def _default_managed_profile_dir(browser: str) -> str:
    normalized = _normalize_browser_name(browser)
    slug = "edge-profile" if normalized == "msedge" else "chrome-profile"
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if os.name == "nt":
        root = (
            PureWindowsPath(local_appdata)
            if local_appdata
            else PureWindowsPath(str(Path.home())) / "AppData" / "Local"
        )
        return str(root / "llm-usage" / "cursor-login" / slug)
    return str((Path.home() / ".llm-usage" / "cursor-login" / slug).resolve())


def _fetch_cursor_session_token_via_managed_profile(
    usage_url: str,
    timeout_sec: int,
    browser: str,
    user_data_dir: Optional[str],
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


def _read_raw_cursor_session_token_candidates_from_managed_profile(
    browser: str,
    user_data_dir: str,
) -> list[str]:
    return _collect_candidate_tokens_from_chromium_profile(
        browser=browser,
        user_data_dir=user_data_dir,
        cookie_name=TOKEN_COOKIE_NAME,
    )


def _read_cursor_session_token_from_local_browsers(
    preferred_browser: str,
    strict: bool = False,
) -> Optional[str]:
    candidates = _read_raw_cursor_session_token_candidates_from_local_browsers(
        preferred_browser,
        strict=strict,
    )
    return _find_valid_token(candidates)


def _read_raw_cursor_session_token_candidates_from_local_browsers(
    preferred_browser: str,
    strict: bool = False,
) -> list[str]:
    return _collect_candidate_tokens_from_local_browsers(
        preferred_browser,
        strict=strict,
    )


def _read_cookie_value_from_local_browsers(
    preferred_browser: str,
    cookie_name: str,
    strict: bool = False,
) -> Optional[str]:
    candidates = _collect_named_cookie_values_from_local_browsers(
        preferred_browser=preferred_browser,
        cookie_name=cookie_name,
        strict=strict,
    )
    return candidates[0] if candidates else None


def _cookie_visibility_diagnostics() -> list[str]:
    names = ["chrome", "msedge", "safari", "firefox", "chromium"]
    lines: list[str] = ["warn: cookie visibility diagnostics:"]
    for name in names:
        profile_note = ""
        if name in {"chrome", "msedge", "chromium"}:
            profile_files = _chromium_cookie_files(name)
            profile_note = f", scanned {len(profile_files)} profile cookie file(s)"
        try:
            tokens = _collect_candidate_tokens_from_local_browsers(name, strict=True)
            if not tokens:
                lines.append(f"warn:   {name}: 0 candidate token(s){profile_note}")
                continue
            ok, reason = _validate_cursor_session_token(tokens[0])
            verdict = "ok" if ok else reason
            lines.append(
                f"warn:   {name}: {len(tokens)} candidate token(s), "
                f"first token validation: {verdict}{profile_note}"
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"warn:   {name}: error reading cookies: {exc}{profile_note}")
    return lines


def _collect_candidate_tokens_from_local_browsers(
    preferred_browser: str,
    strict: bool = False,
) -> list[str]:
    try:
        import browser_cookie3  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "browser-cookie3 is not installed. run: pip install browser-cookie3"
        ) from exc

    order = _candidate_browser_order(preferred_browser, strict=strict)
    out: list[str] = []
    for browser in order:
        for token in _read_tokens_with_browser_cookie3(browser_cookie3, browser):
            if token in out:
                continue
            out.append(token)
    return out


def _collect_named_cookie_values_from_local_browsers(
    preferred_browser: str,
    cookie_name: str,
    strict: bool = False,
) -> list[str]:
    try:
        import browser_cookie3  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "browser-cookie3 is not installed. run: pip install browser-cookie3"
        ) from exc

    order = _candidate_browser_order(preferred_browser, strict=strict)
    out: list[str] = []
    for browser in order:
        for value in _read_named_cookie_values_with_browser_cookie3(
            browser_cookie3,
            browser,
            cookie_name=cookie_name,
        ):
            if value in out:
                continue
            out.append(value)
    return out


def _collect_candidate_tokens_from_chromium_profile(
    browser: str,
    user_data_dir: str,
    cookie_name: str,
) -> list[str]:
    try:
        import browser_cookie3  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "browser-cookie3 is not installed. run: pip install browser-cookie3"
        ) from exc

    loader = _cookie_loader(browser_cookie3, _normalize_browser_name(browser))
    if loader is None:
        return []

    out: list[str] = []
    for cookie_file in _chromium_cookie_files_from_user_data_dir(user_data_dir):
        try:
            load_kwargs: dict[str, Any] = {
                "cookie_file": cookie_file,
                "domain_name": CURSOR_DOMAIN,
            }
            key_file = _chromium_key_file_for_cookie_file(cookie_file)
            if key_file:
                load_kwargs["key_file"] = key_file
            cookies = loader(**load_kwargs)
        except Exception:  # noqa: BLE001
            continue
        for value in _extract_cookie_values_from_cookie_iterable(cookies, cookie_name):
            if value not in out:
                out.append(value)
    return out


def _read_tokens_with_browser_cookie3(browser_cookie3: Any, browser: str) -> list[str]:
    return _read_named_cookie_values_with_browser_cookie3(
        browser_cookie3=browser_cookie3,
        browser=browser,
        cookie_name=TOKEN_COOKIE_NAME,
    )


def _read_named_cookie_values_with_browser_cookie3(
    browser_cookie3: Any,
    browser: str,
    cookie_name: str,
) -> list[str]:
    loader = _cookie_loader(browser_cookie3, browser)
    if loader is None:
        return []

    # browser-cookie3 picks only one cookie DB by default.
    # For Chromium-based browsers, scan all profile cookie DBs explicitly.
    if browser in {"chrome", "chromium", "msedge"}:
        out: list[str] = []
        for cookie_file in _chromium_cookie_files(browser):
            try:
                load_kwargs: dict[str, Any] = {
                    "cookie_file": cookie_file,
                    "domain_name": CURSOR_DOMAIN,
                }
                key_file = _chromium_key_file_for_cookie_file(cookie_file)
                if key_file:
                    load_kwargs["key_file"] = key_file
                cookies = loader(**load_kwargs)
            except Exception:  # noqa: BLE001
                continue
            for value in _extract_cookie_values_from_cookie_iterable(cookies, cookie_name):
                if value not in out:
                    out.append(value)
        if out:
            return out

    try:
        cookies = loader(domain_name=CURSOR_DOMAIN)
    except Exception:  # noqa: BLE001
        return []
    return _extract_cookie_values_from_cookie_iterable(cookies, cookie_name)


def _cookie_loader(browser_cookie3: Any, browser: str):  # noqa: ANN201
    mapping = {
        "chrome": "chrome",
        "chromium": "chromium",
        "msedge": "edge",
        "firefox": "firefox",
        "safari": "safari",
    }
    target = mapping.get(browser)
    if not target:
        return None
    return getattr(browser_cookie3, target, None)


def _extract_tokens_from_cookie_iterable(cookies: Any) -> list[str]:
    return _extract_cookie_values_from_cookie_iterable(cookies, TOKEN_COOKIE_NAME)


def _extract_cookie_values_from_cookie_iterable(cookies: Any, cookie_name: str) -> list[str]:
    now = time.time()
    scored: list[tuple[float, str]] = []
    for cookie in cookies:
        name = getattr(cookie, "name", "")
        if name != cookie_name:
            continue

        domain = str(getattr(cookie, "domain", "")).lower()
        if CURSOR_DOMAIN not in domain:
            continue

        value = str(getattr(cookie, "value", "")).strip()
        if not value:
            continue

        expires_raw = getattr(cookie, "expires", None)
        expiry_score = 0.0
        if isinstance(expires_raw, (int, float)):
            # Skip obviously expired persistent cookies.
            if expires_raw > 0 and expires_raw < now:
                continue
            expiry_score = float(expires_raw)

        scored.append((expiry_score, value))

    # Prefer tokens with a farther expiry first.
    scored.sort(key=lambda item: item[0], reverse=True)

    out: list[str] = []
    for _, token in scored:
        if token not in out:
            out.append(token)
    return out


def _extract_token_from_cookie_iterable(cookies: Any) -> Optional[str]:
    tokens = _extract_tokens_from_cookie_iterable(cookies)
    return tokens[0] if tokens else None


def _find_valid_token(candidates: list[str]) -> Optional[str]:
    if not candidates:
        return None

    try:
        workos_ids = _collect_named_cookie_values_from_local_browsers(
            preferred_browser="default",
            cookie_name=WORKOS_ID_COOKIE_NAME,
            strict=False,
        )
    except Exception:  # noqa: BLE001
        workos_ids = []
    first_candidate = candidates[0]
    saw_only_request_failures = True
    for token in candidates:
        ok, reason = _validate_cursor_session_token(token)
        if ok:
            return token
        if "request failed" not in reason.lower():
            saw_only_request_failures = False
        for workos_id in workos_ids:
            ok_w, reason_w = _validate_cursor_session_token(token, workos_id=workos_id)
            if ok_w:
                return token
            if "request failed" not in reason_w.lower():
                saw_only_request_failures = False

    # If validation failed only because runtime cannot reach cursor.com (proxy/network),
    # fall back to the freshest local candidate so the caller can proceed.
    if saw_only_request_failures:
        return first_candidate
    return None


def _select_login_fallback_token(candidates: list[str], baseline: list[str]) -> Optional[str]:
    if not candidates:
        return None
    if not baseline:
        return candidates[0]
    for token in candidates:
        if token not in baseline:
            return token
    return None


def _validate_cursor_session_token(token: str, workos_id: Optional[str] = None) -> tuple[bool, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=1)

    url = f"{CURSOR_BASE_URL}/api/dashboard/get-filtered-usage-events"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": CURSOR_BASE_URL,
        "Referer": f"{CURSOR_BASE_URL}/dashboard/usage",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    start_ms = str(int(start.timestamp() * 1000))
    end_ms = str(int(end.timestamp() * 1000))
    payloads = [
        {
            "teamId": 0,
            "startDate": start_ms,
            "endDate": end_ms,
            "page": 1,
            "pageSize": 1,
        },
        {
            "startDate": start_ms,
            "endDate": end_ms,
            "page": 1,
            "pageSize": 1,
        },
    ]
    cookies = {TOKEN_COOKIE_NAME: token}
    if workos_id:
        cookies[WORKOS_ID_COOKIE_NAME] = workos_id

    auth_failures: list[tuple[int, str]] = []
    for body in payloads:
        try:
            response = requests.post(
                url,
                headers=headers,
                cookies=cookies,
                json=body,
                timeout=10,
            )
        except requests.RequestException as exc:
            return False, f"request failed: {exc}"

        if response.status_code in {401, 403}:
            auth_failures.append((response.status_code, response.text[:140]))
            continue

        if response.status_code >= 400:
            return False, f"http error {response.status_code}"

        try:
            payload = response.json()
        except ValueError:
            # If server returned 2xx but non-JSON, still treat token as plausible.
            return True, "ok"

        if isinstance(payload, dict):
            events = payload.get("usageEventsDisplay")
            if isinstance(events, list):
                return True, "ok"

        return True, "ok"

    if auth_failures:
        statuses = "/".join(str(code) for code, _ in auth_failures)
        hints = " | ".join(
            snippet.replace("\n", " ").strip()
            for _, snippet in auth_failures
            if snippet.strip()
        )
        if hints:
            return False, f"authentication failed ({statuses}): {hints}"
        return False, f"authentication failed ({statuses})"
    return False, "authentication failed"


def _open_url_in_system_browser(url: str, browser: str = "default", user_data_dir: Optional[str] = None) -> None:
    browser = _normalize_browser_name(browser)
    try:
        if sys.platform == "darwin":
            app_name = _macos_app_name_for_browser(browser)
            if app_name:
                subprocess.Popen(["open", "-a", app_name, url])
                return
            subprocess.Popen(["open", url])
            return
        if os.name == "nt":
            command = _windows_browser_command(browser)
            if user_data_dir and browser in {"chrome", "chromium", "msedge"} and command:
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
            if command:
                subprocess.Popen([*command, url])
                return
            os.startfile(url)  # type: ignore[attr-defined]
            return
        linux_cmd = _linux_browser_command(browser)
        if linux_cmd:
            subprocess.Popen([linux_cmd, url])
            return
        subprocess.Popen(["xdg-open", url])
    except OSError as exc:
        raise RuntimeError(f"failed to open system browser: {exc}") from exc


def _resolve_browser_choice(requested: str) -> str:
    requested = _normalize_browser_name(requested or "default")
    if requested in {"chrome", "chromium", "msedge", "safari", "firefox"}:
        return requested
    if requested != "default":
        return "chromium"

    detected = _detect_system_default_browser()
    if detected:
        return detected
    return "chromium"


def _detect_system_default_browser() -> Optional[str]:
    if os.name == "nt":
        return _detect_windows_default_browser()

    if sys.platform != "darwin":
        return None

    secure = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    legacy = Path.home() / "Library/Preferences/com.apple.LaunchServices.plist"
    bundle_id = _extract_handler_bundle_id_from_plist(secure) or _extract_handler_bundle_id_from_plist(legacy)
    if not bundle_id:
        return None

    mapping = {
        "com.google.chrome": "chrome",
        "com.google.chrome.beta": "chrome",
        "com.google.chrome.dev": "chrome",
        "com.google.chrome.canary": "chrome",
        "com.microsoft.edgemac": "msedge",
        "com.apple.safari": "safari",
        "org.mozilla.firefox": "firefox",
        "com.brave.browser": "chromium",
        "company.thebrowser.browser": "chromium",
    }
    return mapping.get(bundle_id)


def _detect_windows_default_browser() -> Optional[str]:
    for scheme in ("https", "http"):
        key = (
            r"HKCU\Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
            rf"\{scheme}\UserChoice"
        )
        try:
            result = subprocess.run(
                ["reg", "query", key, "/v", "ProgId"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            continue
        if result.returncode != 0:
            continue
        prog_id = _extract_windows_progid_from_reg_query(result.stdout)
        if not prog_id:
            continue
        mapped = _map_windows_progid_to_browser(prog_id)
        if mapped:
            return mapped
    return None


def _extract_windows_progid_from_reg_query(output: str) -> Optional[str]:
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        if parts[0].lower() != "progid":
            continue
        return parts[-1].strip().lower()
    return None


def _map_windows_progid_to_browser(prog_id: str) -> Optional[str]:
    value = (prog_id or "").strip().lower()
    if not value:
        return None
    mapping = {
        "chromehtml": "chrome",
        "microsoftedgehtm": "msedge",
        "msedgehtm": "msedge",
        "firefoxurl": "firefox",
        "chromiumhtm": "chromium",
        "bravehtml": "chromium",
    }
    if value in mapping:
        return mapping[value]
    if "edge" in value:
        return "msedge"
    if "chrome" in value:
        return "chrome"
    if "firefox" in value:
        return "firefox"
    if "chromium" in value:
        return "chromium"
    return None


def _extract_handler_bundle_id_from_plist(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return None

    handlers = payload.get("LSHandlers")
    if not isinstance(handlers, list):
        return None

    for scheme in ("https", "http"):
        for item in handlers:
            if not isinstance(item, dict):
                continue
            if item.get("LSHandlerURLScheme") != scheme:
                continue
            bundle_id = item.get("LSHandlerRoleAll") or item.get("LSHandlerRoleViewer")
            if isinstance(bundle_id, str) and bundle_id.strip():
                return bundle_id.strip().lower()
    return None


def _candidate_browser_order(preferred_browser: str, strict: bool) -> list[str]:
    preferred = _normalize_browser_name(preferred_browser)
    if preferred == "default":
        preferred = "chromium"

    if strict:
        return [preferred]

    order = [preferred, "chrome", "msedge", "chromium", "firefox", "safari"]
    deduped: list[str] = []
    for name in order:
        normalized = _normalize_browser_name(name)
        if normalized in deduped:
            continue
        deduped.append(normalized)
    return deduped


def _normalize_browser_name(name: str) -> str:
    value = (name or "").strip().lower()
    if value == "edge":
        return "msedge"
    if value == "webkit":
        return "safari"
    return value


def _macos_app_name_for_browser(browser: str) -> Optional[str]:
    mapping = {
        "chrome": "Google Chrome",
        "msedge": "Microsoft Edge",
        "safari": "Safari",
        "firefox": "Firefox",
        "chromium": "Chromium",
    }
    return mapping.get(browser)


def _windows_browser_command(browser: str) -> Optional[list[str]]:
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
        "firefox": [
            os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
        ],
    }
    for candidate in candidates.get(_normalize_browser_name(browser), []):
        if candidate and os.path.isfile(candidate):
            return [candidate]
    return None


def _linux_browser_command(browser: str) -> Optional[str]:
    mapping = {
        "chrome": "google-chrome",
        "msedge": "microsoft-edge",
        "firefox": "firefox",
        "chromium": "chromium-browser",
    }
    return mapping.get(browser)


def _chromium_cookie_files(browser: str) -> list[str]:
    browser = _normalize_browser_name(browser)
    if browser not in {"chrome", "chromium", "msedge"}:
        return []

    profile_patterns = [
        "Default/Cookies",
        "Default/Network/Cookies",
        "Profile */Cookies",
        "Profile */Network/Cookies",
        "Guest Profile/Cookies",
        "Guest Profile/Network/Cookies",
        "System Profile/Cookies",
        "System Profile/Network/Cookies",
    ]

    out: list[str] = []
    for root_pattern in _chromium_user_data_root_patterns(browser):
        expanded_root = os.path.expandvars(os.path.expanduser(root_pattern))
        for root in sorted(glob.glob(expanded_root)):
            for rel in profile_patterns:
                for cookie_file in sorted(glob.glob(os.path.join(root, rel))):
                    if cookie_file in out:
                        continue
                    if os.path.isfile(cookie_file):
                        out.append(cookie_file)
    return out


def _chromium_cookie_files_from_user_data_dir(user_data_dir: str) -> list[str]:
    profile_patterns = [
        "Default/Cookies",
        "Default/Network/Cookies",
        "Profile */Cookies",
        "Profile */Network/Cookies",
        "Guest Profile/Cookies",
        "Guest Profile/Network/Cookies",
        "System Profile/Cookies",
        "System Profile/Network/Cookies",
    ]

    root = os.path.expandvars(os.path.expanduser(user_data_dir))
    out: list[str] = []
    for rel in profile_patterns:
        for cookie_file in sorted(glob.glob(os.path.join(root, rel))):
            if cookie_file in out:
                continue
            if os.path.isfile(cookie_file):
                out.append(cookie_file)
    return out


def _chromium_key_file_for_cookie_file(cookie_file: str) -> Optional[str]:
    path = Path(cookie_file)
    name = path.name.lower()
    if name != "cookies":
        return None

    parent = path.parent
    if parent.name.lower() == "network":
        user_data_root = parent.parent.parent
    else:
        user_data_root = parent.parent

    key_file = user_data_root / "Local State"
    if key_file.is_file():
        return str(key_file)
    return None


def _chromium_user_data_root_patterns(browser: str) -> list[str]:
    browser = _normalize_browser_name(browser)
    if sys.platform == "darwin":
        mapping = {
            "chrome": ["~/Library/Application Support/Google/Chrome*"],
            "msedge": ["~/Library/Application Support/Microsoft Edge*"],
            "chromium": ["~/Library/Application Support/Chromium*"],
        }
        return mapping.get(browser, [])

    if os.name == "nt":
        mapping = {
            "chrome": [
                r"%LOCALAPPDATA%\Google\Chrome*\User Data",
                r"%APPDATA%\Google\Chrome*\User Data",
            ],
            "msedge": [
                r"%LOCALAPPDATA%\Microsoft\Edge*\User Data",
                r"%APPDATA%\Microsoft\Edge*\User Data",
            ],
            "chromium": [
                r"%LOCALAPPDATA%\Chromium*\User Data",
                r"%APPDATA%\Chromium*\User Data",
            ],
        }
        return mapping.get(browser, [])

    mapping = {
        "chrome": [
            "~/.config/google-chrome*",
            "~/.var/app/com.google.Chrome/config/google-chrome*",
        ],
        "msedge": [
            "~/.config/microsoft-edge*",
            "~/.var/app/com.microsoft.Edge/config/microsoft-edge*",
        ],
        "chromium": [
            "~/.config/chromium*",
            "~/.var/app/org.chromium.Chromium/config/chromium*",
        ],
    }
    return mapping.get(browser, [])
