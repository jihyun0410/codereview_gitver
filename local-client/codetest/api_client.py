"""
Agent REST API 클라이언트.

로컬 클라이언트는 **오직 이 클래스를 통해서만** Agent 와 통신한다.
Agent 는 LLM 판단을, MCP 는 코드 기반 처리(AST·@SpringBootTest 주입·JaCoCo 실행)를
담당하며 둘 사이의 송/수신은 Agent 가 처리하므로 클라이언트는 Agent 만 알면 된다.
"""

from __future__ import annotations

from typing import Any

import httpx

#: LLM 생성 + Gradle 빌드가 겹치면 수 분이 걸린다
DEFAULT_TIMEOUT = 300.0
#: 테스트 실행까지 포함하는 호출(run/execute)의 기본 대기 시간
EXECUTE_TIMEOUT = 1200.0


class ApiError(RuntimeError):
    """서버가 4xx/5xx 를 반환했거나 연결에 실패한 경우."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AgentClient:
    def __init__(self, server_url: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = server_url.rstrip("/") + "/api/v1"
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, timeout: float | None = None, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        effective = timeout or self.timeout
        try:
            with httpx.Client(timeout=effective) as client:
                response = client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.ConnectError as exc:
            raise ApiError(
                f"Agent Server 에 연결할 수 없습니다: {self.base_url}\n"
                f"  · 서버가 실행 중인지 확인하세요.\n"
                f"  · CODETEST_SERVER_URL 환경변수로 주소를 바꿀 수 있습니다.\n"
                f"  ({exc})"
            ) from None
        except httpx.TimeoutException:
            raise ApiError(f"요청이 시간 초과되었습니다 ({effective:.0f}s).") from None

        if response.status_code >= 400:
            raise ApiError(_extract_detail(response), response.status_code)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/health")

    # --- 프로젝트 ------------------------------------------------------
    def create_project(
        self,
        name: str,
        git_url: str,
        owner: str,
        github_token: str | None = None,
        default_branch: str = "main",
    ) -> dict:
        return self._request(
            "POST",
            "/projects",
            json={
                "name": name,
                "git_url": git_url,
                "owner": owner,
                "github_token": github_token,
                "default_branch": default_branch,
            },
        )

    def delete_project(self, project_id: str) -> None:
        self._request("DELETE", f"/projects/{project_id}")

    # --- Test Code -----------------------------------------------------
    def generate_tests(
        self, project_id: str, diff: str, sources: list[dict], scope: str
    ) -> dict:
        """codetest generate — 생성만 한다."""
        return self._request(
            "POST",
            "/tests/generate",
            json={
                "project_id": project_id,
                "diff": diff,
                "sources": sources,
                "scope": scope,
            },
        )

    def run_tests(
        self,
        project_id: str,
        diff: str,
        sources: list[dict],
        scope: str,
        timeout: float | None = None,
    ) -> dict:
        """codetest run — 생성 + @SpringBootTest 실행 + 판정을 한 번에 받는다."""
        return self._request(
            "POST",
            "/tests/run",
            timeout=timeout or EXECUTE_TIMEOUT,
            json={
                "project_id": project_id,
                "diff": diff,
                "sources": sources,
                "scope": scope,
            },
        )

    def execute_tests(
        self,
        project_id: str,
        test_code: str,
        sources: list[dict],
        base_package: str | None = None,
        intent: str = "",
        intent_rationale: str = "",
        timeout: float | None = None,
    ) -> dict:
        """codetest test — src/test/test.txt 의 Test Code 를 실행하고 판정을 받는다."""
        return self._request(
            "POST",
            "/tests/execute",
            timeout=timeout or EXECUTE_TIMEOUT,
            json={
                "project_id": project_id,
                "test_code": test_code,
                "sources": sources,
                "base_package": base_package,
                "intent": intent,
                "intent_rationale": intent_rationale,
            },
        )


def _extract_detail(response: httpx.Response) -> str:
    """FastAPI 오류 응답에서 사람이 읽을 메시지를 뽑는다."""
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:300]}"

    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, list):  # pydantic 검증 오류
        parts = [
            f"{'.'.join(str(x) for x in item.get('loc', []))}: {item.get('msg')}"
            for item in detail
        ]
        return f"HTTP {response.status_code}: " + " / ".join(parts)
    return f"HTTP {response.status_code}: {detail or response.text[:300]}"
