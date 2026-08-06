"""
Test Code 저장 / 조회.

정의서
  · "codetest test : 프로젝트 경로에 /src/test/test.txt 파일에 있는 Test Code를
     가져와서 테스트를 실행, report 생성"

테스트 **실행**은 MCP 가 Gradle + JaCoCo 로 수행한다(정의서 [상세] 4).
클라이언트는 생성된 Test Code 를 `src/test/test.txt` 에 남기고, 다시 실행할 때
읽어서 Agent 로 보내는 일만 한다.

실행에 필요한 부가 정보(파악한 의도, 기준 패키지 등)는 `.codetest/last_test.json`
에 함께 남겨 `codetest test` 가 test.txt 만으로도 같은 맥락에서 실행되게 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

TEST_FILE = Path("src") / "test" / "test.txt"
META_FILE = Path(".codetest") / "last_test.json"

#: 메타가 없을 때 쓰는 기본값
DEFAULT_META: dict = {
    "importance": "-",
    "importance_rationale": "",
    "intent": "",
    "intent_rationale": "",
    "thinking": "",
    "test_cases": "",
    "rationale": "",
    "target_code": "",
    "base_package": None,
}


def save_test(repo_root: Path, test_code: str, meta: dict) -> Path:
    """Test Code 와 부가 정보를 저장하고 test.txt 경로를 돌려준다."""
    path = repo_root / TEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(test_code, encoding="utf-8")

    meta_path = repo_root / META_FILE
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_test(repo_root: Path) -> tuple[str, dict]:
    """저장된 Test Code 와 부가 정보를 읽는다."""
    path = repo_root / TEST_FILE
    if not path.is_file():
        raise FileNotFoundError(
            f"{TEST_FILE.as_posix()} 파일이 없습니다. `codetest generate` 로 먼저 생성하세요."
        )
    code = path.read_text(encoding="utf-8")

    meta_path = repo_root / META_FILE
    meta = dict(DEFAULT_META)
    if meta_path.is_file():
        try:
            meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return code, meta


def meta_from_generated(generated: dict) -> dict:
    """생성 응답에서 다음 실행에 필요한 항목만 추린다."""
    return {key: generated.get(key, default) for key, default in DEFAULT_META.items()}
