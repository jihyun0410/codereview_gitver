"""MCP 클라이언트 검증 — 서버 없이 전송 봉투와 응답 파싱만 확인한다."""

from __future__ import annotations

import json

import httpx
import pytest

from codetest import api_client
from codetest.api_client import AgentClient, ApiError, _result_payload, _sse_messages


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


SSE = {"content-type": "text/event-stream", "mcp-session-id": "sess-1"}


def _sse(*messages: dict) -> bytes:
    return b"".join(
        b"event: message\ndata: " + json.dumps(m).encode() + b"\n\n" for m in messages
    )


def _handler(request: httpx.Request) -> httpx.Response:
    """MCP 서버 대역 — 핸드셰이크와 tools/call 에 SSE 로 답한다."""
    body = json.loads(request.content)
    _sent.append(body)
    _headers_sent.append(dict(request.headers))

    if body.get("method") == "initialize":
        return httpx.Response(
            200,
            headers=SSE,
            content=_sse({"jsonrpc": "2.0", "id": body["id"],
                          "result": {"protocolVersion": "2025-06-18"}}),
        )
    if "id" not in body:  # notifications/initialized
        return httpx.Response(202, headers={"mcp-session-id": "sess-1"})
    return httpx.Response(
        200,
        headers=SSE,
        content=_sse({
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"content": [{"type": "text", "text": '{"id":"p9","name":"demo"}'}]},
        }),
    )


_sent: list[dict] = []
_headers_sent: list[dict] = []


def _client(monkeypatch, handler=_handler) -> AgentClient:
    """httpx.Client 가 MockTransport 를 쓰도록 갈아 끼운다."""
    _sent.clear()
    _headers_sent.clear()
    real_client = httpx.Client

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(api_client.httpx, "Client", _factory)
    return AgentClient("http://server/mcp/abc")


def test_register_does_handshake_then_tools_call(monkeypatch):
    result = _client(monkeypatch).create_project(name="demo", git_url="git@x", owner="me")

    methods = [m.get("method") for m in _sent]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]

    call = _sent[-1]
    assert call["params"]["name"] == "register_project"      # 경로가 아니라 도구 이름
    assert call["params"]["arguments"]["git_url"] == "git@x"
    assert "github_token" not in call["params"]["arguments"]

    # 핸드셰이크에서 받은 세션 id 가 이후 요청 헤더에 실린다
    assert _headers_sent[-1]["mcp-session-id"] == "sess-1"
    assert "text/event-stream" in _headers_sent[-1]["accept"]

    assert result == {"id": "p9", "name": "demo"}


# --- 응답 도중 연결이 끊긴 경우 -------------------------------------------
#
# `RemoteProtocolError: peer closed connection without sending complete message
# body (incomplete chunked read)` — 서버가 본문을 끝맺지 않고 닫았다는 뜻이다.
# httpx 의 ConnectError 도 TimeoutException 도 아니라서 예전에는 그대로 새어
# 나가 터미널에 파이썬 트레이스백이 찍혔다.
def _truncated(*messages: dict):
    """메시지를 보낸 뒤 종료 청크 없이 끊기는 스트림."""
    def _stream():
        if messages:
            yield _sse(*messages)
        raise httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)"
        )

    return httpx.Response(200, headers=SSE, content=_stream())


def test_result_survives_a_stream_that_is_cut_after_the_answer(monkeypatch):
    """MCP 는 결과를 보낸 뒤에 스트림을 닫는다 — 그 닫힘만 잘려도 결과는 살린다."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        _sent.append(body)
        if body.get("method") == "initialize":
            return httpx.Response(200, headers=SSE, content=_sse(
                {"jsonrpc": "2.0", "id": body["id"], "result": {}}))
        if "id" not in body:
            return httpx.Response(202)
        return _truncated({
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"structuredContent": {"id": "p9"}},
        })

    assert _client(monkeypatch, handler).create_project(
        name="demo", git_url="git@x", owner="me"
    ) == {"id": "p9"}


def test_cut_before_any_answer_becomes_a_readable_api_error(monkeypatch):
    """한 줄도 못 받았으면 트레이스백 대신 무엇을 확인할지 알려 준다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _truncated()

    with pytest.raises(ApiError) as exc:
        _client(monkeypatch, handler).tool_names()

    message = str(exc.value)
    assert "연결을 끊었습니다" in message
    assert "RemoteProtocolError" in message          # 원인을 지우지는 않는다
    assert "proxy_read_timeout" in message           # 어디를 볼지 알려 준다
