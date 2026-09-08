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
            with httpx.Client(timeout=effective) as client:
                response = client.post(self.endpoint, headers=self._headers(), json=payload)
        except httpx.ConnectError as exc:
            raise ApiError(
                f"MCP 서버에 연결할 수 없습니다: {self.endpoint}\n"
                f"  · 서버가 실행 중인지, 사내망/프록시가 필요한지 확인하세요.\n"
                f"  · CODETEST_SERVER_URL 환경변수로 주소를 바꿀 수 있습니다.\n"
                f"  ({exc})"
            ) from None
        except httpx.TimeoutException:
            raise ApiError(f"요청이 시간 초과되었습니다 ({effective:.0f}s).") from None

        # 서버가 세션을 발급하면 이후 요청에 계속 실어 보내야 한다
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        if response.status_code >= 400:
            raise ApiError(
                f"HTTP {response.status_code}: {response.text[:300] or response.reason_phrase}",
                response.status_code,
            )
        # 알림(notification)에는 응답 본문이 없다 (202 Accepted)
        if response.status_code == 202 or not response.content:
            return None

        if "text/event-stream" in response.headers.get("content-type", ""):
            messages = _sse_messages(response.text)
        else:
            body = response.json()
            messages = body if isinstance(body, list) else [body]

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

    def run_tests(
        self,
        project_id: str,
        diff: str,
        sources: list[dict],
        timeout: float | None = None,
    ) -> dict:
        """codetest run — 생성 + @SpringBootTest 실행 + 판정을 한 번에 받는다."""
        return self._call(
            "test_run",
            {"project_id": project_id, "diff": diff, "sources": sources},
            timeout=timeout or EXECUTE_TIMEOUT,
        )

    def execute_tests(
        self,
        project_id: str,
        test_code: str,
        sources: list[dict],
        base_package: str | None = None,
        diff: str = "",
        intent: str = "",
        intent_rationale: str = "",
        timeout: float | None = None,
    ) -> dict:
        """codetest test — src/test/test.txt 의 Test Code 를 실행하고 판정을 받는다.

        diff 는 MCP 가 기능 중요도를 다시 판단하는 데 쓴다. 보내지 않으면 sources 만으로
        판단하므로 변경 구간이 파일 전체로 잡혀 등급이 실제보다 높게 나올 수 있다.

        intent / intent_rationale 은 지난 생성 때 파악한 의도다. 결과값에 함께 표시된다.
        """
        return self._call(
            "execute_tests",
            {
                "project_id": project_id,
                "test_code": test_code,
                "sources": sources,
                "base_package": base_package,
                "diff": diff,
                "intent": intent,
                "intent_rationale": intent_rationale,
            },
            timeout=timeout or EXECUTE_TIMEOUT,
        )


# ===========================================================================
#  응답 해석
# ===========================================================================
def _sse_messages(body: str) -> list[dict]:
    """SSE 본문에서 `data:` 줄만 모아 JSON 메시지 목록으로 만든다."""
    chunks: list[str] = []
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
        elif not line.strip():  # 빈 줄 = 이벤트 경계
            if buffer:
                chunks.append("\n".join(buffer))
                buffer = []
    if buffer:
        chunks.append("\n".join(buffer))

    messages: list[dict] = []
    for chunk in chunks:
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            messages.append(parsed)
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
