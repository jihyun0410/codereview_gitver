"""MCP 클라이언트 검증 — 서버 없이 전송 봉투와 응답 파싱만 확인한다."""

from __future__ import annotations

import json

from codetest import api_client
from codetest.api_client import AgentClient, _result_payload, _sse_messages


def test_sse_messages_parses_data_lines():
    body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n'
        "\n"
    )
    messages = _sse_messages(body)
    assert [m.get("id") for m in messages] == [1, None]
    assert messages[0]["result"] == {"ok": True}


def test_result_payload_prefers_structured_then_json_text():
    assert _result_payload({"structuredContent": {"id": "p1"}}) == {"id": "p1"}
    assert _result_payload({"content": [{"type": "text", "text": '{"id": "p2"}'}]}) == {"id": "p2"}
    # JSON 이 아니면 원문을 그대로 넘긴다
    assert _result_payload({"content": [{"type": "text", "text": "hi"}]}) == {"text": "hi"}


class _FakeResponse:
    def __init__(self, payload: dict | None):
        self.status_code = 202 if payload is None else 200
        self.headers = {"content-type": "application/json", "mcp-session-id": "sess-1"}
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()
        self.reason_phrase = "OK"

    def json(self):
        return json.loads(self.text)


class _FakeClient:
    """httpx.Client 대역 — 보낸 요청을 모아두고 정해진 응답을 돌려준다."""

    sent: list[dict] = []
    headers_sent: list[dict] = []

    def __init__(self, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        _FakeClient.sent.append(json)
        _FakeClient.headers_sent.append(headers or {})
        if json.get("method") == "initialize":
            return _FakeResponse({"jsonrpc": "2.0", "id": json["id"],
                                  "result": {"protocolVersion": "2025-06-18"}})
        if "id" not in json:  # notifications/initialized
            return _FakeResponse(None)
        return _FakeResponse({
            "jsonrpc": "2.0", "id": json["id"],
            "result": {"content": [{"type": "text", "text": '{"id":"p9","name":"demo"}'}]},
        })


def test_register_does_handshake_then_tools_call(monkeypatch):
    _FakeClient.sent.clear()
    _FakeClient.headers_sent.clear()
    monkeypatch.setattr(api_client.httpx, "Client", _FakeClient)

    client = AgentClient("http://server/mcp/abc")
    result = client.create_project(name="demo", git_url="git@x", owner="me")

    methods = [m.get("method") for m in _FakeClient.sent]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]

    call = _FakeClient.sent[-1]
    assert call["params"]["name"] == "register_project"      # 경로가 아니라 도구 이름
    assert call["params"]["arguments"]["git_url"] == "git@x"
    assert "github_token" not in call["params"]["arguments"]

    # 핸드셰이크에서 받은 세션 id 가 이후 요청 헤더에 실린다
    assert _FakeClient.headers_sent[-1]["Mcp-Session-Id"] == "sess-1"
    assert "text/event-stream" in _FakeClient.headers_sent[-1]["Accept"]

    assert result == {"id": "p9", "name": "demo"}
