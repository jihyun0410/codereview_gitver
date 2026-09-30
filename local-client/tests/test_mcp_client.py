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
_timeouts: list = []


def _client(monkeypatch, handler=_handler) -> AgentClient:
    """httpx.Client 가 MockTransport 를 쓰도록 갈아 끼운다."""
    _sent.clear()
    _headers_sent.clear()
    _timeouts.clear()
    real_client = httpx.Client

    def _factory(*args, **kwargs):
        _timeouts.append(kwargs.get("timeout"))
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


def test_register_returns_even_if_the_stream_never_closes(monkeypatch):
    """결과를 보낸 뒤 앞단이 스트림을 닫지 않아도 터미널이 끝나야 한다."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return httpx.Response(200, headers=SSE, content=_sse(
                {"jsonrpc": "2.0", "id": body["id"], "result": {}}))
        if "id" not in body:
            return httpx.Response(202)

        def _stream():
            yield _sse({"jsonrpc": "2.0", "id": body["id"],
                        "result": {"structuredContent": {"id": "p9"}}})
            raise AssertionError("응답을 받은 뒤에도 스트림을 계속 읽었다")

        return httpx.Response(200, headers=SSE, content=_stream())

    assert _client(monkeypatch, handler).create_project(
        name="demo", git_url="git@x", owner="me"
    ) == {"id": "p9"}


_PROGRESS = {"jsonrpc": "2.0", "method": "notifications/progress",
             "params": {"progressToken": "test_generate", "progress": 10}}


def _handshake_then(tool_response):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        _sent.append(body)
        if body.get("method") == "initialize":
            return httpx.Response(200, headers=SSE, content=_sse(
                {"jsonrpc": "2.0", "id": body["id"], "result": {}}))
        if "id" not in body:
            return httpx.Response(202)
        return tool_response(body)
    return handler


def test_heartbeats_are_skipped_and_the_token_is_sent(monkeypatch):
    """MCP 는 오래 걸리는 동안 진행 알림을 흘린다 — 결과만 골라 쓴다."""
    handler = _handshake_then(lambda body: httpx.Response(200, headers=SSE, content=_sse(
        _PROGRESS, _PROGRESS,
        {"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": {"intent": "x"}}},
    )))
    assert _client(monkeypatch, handler).generate_tests("p9", "", []) == {"intent": "x"}
    assert _sent[-1]["params"]["_meta"] == {"progressToken": "test_generate"}


def test_cut_after_heartbeats_but_before_the_answer_is_an_error(monkeypatch):
    """진행 알림만 받고 끊겼으면 결과가 없는 것이다 — 빈 결과로 넘기면 안 된다."""
    handler = _handshake_then(lambda body: _truncated(_PROGRESS))
    with pytest.raises(ApiError, match="연결을 끊었습니다"):
        _client(monkeypatch, handler).generate_tests("p9", "", [])


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


# --- 타임아웃 -------------------------------------------------------------
#
# 핸드셰이크가 30초였을 때, 사내 프록시를 거쳐 MCP 서버가 처음 깨어나는 동안
# "요청이 시간 초과되었습니다 (30s)" 로 끊겼다. 값 자체는 운영에서 조정하므로
# 숫자를 박지 않고 **어느 호출이 어떤 예산을 쓰는지**와 최소 3분 하한만 고정한다.
#: 30초 사고를 되풀이하지 않기 위한 하한
MIN_SHORT_TIMEOUT = 180.0


def test_the_handshake_uses_the_short_budget(monkeypatch):
    _client(monkeypatch).tool_names()

    # initialize / notifications/initialized / tools/list 세 번 모두
    assert _timeouts == [api_client.SHORT_TIMEOUT] * 3
    assert api_client.SHORT_TIMEOUT >= MIN_SHORT_TIMEOUT


def test_each_call_uses_its_own_budget(monkeypatch):
    """생성과 실행은 예산이 다르다 — 서로 덮어쓰면 안 된다."""
    client = _client(monkeypatch)
    client.generate_tests("p1", "diff", [])
    client.report_execution("p1", {"exit_code": 0}, "class T {}")

    # 핸드셰이크(initialize + notifications) 2회 뒤 tools/call 두 건
    assert _timeouts[2:] == [api_client.DEFAULT_TIMEOUT, api_client.EXECUTE_TIMEOUT]
    for budget in (api_client.DEFAULT_TIMEOUT, api_client.EXECUTE_TIMEOUT):
        assert budget >= MIN_SHORT_TIMEOUT


def test_prepare_test_uses_the_short_budget_too(monkeypatch):
    client = _client(monkeypatch)
    client.prepare_test("p1", "class T {}")

    assert _timeouts[2:] == [api_client.SHORT_TIMEOUT]
