"""
로컬 클라이언트 설정.

  · 서버 주소 / API Key : `~/.codetest/config.json` (전역)
  · 등록된 project_id   : `<repo>/.codetest/config.json` (저장소별)
  · 프로젝트 구조 덮어쓰기: `<repo>/.codetest/config.json` (저장소별)

환경변수 CODETEST_SERVER_URL / CODETEST_API_KEY 가 파일보다 우선한다.

프로젝트 구조(module / test_source_root)는 **평소에 적을 필요가 없다** —
`project_layout` 이 실제 디렉터리를 보고 정한다. 빌드 스크립트에서 소스 경로를
직접 바꾼 프로젝트에서만 탈출구로 쓴다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".codetest" / "config.json"
LOCAL_CONFIG_RELPATH = Path(".codetest") / "config.json"


@dataclass
class Config:
    server_url: str = "http://maxis-proxy.mks01.skhynix.com:80/mcp/999c9f67-4571-4736-aed4-fe61ded3f835"
    api_key: str = ""
    #: 이 저장소에 대응하는 Agent Server 프로젝트 ID
    project_id: str | None = None

    # --- 프로젝트 구조 (보통은 비워 둔다. 자동 탐지가 맞지 않을 때만 쓴다) ---
    #: 테스트를 넣을 빌드 모듈 (저장소 기준 상대 경로, 예: "api")
    module: str = ""
    #: 테스트 소스 루트 (저장소 기준 상대 경로, 예: "api/src/test/java").
    #: 빌드 스크립트에서 sourceSets 를 직접 바꾼 프로젝트용 탈출구다.
    test_source_root: str = ""
    #: gradlew/mvnw 가 없을 때 쓸 실행 파일
    gradle_command: str = "gradle"
    maven_command: str = "mvn"


def _read_json(path: Path) -> dict:
    """설정 JSON 을 읽는다. 못 읽으면 빈 dict.

    인코딩은 utf-8-sig 로 읽는다. 이 파일은 Python 만 쓰는 게 아니라
    codetest.ps1 이나 사용자가 메모장/IDE 로도 건드린다. Windows PowerShell 5.1 의
    `Set-Content -Encoding UTF8` 은 BOM 을 붙이는데, 순수 utf-8 로 읽으면 BOM 때문에
    JSONDecodeError 가 나고 project_id 가 없는 것처럼 보인다.
    utf-8-sig 는 BOM 이 있으면 벗기고 없으면 그대로 읽으므로 양쪽 다 처리한다.
    """
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}


def load(repo_root: Path | None = None) -> Config:
    """전역 → 저장소별 → 환경변수 순으로 덮어쓴다."""
    merged: dict = dict(_read_json(GLOBAL_CONFIG_PATH))
    if repo_root is not None:
        merged.update(_read_json(repo_root / LOCAL_CONFIG_RELPATH))

    if os.getenv("CODETEST_SERVER_URL"):
        merged["server_url"] = os.environ["CODETEST_SERVER_URL"]
    if os.getenv("CODETEST_API_KEY"):
        merged["api_key"] = os.environ["CODETEST_API_KEY"]

    known = set(Config.__dataclass_fields__)
    return Config(**{k: v for k, v in merged.items() if k in known})


def save_project_id(repo_root: Path, project_id: str | None) -> Path:
    """저장소별 설정에 project_id 를 기록/삭제한다."""
    path = repo_root / LOCAL_CONFIG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_json(path)
    if project_id is None:
        payload.pop("project_id", None)
    else:
        payload["project_id"] = project_id
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def save_global(config: Config) -> Path:
    """전역 설정(서버 주소·API Key) 저장."""
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    payload.pop("project_id", None)  # project_id 는 저장소별 설정에만 둔다
    GLOBAL_CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:  # API Key 가 담기므로 소유자만 읽도록 (POSIX)
        GLOBAL_CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
    return GLOBAL_CONFIG_PATH
