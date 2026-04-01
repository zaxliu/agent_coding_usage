from llm_usage.sinks import feishu_bitable


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self) -> dict:
        return self._payload


def test_fetch_tenant_access_token_success(monkeypatch):
    def _fake_post(url, json, timeout):  # noqa: ANN001, ANN201
        return _Resp({"code": 0, "tenant_access_token": "t-abc"})

    monkeypatch.setattr(feishu_bitable.requests, "post", _fake_post)
    token = feishu_bitable.fetch_tenant_access_token("id", "secret")
    assert token == "t-abc"


def test_fetch_tenant_access_token_raises_on_api_error(monkeypatch):
    def _fake_post(url, json, timeout):  # noqa: ANN001, ANN201
        return _Resp({"code": 999, "msg": "bad"})

    monkeypatch.setattr(feishu_bitable.requests, "post", _fake_post)
    try:
        feishu_bitable.fetch_tenant_access_token("id", "secret")
    except RuntimeError as exc:
        assert "feishu auth error" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_fetch_first_table_id_success(monkeypatch):
    def _fake_get(url, headers, params, timeout):  # noqa: ANN001, ANN201
        return _Resp({"code": 0, "data": {"items": [{"table_id": "tbl1"}]}})

    monkeypatch.setattr(feishu_bitable.requests, "get", _fake_get)
    table_id = feishu_bitable.fetch_first_table_id("app", "token")
    assert table_id == "tbl1"


def test_fetch_first_table_id_raises_on_empty_items(monkeypatch):
    def _fake_get(url, headers, params, timeout):  # noqa: ANN001, ANN201
        return _Resp({"code": 0, "data": {"items": []}})

    monkeypatch.setattr(feishu_bitable.requests, "get", _fake_get)
    try:
        feishu_bitable.fetch_first_table_id("app", "token")
    except RuntimeError as exc:
        assert "table list is empty" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_fetch_bitable_field_type_map_uses_fields_endpoint(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            self.app_token = app_token
            self.table_id = table_id
            self.bot_token = bot_token

        def _fetch_field_type_map(self):  # noqa: ANN201
            calls.append((self.app_token, self.table_id))
            return {"row_key": 1}

    monkeypatch.setattr(feishu_bitable, "FeishuBitableClient", _Client)
    out = feishu_bitable.fetch_bitable_field_type_map("a", "t", "b")
    assert out == {"row_key": 1}
    assert calls == [("a", "t")]


def test_fetch_first_table_id_permission_error_includes_hint(monkeypatch):
    def _fake_get(url, headers, params, timeout):  # noqa: ANN001, ANN201
        return _Resp({"code": 91403, "msg": "Forbidden: no permission to access this table"})

    monkeypatch.setattr(feishu_bitable.requests, "get", _fake_get)
    try:
        feishu_bitable.fetch_first_table_id("app", "token")
    except RuntimeError as exc:
        text = str(exc)
        assert "feishu list tables error" in text
        assert "hint=" in text
        assert "可编辑权限" in text
    else:
        raise AssertionError("expected RuntimeError")
