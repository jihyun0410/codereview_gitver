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


# --- 지난 실행의 리포트가 이번 집계에 섞이면 안 된다 --------------------------------
#
# 실제로 겪은 증상: 컴파일이 깨져 테스트가 한 건도 안 돌았는데 리포트에
# "FAIL / 총 3 / 성공 3 / 실패 0" 이 찍혔다. gradle 은 exit 1 로 끝났고,
# 집계는 지난 실행이 남긴 XML 을 그대로 더한 값이었다.
COMPILE_FAILURE = """\
> Task :compileTestJava FAILED
/repo/src/test/java/com/example/demo/FooTest.java:52: error: not a statement
                discount;
                ^
/repo/src/test/java/com/example/demo/FooTest.java:78: error: method endpointTotal() is already defined in class FooTest
    void endpointTotal() {
         ^
2 errors

FAILURE: Build failed with an exception.
"""


def _stale_report(project: Path, class_fqcn: str, tests: int = 3) -> Path:
    junit = project / "build" / "test-results" / "test"
    junit.mkdir(parents=True, exist_ok=True)
    path = junit / f"TEST-{class_fqcn}.xml"
    path.write_text(
        f'<?xml version="1.0"?><testsuite name="{class_fqcn}" tests="{tests}" '
        'failures="0" errors="0" skipped="0"/>',
        encoding="utf-8",
    )
    return path


def test_a_compile_failure_does_not_inherit_the_previous_pass_count(project, monkeypatch):
    """컴파일이 깨졌으면 집계는 0 이어야 한다 — 지난 성공을 물려받으면 안 된다."""
    _stale_report(project, "com.example.demo.FooTest")
    monkeypatch.setattr(executor, "_run", lambda c, cwd, t: (1, COMPILE_FAILURE))
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.exit_code == 1
    assert (result.total, result.passed, result.failed) == (0, 0, 0)
    assert result.tests_ran is False


def test_a_compile_failure_says_why(project, monkeypatch):
    """'실패 0건인데 FAIL' 로 끝나지 않도록 원인을 남긴다."""
    monkeypatch.setattr(executor, "_run", lambda c, cwd, t: (1, COMPILE_FAILURE))
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.build_errors == [
        "FooTest.java:52: not a statement",
        "FooTest.java:78: method endpointTotal() is already defined in class FooTest",
    ]


def test_other_test_classes_are_not_counted(project, monkeypatch):
    """`--tests *FooTest` 로 한 클래스만 돌리므로 나머지는 갱신되지도 않는다."""
    _stale_report(project, "com.example.demo.OtherTest", tests=7)
    _fake_gradle(monkeypatch, project)

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.total == 3               # FooTest 의 3건만 (OtherTest 7건은 제외)
    assert (project / "build" / "test-results" / "test"
            / "TEST-com.example.demo.OtherTest.xml").is_file(), "남의 리포트는 지우지 않는다"


def test_stale_jacoco_is_not_reported_as_this_run(project, monkeypatch):
    """커버리지도 지난 실행 값이 남아 이번 결과로 둔갑하면 안 된다."""
    jacoco = project / "build" / "reports" / "jacoco" / "test"
    jacoco.mkdir(parents=True)
    (jacoco / "jacocoTestReport.xml").write_text(JACOCO_XML, encoding="utf-8")

    monkeypatch.setattr(executor, "_run", lambda c, cwd, t: (1, COMPILE_FAILURE))
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.coverage is None


def test_a_successful_run_reports_no_build_errors(project, monkeypatch):
    _fake_gradle(monkeypatch, project, returncode=0)

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.build_errors == []
    assert result.tests_ran is True


def test_test_failures_are_not_mistaken_for_build_errors(project, monkeypatch):
    """테스트가 돌았는데 깨진 것은 빌드 실패가 아니다."""
    _fake_gradle(monkeypatch, project, returncode=1)

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.exit_code == 1
    assert result.failed == 1
    assert result.build_errors == []       # 원인이 테스트 실패로 이미 설명된다
    assert result.tests_ran is True


def test_falls_back_to_gradles_own_reason(project, monkeypatch):
    """컴파일 오류가 아닌 실패(의존성 해석 등)는 gradle 설명을 가져온다."""
    output = (
        "FAILURE: Build failed with an exception.\n"
        "\n"
        "* What went wrong:\n"
        "Could not resolve all files for configuration ':testRuntimeClasspath'.\n"
        "> Could not find org.junit:junit-bom:5.10.0.\n"
        "\n"
        "* Try:\n"
        "Run with --stacktrace.\n"
    )
    monkeypatch.setattr(executor, "_run", lambda c, cwd, t: (1, output))
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")

    result = run_tests(project, SOURCE, TEST_PATH)

    assert result.build_errors == [
        "Could not resolve all files for configuration ':testRuntimeClasspath'.",
        "> Could not find org.junit:junit-bom:5.10.0.",
    ]


def test_build_errors_reach_the_report_payload(project, monkeypatch):
    """MCP 로 보내는 dict 에 실려야 리포트까지 도달한다."""
    monkeypatch.setattr(executor, "_run", lambda c, cwd, t: (1, COMPILE_FAILURE))
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/gradle")

    payload = run_tests(project, SOURCE, TEST_PATH).to_dict()

    assert payload["build_errors"]
    assert payload["total"] == 0
