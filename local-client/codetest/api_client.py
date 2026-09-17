"""
Agent MCP 클라이언트.

서버는 REST API 가 아니라 **MCP(Model Context Protocol) 서버**다.
따라서 일반적인 REST 호출과 구성이 다르다.

  REST                                  MCP (Streamable HTTP)
  ────────────────────────────────      ──────────────────────────────────────
  기능마다 경로가 다름                  경로는 단 하나(엔드포인트 URL) 뿐
  POST /register_project                POST <엔드포인트>  본문에 tools/call
  본문 = 그대로 인자                    본문 = JSON-RPC 2.0 봉투
  바로 호출                             initialize 핸드셰이크 후에 호출 가능
  응답 = JSON                           응답 = JSON 또는 SSE(text/event-stream)

`/register_project` 같은 하위 경로로 보내면 서버에 그런 경로가 없으므로
404 가 돌아온다. 아래 클라이언트는 모든 호출을 엔드포인트 하나로 보내고,
`register_project` 등은 **도구 이름**으로 전달한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

#: LLM 생성 + Gradle 빌드가 겹치면 수 분이 걸린다
DEFAULT_TIMEOUT = 300.0
#: 테스트 실행까지 포함하는 호출(run/execute)의 기본 대기 시간
EXECUTE_TIMEOUT = 1200.0

#: 클라이언트가 제안하는 MCP 프로토콜 버전 (서버가 다른 버전을 고르면 그것을 따른다)
PROTOCOL_VERSION = "2025-06-18"


class ApiError(RuntimeError):
    """서버가 오류를 반환했거나 연결에 실패한 경우."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AgentClient:
    """MCP 서버에 도구 호출(tools/call)을 보내는 클라이언트."""

    def __init__(self, server_url: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT) -> None:
        #: MCP 는 하위 경로가 없다 — 이 URL 하나로 모든 요청을 보낸다
        self.endpoint = server_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session_id: str | None = None
        self._protocol_version = PROTOCOL_VERSION
        self._initialized = False
        self._next_id = 0

    # --- 표시용 ---------------------------------------------------------
    def describe(self, tool: str) -> str:
        """터미널에 찍을 '어디로 무엇을 보내는지' 한 줄."""
        return f"{self.endpoint}  (MCP tool: {tool})"

    # --- 전송 계층 ------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # MCP 서버는 두 형식 중 하나로 답할 수 있으므로 둘 다 받겠다고 알린다
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _post(self, payload: dict, timeout: float | None = None) -> dict | None:
        effective = timeout or self.timeout
        try:
            with (
                httpx.Client(timeout=effective) as client,
                client.stream(
                    "POST", self.endpoint, headers=self._headers(), json=payload
                ) as response,
            ):
                # 서버가 세션을 발급하면 이후 요청에 계속 실어 보내야 한다
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id

                if response.status_code >= 400:
                    response.read()
                    raise ApiError(
                        f"HTTP {response.status_code}: "
                        f"{response.text[:300] or response.reason_phrase}",
                        response.status_code,
                    )
                # 알림(notification)에는 응답 본문이 없다 (202 Accepted)
                if response.status_code == 202:
                    return None

                if "text/event-stream" in response.headers.get("content-type", ""):
                    messages = _read_sse(response)
                else:
                    response.read()
                    if not response.content:
                        return None
                    body = response.json()
                    messages = body if isinstance(body, list) else [body]
        except httpx.ConnectError as exc:
            raise ApiError(
                f"MCP 서버에 연결할 수 없습니다: {self.endpoint}\n"
                f"  · 서버가 실행 중인지, 사내망/프록시가 필요한지 확인하세요.\n"
                f"  · CODETEST_SERVER_URL 환경변수로 주소를 바꿀 수 있습니다.\n"
                f"  ({exc})"
            ) from None
        except httpx.TimeoutException:
            raise ApiError(f"요청이 시간 초과되었습니다 ({effective:.0f}s).") from None
        except httpx.TransportError as exc:
            raise ApiError(_disconnected(exc, self.endpoint)) from None

        # SSE 에는 알림이 섞여 오므로 내 요청 id 에 대응하는 응답만 고른다
        for message in messages:
            if isinstance(message, dict) and message.get("id") == payload.get("id"):
                return message
        return messages[-1] if messages else None

    def _rpc(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._next_id += 1
        message = self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}},
            timeout,
        )
        if message is None:
            raise ApiError(f"{method}: 서버가 빈 응답을 보냈습니다.")
        if "error" in message:
            error = message["error"] or {}
            raise ApiError(f"{method} 실패 [{error.get('code')}]: {error.get('message')}")
        return message.get("result") or {}

    def _initialize(self) -> None:
        """MCP 는 initialize 핸드셰이크를 마쳐야 도구를 호출할 수 있다."""
        if self._initialized:
            return
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "codetest", "version": "0.1.0"},
            },
            timeout=30.0,
        )
        self._protocol_version = result.get("protocolVersion", PROTOCOL_VERSION)
        self._initialized = True  # 이 시점부터 프로토콜 버전 헤더를 붙인다
        # 초기화 완료 알림 (응답 없음)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, 30.0)

    # --- 도구 호출 ------------------------------------------------------
    def tool_names(self) -> list[str]:
        """서버가 실제로 제공하는 도구 이름 목록."""
        self._initialize()
        return [t.get("name", "?") for t in self._rpc("tools/list", timeout=30.0).get("tools", [])]

    def _call(self, tool: str, arguments: dict, timeout: float | None = None) -> Any:
        self._initialize()
        try:
            result = self._rpc("tools/call", {"name": tool, "arguments": arguments}, timeout)
        except ApiError as exc:
            raise ApiError(f"{exc}{self._tool_hint()}", exc.status_code) from None

        if result.get("isError"):
            raise ApiError(f"'{tool}' 도구 실행 실패: {_result_text(result)}")
        return _result_payload(result)

    def _tool_hint(self) -> str:
        """도구 이름이 틀렸을 때 실제 목록을 같이 보여 준다."""
        try:
            names = self.tool_names()
        except ApiError:
            return ""
        return f"\n  · 서버가 제공하는 도구: {', '.join(names) or '(없음)'}" if names else ""

    # --- 헬스체크 -------------------------------------------------------
    def health(self) -> dict:
        return self._call("hello", {})

    # --- 프로젝트 -------------------------------------------------------
    def create_project(
        self,
        name: str,
        git_url: str,
        owner: str,
        default_branch: str = "main",
        sources: list[dict] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """codetest project register — 커밋된 소스 스냅샷을 함께 올린다.

        sources 는 이미 커밋된 파일 본문이다. 이후 generate/run/test 는 미커밋
        변경분만 보내는데, MCP 가 이 스냅샷 위에 그것을 덮어 "현재 코드" 를 만들어
        Agent 에 넘긴다. 없으면 LLM 이 변경 파일만 보고 구조만으로 테스트를 짜게 된다.
        """
        return self._call(
            "register_project",
            {
                "name": name,
                "git_url": git_url,
                "owner": owner,
                "default_branch": default_branch,
                "sources": sources or [],
            },
            timeout=timeout,
        )

    def delete_project(self, project_id: str) -> None:
        self._call("delete_project", {"project_id": project_id})

    # --- Test Code -----------------------------------------------------
    def generate_tests(self, project_id: str, diff: str, sources: list[dict]) -> dict:
        """codetest generate — 생성만 한다."""
        return self._call(
            "test_generate",
            {"project_id": project_id, "diff": diff, "sources": sources},
        )

    def prepare_test(
        self, project_id: str, test_code: str, base_package: str | None = None
    ) -> dict:
        """1단계 — MCP 가 @SpringBootTest 를 주입하고 저장 경로를 계산한다."""
        return self._call(
            "prepare_test",
            {
                "project_id": project_id,
                "test_code": test_code,
                "base_package": base_package,
            },
            timeout=120.0,
        )

    def report_execution(
        self,
        project_id: str,
        execution: dict,
        test_code: str,
        diff: str = "",
        sources: list[dict] | None = None,
        intent: str = "",
        intent_rationale: str = "",
        timeout: float | None = None,
    ) -> dict:
        """2단계 — 이 PC 에서 돌린 실행 결과를 보내 리포트를 받는다."""
        return self._call(
            "report_execution",
            {
                "project_id": project_id,
                "execution": execution,
                "test_code": test_code,
                "diff": diff,
                "sources": sources or [],
                "intent": intent,
                "intent_rationale": intent_rationale,
            },
            timeout=timeout or EXECUTE_TIMEOUT,
        )


# ===========================================================================
#  연결이 끊겼을 때
# ===========================================================================
def _disconnected(exc: httpx.TransportError, endpoint: str) -> str:
    """전송 도중 끊긴 경우의 안내문.

    `RemoteProtocolError: peer closed connection without sending complete
    message body (incomplete chunked read)` 는 "서버가 본문을 끝맺지 않고
    연결을 닫았다" 는 뜻이다. 우리 쪽 요청이 틀려서가 아니라 **상대가 중간에
    사라진 것**이므로, 고칠 곳은 서버나 그 앞단이다.
    """
    return (
        f"서버가 응답을 끝맺지 않고 연결을 끊었습니다: {endpoint}\n"
        f"  · MCP 서버가 처리 도중 죽었는지 서버 로그를 확인하세요 "
        f"(예외·OOM·재시작).\n"
        f"  · 앞단 프록시(nginx/LB)가 오래 걸리는 요청을 끊었을 수 있습니다 — "
        f"proxy_read_timeout 과 응답 버퍼링(proxy_buffering off)을 확인하세요.\n"
        f"  · 생성/실행은 수 분이 걸립니다. 잠시 뒤 같은 명령을 다시 실행하면 "
        f"대개 이어서 진행됩니다.\n"
        f"  ({type(exc).__name__}: {exc})"
    )


# ===========================================================================
#  응답 해석
# ===========================================================================
def _iter_sse_messages(lines: Iterable[str]) -> Iterator[dict]:
    """SSE 줄을 받는 대로 JSON 메시지로 바꿔 흘려보낸다.

    본문 전체를 모은 뒤 파싱하면 스트림이 잘렸을 때 **이미 도착한 메시지까지**
    함께 버리게 된다. 이벤트 경계(빈 줄)마다 내보내면 그런 일이 없다.
    """
    buffer: list[str] = []

    def _flush() -> Iterator[dict]:
        if not buffer:
            return
        try:
            parsed = json.loads("\n".join(buffer))
        except ValueError:
            parsed = None
        buffer.clear()
        if isinstance(parsed, dict):
            yield parsed

    for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
        elif not line.strip():  # 빈 줄 = 이벤트 경계
            yield from _flush()
    yield from _flush()


def _sse_messages(body: str) -> list[dict]:
    """SSE 본문에서 `data:` 줄만 모아 JSON 메시지 목록으로 만든다."""
    return list(_iter_sse_messages(body.splitlines()))


def _read_sse(response: httpx.Response) -> list[dict]:
    """SSE 를 받다가 끊겨도 **이미 받은 메시지는 살린다**.

    MCP 는 도구 결과를 보낸 *뒤* 스트림을 닫는다. 그 마지막 닫힘만 앞단에서
    잘려도 httpx 는 RemoteProtocolError 를 던지는데, 그때 받아 둔 응답까지
    버리면 멀쩡히 끝난 생성·실행을 실패로 보고하게 된다. 한 줄도 못 받았을
    때만 오류로 올린다.
    """
    messages: list[dict] = []
    try:
        for message in _iter_sse_messages(response.iter_lines()):
            # 한 건씩 담는다 — 통째로 모으면 끊겼을 때 받은 것까지 함께 잃는다
            messages.append(message)  # noqa: PERF402
    except httpx.TransportError:
        if not messages:
            raise
    return messages


def _result_text(result: dict) -> str:
    """도구 결과의 text 블록들을 이어 붙인다."""
    blocks = result.get("content") or []
    return "\n".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _result_payload(result: dict) -> Any:
    """도구 결과를 dict 로 정규화한다 (structuredContent → JSON 텍스트 → 원문)."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    text = _result_text(result)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"text": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}
