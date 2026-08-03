"""
Test Code 저장 / 로컬 실행.

  · 생성된 Test Code 는 프로젝트의 `src/test/test.txt` 에 저장한다.
  · 실행에 필요한 언어·명령은 `.codetest/last_test.json` 에 함께 남겨
    `codetest test` 가 test.txt 만으로도 다시 실행할 수 있게 한다.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

TEST_FILE = Path("src") / "test" / "test.txt"
META_FILE = Path(".codetest") / "last_test.json"
RUN_TIMEOUT = 600

#: 메타가 없을 때 쓰는 기본값 (Python + pytest)
DEFAULT_META = {
    "language": "python",
    "file_extension": ".py",
    "run_command": ["python", "-m", "pytest", "-q", "{file}"],
}


@dataclass
class RunResult:
    output: str
    exit_code: int


def save_test(repo_root: Path, test_code: str, meta: dict) -> Path:
    """Test Code 와 실행 메타를 저장하고 test.txt 경로를 돌려준다."""
    path = repo_root / TEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(test_code, encoding="utf-8")

    meta_path = repo_root / META_FILE
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_test(repo_root: Path) -> tuple[str, dict]:
    """저장된 Test Code 와 실행 메타를 읽는다."""
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


def run_test(repo_root: Path, test_code: str, meta: dict) -> RunResult:
    """
    Test Code 를 임시 파일에 쓴 뒤 실행 명령으로 돌린다.

    test.txt 를 그대로 실행할 수 없는 언어(python/js/…)가 있으므로
    올바른 확장자의 임시 파일을 만들어 넘긴다.
    """
    extension = meta.get("file_extension") or DEFAULT_META["file_extension"]
    command = meta.get("run_command") or DEFAULT_META["run_command"]

    with tempfile.TemporaryDirectory(prefix="codetest-run-", dir=repo_root) as tmp:
        target = Path(tmp) / f"codetest_generated{extension}"
        target.write_text(test_code, encoding="utf-8")
        argv = [str(target) if part == "{file}" else part for part in command]

        try:
            completed = subprocess.run(  # noqa: S603 — 서버가 지정한 테스트 실행 명령
                argv,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return RunResult(
                output=f"실행 파일을 찾을 수 없습니다: {argv[0]}", exit_code=127
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                output=f"테스트가 {RUN_TIMEOUT}초 안에 끝나지 않아 중단했습니다.", exit_code=124
            )

    output = (completed.stdout or "") + (completed.stderr or "")
    return RunResult(output=output.strip(), exit_code=completed.returncode)
