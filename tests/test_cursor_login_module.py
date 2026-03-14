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

    def _fake_read(preferred_browser, strict=False):  # noqa: ANN001, ANN201
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return "token-after-login"

    monkeypatch.setattr(cursor_login, "_read_cursor_session_token_from_local_browsers", _fake_read)
    opened = {"count": 0}
    monkeypatch.setattr(
        cursor_login,
        "_open_url_in_system_browser",
        lambda url, browser="default": opened.__setitem__("count", opened["count"] + 1),
    )
    monkeypatch.setattr(cursor_login.time, "sleep", lambda sec: None)

    token = cursor_login.fetch_cursor_session_token_via_browser(timeout_sec=30)
    assert token == "token-after-login"
    assert opened["count"] == 1


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
        lambda token: (False, "request failed: network down"),
    )
    assert cursor_login._find_valid_token(["t1", "t2"]) == "t1"


def test_find_valid_token_rejects_on_auth_failure(monkeypatch):
    monkeypatch.setattr(
        cursor_login,
        "_validate_cursor_session_token",
        lambda token: (False, "authentication failed (session cookie may be expired)"),
    )
    assert cursor_login._find_valid_token(["t1", "t2"]) is None


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

    def _fake_loader(domain_name="", cookie_file=None):  # noqa: ANN001, ANN201
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

    def _fake_loader(domain_name="", cookie_file=None):  # noqa: ANN001, ANN201
        assert cookie_file is None
        return [SimpleNamespace(name="WorkosCursorSessionToken", domain=".cursor.com", value="tok-default")]

    monkeypatch.setattr(cursor_login, "_cookie_loader", lambda browser_cookie3, browser: _fake_loader)
    tokens = cursor_login._read_tokens_with_browser_cookie3(object(), "msedge")
    assert tokens == ["tok-default"]


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
