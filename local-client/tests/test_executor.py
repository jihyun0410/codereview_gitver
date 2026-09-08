"""로컬 Gradle 실행기 검증.

여기는 서버 사본이 아니라 **사용자의 실제 작업 트리**다. 그래서 파일 복구 동작이
가장 중요하다 — 미커밋 변경분을 건드리면 안 된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codetest import executor
from codetest.executor import ExecutionError, run_tests

TEST_PATH = "src/test/java/com/example/demo/FooTest.java"
SOURCE = "package com.example.demo;\nclass FooTest {}\n"

JUNIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.example.demo.FooTest" tests="3" failures="1" errors="0" skipped="0">
  <testcase classname="com.example.demo.FooTest" name="ok"/>
  <testcase classname="com.example.demo.FooTest" name="boundary"/>
  <testcase classname="com.example.demo.FooTest" name="bad">
    <failure message="expected 1 but was 2">assertion failed</failure>
  </testcase>
</testsuite>
"""

JACOCO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<report name="demo">
  <counter type="BRANCH" missed="1" covered="3"/>
  <counter type="LINE" missed="2" covered="18"/>
</report>
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "build.gradle").write_text("plugins { id 'java'; id 'jacoco' }", encoding="utf-8")
    return tmp_path


def _fake_gradle(monkeypatch, project: Path, returncode: int = 0, write_reports: bool = True):
    """gradle 을 대신해 리포트만 만들어 둔다."""
    calls: list[list[str]] = []

    def _run(command, cwd, timeout):
        calls.append(command)
        if write_reports:
            junit = project / "build" / "test-results" / "test"
            junit.mkdir(parents=True, exist_ok=True)
            (junit / "TEST-com.example.demo.FooTest.xml").write_text(JUNIT_XML, encoding="utf-8")
            jacoco = project / "build" / "reports" / "jacoco" / "test"
            jacoco.mkdir(parents=True, exist_ok=True)
            (jacoco / "jacocoTestReport.xml").write_text(JACOCO_XML, encoding="utf-8")
        return returncode, "BUILD OUTPUT"

    monkeypatch.setattr(executor, "_run", _run)
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")
    return calls


# --- 결과 수집 ----------------------------------------------------------------
def test_collects_junit_and_jacoco(project, monkeypatch):
    _fake_gradle(monkeypatch, project)

    result = run_tests(project, SOURCE, TEST_PATH)

    assert (result.total, result.failed, result.passed) == (3, 1, 2)
    assert result.failures == ["com.example.demo.FooTest.bad: expected 1 but was 2"]
    assert result.jacoco_enabled is True
    assert result.coverage["line_rate"] == 90.0        # 18 / (18+2)
    assert result.coverage["branch_rate"] == 75.0      # 3 / (3+1)


def test_runs_only_this_test_class(project, monkeypatch):
    calls = _fake_gradle(monkeypatch, project)
    run_tests(project, SOURCE, TEST_PATH)

    command = calls[0]
    assert "--tests" in command
    assert command[command.index("--tests") + 1] == "*FooTest"
    assert "jacocoTestReport" in command       # build.gradle 에 jacoco 가 있으므로


def test_skips_jacoco_task_when_plugin_is_absent(tmp_path, monkeypatch):
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }", encoding="utf-8")
    calls = _fake_gradle(monkeypatch, tmp_path, write_reports=False)

    result = run_tests(tmp_path, SOURCE, TEST_PATH)

    assert "jacocoTestReport" not in calls[0]
    assert result.jacoco_enabled is False


# --- 작업 트리 보호 (가장 중요) -------------------------------------------------
def test_removes_the_test_file_it_created(project, monkeypatch):
    _fake_gradle(monkeypatch, project)
    run_tests(project, SOURCE, TEST_PATH)

    assert not (project / TEST_PATH).exists()


def test_restores_a_pre_existing_test_file(project, monkeypatch):
    """이미 있던 파일이면 원래 내용으로 되돌린다."""
    target = project / TEST_PATH
    target.parent.mkdir(parents=True)
    target.write_text("class FooTest { /* 사용자가 쓰던 것 */ }", encoding="utf-8")
    _fake_gradle(monkeypatch, project)

    run_tests(project, SOURCE, TEST_PATH)

    assert "사용자가 쓰던 것" in target.read_text(encoding="utf-8")


def test_never_touches_other_files(project, monkeypatch):
    """미커밋 변경분을 날리면 안 된다 — 서버 사본과 다른 점이다."""
    mine = project / "src" / "main" / "java" / "com" / "example" / "demo" / "OrderService.java"
    mine.parent.mkdir(parents=True)
    mine.write_text("class OrderService { /* 아직 커밋 안 한 내 변경 */ }", encoding="utf-8")
    _fake_gradle(monkeypatch, project)

    run_tests(project, SOURCE, TEST_PATH)

    assert "아직 커밋 안 한 내 변경" in mine.read_text(encoding="utf-8")


def test_restores_even_when_gradle_fails(project, monkeypatch):
    def _boom(command, cwd, timeout):
        raise ExecutionError("gradle 폭발")

    monkeypatch.setattr(executor, "_run", _boom)
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")

    with pytest.raises(ExecutionError):
        run_tests(project, SOURCE, TEST_PATH)

    assert not (project / TEST_PATH).exists()


# --- 안전장치 ------------------------------------------------------------------
def test_refuses_to_write_outside_the_project(project, monkeypatch):
    _fake_gradle(monkeypatch, project)
    with pytest.raises(ExecutionError, match="프로젝트 밖"):
        run_tests(project, SOURCE, "../../etc/evil.java")


def test_reports_missing_gradle(project, monkeypatch):
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    with pytest.raises(ExecutionError, match="Gradle 을 찾을 수 없습니다"):
        run_tests(project, SOURCE, TEST_PATH)


def test_prefers_the_wrapper_over_system_gradle(project, monkeypatch):
    (project / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    calls = _fake_gradle(monkeypatch, project)

    run_tests(project, SOURCE, TEST_PATH)

    assert calls[0][:2] == ["sh", str(project / "gradlew")]
