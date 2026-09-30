"""
Agent 서버 REST 클라이언트.

서버(`src/main.py`)는 command 마다 라우트 하나를 갖는 FastAPI 앱이다.

  CLI command            요청
    (연결 확인)            GET    /hello?name=
    project register       POST   /project/register
    project delete         DELETE /project/{project_id}
    generate               POST   /generate
    run/test 1단계         POST   /prepare
    run/test 2단계         POST   /report

`CODETEST_SERVER_URL` 은 **서버 기본 주소**(경로 없이)다. 프록시를 거치면
`http://<host>:80/agent/<id>` 처럼 접두어까지만 적는다 — 위 경로가 그 뒤에 붙는다.
"""

from __future__ import annotations

from typing import Any

import httpx

#: 핸드셰이크·조회처럼 LLM 이 끼지 않는 짧은 호출의 대기 시간.
SHORT_TIMEOUT = 3000.0
#: LLM 생성 + Gradle 빌드가 겹치면 수 분이 걸린다
DEFAULT_TIMEOUT = 3000.0
#: 테스트 실행까지 포함하는 호출(run/execute)의 기본 대기 시간
EXECUTE_TIMEOUT = 1200.0


class ApiError(RuntimeError):
    """서버가 오류를 반환했거나 연결에 실패한 경우."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _base_url(server_url: str) -> str:
    """예전 MCP 주소(`…/mcp`)가 남아 있어도 기본 주소로 돌려놓는다."""
    base = server_url.rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return base


class AgentClient:
    """Agent 서버에 REST 요청을 보내는 클라이언트."""

    def __init__(self, server_url: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = _base_url(server_url)
        self.api_key = api_key
        self.timeout = timeout

    # --- 표시용 ---------------------------------------------------------
    def describe(self, command: str) -> str:
        """터미널에 찍을 '어디로 무엇을 보내는지' 한 줄."""
        method, path = _ROUTES[command]
        return f"{method} {self.base_url}{path.format(project_id='{project_id}')}"

    # --- 전송 계층 ------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(
        self,
        command: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
        **path_args: str,
    ) -> Any:
        method, path = _ROUTES[command]
        url = f"{self.base_url}{path.format(**path_args)}"
        effective = timeout or self.timeout
        try:
            response = httpx.request(
                method, url, headers=self._headers(), json=json_body,
                params=params, timeout=effective,
            )
        except httpx.ConnectError as exc:
            raise ApiError(
                f"서버에 연결할 수 없습니다: {url}\n"
                f"  · 서버가 실행 중인지, 사내망/프록시가 필요한지 확인하세요.\n"
                f"  · CODETEST_SERVER_URL 환경변수로 주소를 바꿀 수 있습니다.\n"
                f"  ({exc})"
            ) from None
        except httpx.TimeoutException:
            raise ApiError(f"요청이 시간 초과되었습니다 ({effective:.0f}s).") from None
        except httpx.TransportError as exc:
            raise ApiError(_disconnected(exc, url)) from None

        if response.status_code >= 400:
            raise ApiError(
                f"{method} {url} → HTTP {response.status_code}: {_error_text(response)}",
                response.status_code,
            )
        if not response.content:
            return {}
        try:
            parsed = response.json()
        except ValueError:
            return {"text": response.text}
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    # --- 헬스체크 -------------------------------------------------------
    def health(self) -> dict:
        return self._request("hello", params={"name": "codetest"}, timeout=SHORT_TIMEOUT)

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
        변경분만 보내는데, 서버가 이 스냅샷 위에 그것을 덮어 "현재 코드" 를 만든다.
        """
        return self._request(
            "register_project",
            json_body={
                "name": name,
                "git_url": git_url,
                "owner": owner,
                "default_branch": default_branch,
                "sources": sources or [],
            },
            timeout=timeout,
        )

    def delete_project(self, project_id: str) -> None:
        self._request("delete_project", project_id=project_id, timeout=SHORT_TIMEOUT)

    # --- Test Code -----------------------------------------------------
    def generate_tests(self, project_id: str, diff: str, sources: list[dict]) -> dict:
        """codetest generate — 생성만 한다."""
        return self._request(
            "test_generate",
            json_body={"project_id": project_id, "diff": diff, "sources": sources},
        )

    def prepare_test(
        self, project_id: str, test_code: str, base_package: str | None = None
    ) -> dict:
        """1단계 — 서버가 @SpringBootTest 를 주입하고 저장 경로를 계산한다."""
        return self._request(
            "prepare_test",
            json_body={
                "project_id": project_id,
                "test_code": test_code,
                "base_package": base_package,
            },
            timeout=SHORT_TIMEOUT,
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
        return self._request(
            "report_execution",
            json_body={
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


#: command → (HTTP 메서드, 경로). 서버 `src/main.py` 의 라우트와 같아야 한다.
_ROUTES: dict[str, tuple[str, str]] = {
    "hello": ("GET", "/hello"),
    "register_project": ("POST", "/project/register"),
    "delete_project": ("DELETE", "/project/{project_id}"),
    "test_generate": ("POST", "/generate"),
    "prepare_test": ("POST", "/prepare"),
    "report_execution": ("POST", "/report"),
}


def _error_text(response: httpx.Response) -> str:
    """FastAPI 오류 본문(`{"detail": ...}`)을 사람이 읽을 문장으로 만든다."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # 422 검증 오류
        detail = "; ".join(
            f"{'.'.join(str(p) for p in item.get('loc', []))}: {item.get('msg')}"
            for item in detail if isinstance(item, dict)
        )
    return str(detail or response.text[:300] or response.reason_phrase)


def _disconnected(exc: httpx.TransportError, url: str) -> str:
    """전송 도중 끊긴 경우의 안내문.

    `RemoteProtocolError: peer closed connection without sending complete
    message body` 는 "서버가 본문을 끝맺지 않고 연결을 닫았다" 는 뜻이다.
    우리 쪽 요청이 틀려서가 아니라 **상대가 중간에 사라진 것**이다.
    """
    return (
        f"서버가 응답을 끝맺지 않고 연결을 끊었습니다: {url}\n"
        f"  · 서버가 처리 도중 죽었는지 서버 로그를 확인하세요 (예외·OOM·재시작).\n"
        f"  · 앞단 프록시(nginx/LB)가 오래 걸리는 요청을 끊었을 수 있습니다 — "
        f"proxy_read_timeout 을 확인하세요.\n"
        f"  · 생성/실행은 수 분이 걸립니다. 잠시 뒤 같은 명령을 다시 실행하세요.\n"
        f"  ({type(exc).__name__}: {exc})"
    )
