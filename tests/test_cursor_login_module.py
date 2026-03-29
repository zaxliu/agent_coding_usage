from types import SimpleNamespace

from llm_usage import cursor_login


def test_extract_token_from_cookie_iterable():
    cookies = [
        SimpleNamespace(name="other", domain="cursor.com", value="x"),
        SimpleNamespace(name="WorkosCursorSessionToken", domain=".cursor.com", value=" token-abc "),
    ]
    assert cursor_login._extract_token_from_cookie_iterable(cookies) == "token-abc"


def test_resolve_browser_choice_maps_webkit_to_safari():
    assert cursor_login._resolve_browser_choice("webkit") == "safari"


def test_resolve_browser_choice_maps_edge_alias():
    assert cursor_login._resolve_browser_choice("edge") == "msedge"


def test_fetch_uses_existing_local_token(monkeypatch):
    monkeypatch.setattr(
        cursor_login,
        "_read_cursor_session_token_from_local_browsers",
        lambda preferred_browser, strict=False: "token-existing",
    )
    opened = {"count": 0}
    monkeypatch.setattr(
        cursor_login,
        "_open_url_in_system_browser",
        lambda url, browser="default": opened.__setitem__("count", opened["count"] + 1),
    )

    token = cursor_login.fetch_cursor_session_token_via_browser(timeout_sec=30)
    assert token == "token-existing"
    assert opened["count"] == 0


def test_fetch_opens_browser_then_polls_until_token(monkeypatch):
    calls = {"n": 0}

    def _fake_raw(preferred_browser, strict=False):  # noqa: ANN001, ANN201
        calls["n"] += 1
        if calls["n"] < 3:
            return []
        return ["token-after-login"]

    monkeypatch.setattr(
        cursor_login,
        "_read_raw_cursor_session_token_candidates_from_local_browsers",
        _fake_raw,
    )
    opened = {"count": 0}
    monkeypatch.setattr(
        cursor_login,
        "_open_url_in_system_browser",
        lambda url, browser="default": opened.__setitem__("count", opened["count"] + 1),
    )
    monkeypatch.setattr(cursor_login.time, "sleep", lambda sec: None)
    monkeypatch.setattr(
        cursor_login,
        "_find_valid_token",
        lambda candidates: candidates[0] if candidates else None,
    )

    token = cursor_login.fetch_cursor_session_token_via_browser(timeout_sec=30)
    assert token == "token-after-login"
    assert opened["count"] == 1


def test_fetch_returns_new_raw_candidate_when_validation_does_not_pass(monkeypatch):
    raw_calls = {"n": 0}

    def _fake_raw(preferred_browser, strict=False):  # noqa: ANN001, ANN201
        raw_calls["n"] += 1
        if raw_calls["n"] < 3:
            return []
        return ["token-from-cookie"]

    monkeypatch.setattr(
        cursor_login,
        "_read_raw_cursor_session_token_candidates_from_local_browsers",
        _fake_raw,
    )
    monkeypatch.setattr(cursor_login, "_find_valid_token", lambda candidates: None)
    opened = {"count": 0}
    monkeypatch.setattr(
        cursor_login,
        "_open_url_in_system_browser",
        lambda url, browser="default": opened.__setitem__("count", opened["count"] + 1),
    )
    monkeypatch.setattr(cursor_login.time, "sleep", lambda sec: None)

    token = cursor_login.fetch_cursor_session_token_via_browser(timeout_sec=30, browser="chrome")
    assert token == "token-from-cookie"
    assert opened["count"] == 1


def test_fetch_returns_latest_raw_candidate_after_timeout(monkeypatch):
    monkeypatch.setattr(
        cursor_login,
        "_read_raw_cursor_session_token_candidates_from_local_browsers",
        lambda preferred_browser, strict=False: ["token-timeout"],
    )
    monkeypatch.setattr(cursor_login, "_find_valid_token", lambda candidates: None)
    monkeypatch.setattr(
        cursor_login,
        "_open_url_in_system_browser",
        lambda url, browser="default": None,
    )
    monkeypatch.setattr(cursor_login.time, "sleep", lambda sec: None)

    monotonic_values = iter([0.0, 0.0, 1.0, 31.0, 31.0])
    monkeypatch.setattr(cursor_login.time, "monotonic", lambda: next(monotonic_values))

    token = cursor_login.fetch_cursor_session_token_via_browser(timeout_sec=30, browser="chrome")
    assert token == "token-timeout"


