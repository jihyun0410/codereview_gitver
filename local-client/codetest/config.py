"""
로컬 클라이언트 설정.

  · 서버 주소 / API Key : `~/.codetest/config.json` (전역)
  · 등록된 project_id   : `<repo>/.codetest/config.json` (저장소별)

환경변수 CODETEST_SERVER_URL / CODETEST_API_KEY 가 파일보다 우선한다.
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
    server_url: str = "http://localhost:8000"
    api_key: str = ""
    #: 이 저장소에 대응하는 Agent Server 프로젝트 ID
    project_id: str | None = None


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
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
