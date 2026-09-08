"""커밋된 소스 수집 검증 (codetest project register 가 MCP 로 올리는 것)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codetest.git_local import (
    MAX_SNAPSHOT_FILES,
    collect_committed_files,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "kim")
    return tmp_path


def _write(repo: Path, path: str, content: str = "x") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_collects_committed_sources(repo):
    _write(repo, "src/main/java/A.java", "class A {}")
    _write(repo, "build.gradle", "plugins {}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    collected, warnings = collect_committed_files(repo)

    assert dict(collected) == {
        "src/main/java/A.java": "class A {}",
        "build.gradle": "plugins {}",
    }
    assert warnings == []


def test_uncommitted_files_are_not_included(repo):
    """등록 스냅샷은 '커밋된' 코드다. 미커밋분은 실행 때 따로 간다."""
    _write(repo, "src/main/java/A.java", "class A {}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    _write(repo, "src/main/java/B.java", "class B {}")      # 커밋 안 함

    collected, _ = collect_committed_files(repo)

    assert "src/main/java/A.java" in dict(collected)
    assert "src/main/java/B.java" not in dict(collected)


def test_skips_binaries_and_unsupported_types(repo):
    _write(repo, "src/main/java/A.java", "class A {}")
    _write(repo, "logo.png", "binary-ish")
    _write(repo, "notes.txt", "메모")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    paths = dict(collect_committed_files(repo)[0])

    assert "src/main/java/A.java" in paths
    assert "logo.png" not in paths
    assert "notes.txt" not in paths


def test_excludes_agent_artifacts(repo):
    """생성한 test.txt 가 다시 대상이 되면 자기 오염이 난다."""
    _write(repo, "src/main/java/A.java", "class A {}")
    _write(repo, "src/test/test.txt", "class GeneratedTest {}")
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-qm", "init")

    paths = dict(collect_committed_files(repo)[0])

    assert "src/main/java/A.java" in paths
    assert "src/test/test.txt" not in paths


def test_warns_and_truncates_when_too_many_files(repo):
    for i in range(5):
        _write(repo, f"src/main/java/F{i}.java", f"class F{i} {{}}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    collected, warnings = collect_committed_files(repo, max_files=3)

    assert len(collected) == 3
    assert any("앞 3개만" in w for w in warnings)


def test_warns_when_total_size_exceeds_the_cap(repo):
    for i in range(4):
        _write(repo, f"src/main/java/F{i}.java", "x" * 1000)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    collected, warnings = collect_committed_files(repo, max_total_bytes=2500)

    assert len(collected) < 4
    assert any("상한" in w for w in warnings)


def test_default_caps_are_sane():
    assert MAX_SNAPSHOT_FILES >= 100