def test_open_url_uses_selected_macos_browser(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(cursor_login.sys, "platform", "darwin")
    monkeypatch.setattr(cursor_login.subprocess, "Popen", lambda cmd: calls.append(cmd))

    cursor_login._open_url_in_system_browser("https://cursor.com/dashboard/usage", browser="chrome")
    assert calls
    assert calls[0][:3] == ["open", "-a", "Google Chrome"]


def test_candidate_browser_order_strict():
    order = cursor_login._candidate_browser_order("edge", strict=True)
    assert order == ["msedge"]


def test_find_valid_token_accepts_first_when_only_network_failures(monkeypatch):
    monkeypatch.setattr(
        cursor_login,
        "_validate_cursor_session_token",
        lambda token, workos_id="": (False, "request failed: network down"),
    )
    assert cursor_login._find_valid_token(["t1", "t2"]) == "t1"


def test_find_valid_token_rejects_on_auth_failure(monkeypatch):
    monkeypatch.setattr(
        cursor_login,
        "_validate_cursor_session_token",
        lambda token, workos_id="": (False, "authentication failed (session cookie may be expired)"),
    )
    assert cursor_login._find_valid_token(["t1", "t2"]) is None


def test_select_login_fallback_token_prefers_new_cookie():
    assert cursor_login._select_login_fallback_token(["new", "old"], baseline=["old"]) == "new"


def test_cookie_visibility_diagnostics_formats_lines(monkeypatch):
    def _fake_collect(browser, strict=False):  # noqa: ANN001, ANN201
        if browser == "chrome":
            return ["a", "b"]
        if browser == "safari":
            raise RuntimeError("denied")
        return []

    monkeypatch.setattr(cursor_login, "_collect_candidate_tokens_from_local_browsers", _fake_collect)
    monkeypatch.setattr(cursor_login, "_chromium_cookie_files", lambda browser: ["/tmp/a"] if browser == "chrome" else [])
    monkeypatch.setattr(cursor_login, "_validate_cursor_session_token", lambda token: (False, "invalid"))
    lines = cursor_login._cookie_visibility_diagnostics()
    assert lines[0].startswith("warn: cookie visibility diagnostics")
    assert any("chrome: 2" in line for line in lines)
    assert any("scanned 1 profile cookie file(s)" in line for line in lines)
    assert any("safari: error reading cookies" in line for line in lines)


def test_read_tokens_with_browser_cookie3_scans_multiple_profile_files(monkeypatch):
    monkeypatch.setattr(cursor_login, "_chromium_cookie_files", lambda browser: ["/tmp/p1", "/tmp/p2"])

    def _fake_loader(domain_name="", cookie_file=None, key_file=None):  # noqa: ANN001, ANN201
        assert key_file is None
        if cookie_file == "/tmp/p1":
            return [SimpleNamespace(name="WorkosCursorSessionToken", domain=".cursor.com", value="tok-p1")]
        if cookie_file == "/tmp/p2":
            return [SimpleNamespace(name="WorkosCursorSessionToken", domain=".cursor.com", value="tok-p2")]
        return []

    monkeypatch.setattr(cursor_login, "_cookie_loader", lambda browser_cookie3, browser: _fake_loader)
    tokens = cursor_login._read_tokens_with_browser_cookie3(object(), "msedge")
    assert tokens == ["tok-p1", "tok-p2"]


def test_read_tokens_with_browser_cookie3_falls_back_to_default_loader(monkeypatch):
    monkeypatch.setattr(cursor_login, "_chromium_cookie_files", lambda browser: [])

    def _fake_loader(domain_name="", cookie_file=None, key_file=None):  # noqa: ANN001, ANN201
        assert cookie_file is None
        assert key_file is None
        return [SimpleNamespace(name="WorkosCursorSessionToken", domain=".cursor.com", value="tok-default")]

    monkeypatch.setattr(cursor_login, "_cookie_loader", lambda browser_cookie3, browser: _fake_loader)
    tokens = cursor_login._read_tokens_with_browser_cookie3(object(), "msedge")
    assert tokens == ["tok-default"]


def test_chromium_key_file_for_cookie_file_uses_user_data_root(tmp_path):
    user_data_dir = tmp_path / "Google" / "Chrome" / "User Data"
    network_cookie_file = user_data_dir / "Profile 2" / "Network" / "Cookies"
    network_cookie_file.parent.mkdir(parents=True)
    network_cookie_file.write_text("", encoding="utf-8")
    key_file = user_data_dir / "Local State"
    key_file.write_text("{}", encoding="utf-8")

    assert cursor_login._chromium_key_file_for_cookie_file(str(network_cookie_file)) == str(key_file)


def test_read_tokens_with_browser_cookie3_passes_matching_key_file(monkeypatch, tmp_path):
    user_data_dir = tmp_path / "Google" / "Chrome" / "User Data"
    cookie_file = user_data_dir / "Profile 3" / "Network" / "Cookies"
    cookie_file.parent.mkdir(parents=True)
    cookie_file.write_text("", encoding="utf-8")
    key_file = user_data_dir / "Local State"
    key_file.write_text("{}", encoding="utf-8")

    seen: list[tuple[str | None, str | None]] = []

    def _fake_loader(domain_name="", cookie_file=None, key_file=None):  # noqa: ANN001, ANN201
        seen.append((cookie_file, key_file))
        return [SimpleNamespace(name="WorkosCursorSessionToken", domain=".cursor.com", value="tok-win")]

    monkeypatch.setattr(cursor_login, "_chromium_cookie_files", lambda browser: [str(cookie_file)])
    monkeypatch.setattr(cursor_login, "_cookie_loader", lambda browser_cookie3, browser: _fake_loader)

    tokens = cursor_login._read_tokens_with_browser_cookie3(object(), "chrome")
    assert tokens == ["tok-win"]
    assert seen == [(str(cookie_file), str(key_file))]


def test_validate_cursor_session_token_sets_origin_header(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():  # noqa: ANN205
            return {"usageEventsDisplay": []}

    def _fake_post(url, headers, cookies, json, timeout):  # noqa: ANN001, ANN201
        captured["origin"] = headers.get("Origin")
        return _Resp()

    monkeypatch.setattr(cursor_login.requests, "post", _fake_post)
    ok, reason = cursor_login._validate_cursor_session_token("token-abc")
    assert ok is True
    assert reason == "ok"
    assert captured["origin"] == "https://cursor.com"
