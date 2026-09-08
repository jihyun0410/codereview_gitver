"""
로컬 Gradle 실행기.

정의서: "JaCoCo와 @SpringBootTest 를 사용하여 Test Code 실행"

테스트는 **명령을 입력한 이 PC 의 프로젝트에서** 돌린다. 개발자가 방금 고친
코드가 그대로 들어 있는 작업 트리라 별도 사본을 만들거나 변경분을 덮어쓸 필요가
없다. `@SpringBootTest` 주입은 코드 기반 작업이라 MCP 가 해 주고, 여기서는 그
결과를 받아 파일로 쓰고 gradle 을 돌린 뒤 집계만 한다.

**복구 주의**: 서버 사본과 달리 여기는 사용자의 실제 작업 트리다.
`git checkout -- .` 같은 되돌리기는 미커밋 변경분을 날리므로 절대 하지 않는다.
이 실행이 새로 만든 파일만 지운다.
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

#: JaCoCo / JUnit 리포트 위치 (Gradle 기본값)
_JACOCO_XML = Path("build") / "reports" / "jacoco" / "test" / "jacocoTestReport.xml"
_JUNIT_DIR = Path("build") / "test-results" / "test"
_BUILD_FILES = ("build.gradle", "build.gradle.kts", "pom.xml")

#: gradle 출력이 리포트에 통째로 실리지 않도록 자른다
_OUTPUT_LIMIT = 20_000


class ExecutionError(RuntimeError):
    """테스트를 실행할 수 없는 상태 (Gradle 부재, 경로 오류 등)."""


@dataclass
class Coverage:
    line_covered: int = 0
    line_missed: int = 0
    branch_covered: int = 0
    branch_missed: int = 0

    @staticmethod
    def _rate(covered: int, missed: int) -> float:
        total = covered + missed
        return round(covered / total * 100, 2) if total else 0.0

    def to_dict(self) -> dict:
        return {
            "line_covered": self.line_covered,
            "line_missed": self.line_missed,
            "line_rate": self._rate(self.line_covered, self.line_missed),
            "branch_covered": self.branch_covered,
            "branch_missed": self.branch_missed,
            "branch_rate": self._rate(self.branch_covered, self.branch_missed),
        }


@dataclass
class ExecutionResult:
    """실행의 객관적 사실. 적절성 판단은 Agent 가 한다."""

    exit_code: int = 0
    output: str = ""
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)
    coverage: dict | None = None
    jacoco_enabled: bool = False
    springboot_applied: bool = False
    applied: list[str] = field(default_factory=list)
    test_file_path: str = ""
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "output": self.output,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total": self.total,
            "failures": self.failures,
            "coverage": self.coverage,
            "jacoco_enabled": self.jacoco_enabled,
            "springboot_applied": self.springboot_applied,
            "applied": self.applied,
            "test_file_path": self.test_file_path,
            "command": self.command,
        }


def run_tests(
    repo_root: Path,
    test_source: str,
    test_file_path: str,
    springboot_applied: bool = True,
    applied: list[str] | None = None,
    gradle_command: str = "gradle",
    timeout: int = 900,
) -> ExecutionResult:
    """
    이 PC 의 프로젝트에서 @SpringBootTest 를 실행한다.

    :param test_source:    MCP 가 @SpringBootTest 주입을 마친 Java 소스
    :param test_file_path: 저장소 루트 기준 상대 경로 (MCP 가 계산해 준 값)
    """
    if not repo_root.is_dir():
        raise ExecutionError(f"프로젝트 경로가 없습니다: {repo_root}")

    target = _safe_target(repo_root, test_file_path)
    existed = target.is_file()
    previous = target.read_text(encoding="utf-8") if existed else None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test_source, encoding="utf-8")

        command = _build_command(repo_root, test_file_path, gradle_command)
        returncode, output = _run(command, repo_root, timeout)

        result = ExecutionResult(
            exit_code=returncode,
            output=_clip(output),
            test_file_path=test_file_path,
            springboot_applied=springboot_applied,
            applied=list(applied or []),
            command=command,
            jacoco_enabled=_has_jacoco(repo_root),
        )
        _collect_junit(repo_root, result)
        _collect_jacoco(repo_root, result)
        return result
    finally:
        # 실제 작업 트리다. 우리가 만든 것만 되돌린다.
        _restore(target, previous, existed)


# ---------------------------------------------------------------------------
#  실행
# ---------------------------------------------------------------------------
def _class_name(test_file_path: str) -> str:
    return Path(test_file_path).stem


def _build_command(repo_root: Path, test_file_path: str, gradle_command: str) -> list[str]:
    """이 테스트 클래스만 돌리고 JaCoCo 리포트까지 만든다."""
    launcher = _gradle_launcher(repo_root, gradle_command)
    command = [*launcher, "test", "--tests", f"*{_class_name(test_file_path)}"]
    if _has_jacoco(repo_root):
        command.append("jacocoTestReport")
    command.append("--console=plain")
    return command


def _gradle_launcher(repo_root: Path, gradle_command: str) -> list[str]:
    """gradlew → 없으면 시스템 gradle. Windows 는 gradlew.bat."""
    for name, prefix in (("gradlew.bat", []), ("gradlew", ["sh"])):
        wrapper = repo_root / name
        if wrapper.is_file():
            return [*prefix, str(wrapper)]

    gradle = shutil.which(gradle_command)
    if gradle is None:
        raise ExecutionError(
            f"Gradle 을 찾을 수 없습니다 ({gradle_command}).\n"
            "  · 프로젝트에 gradlew 를 두거나\n"
            "  · Gradle 을 설치해 PATH 에 넣거나\n"
            "  · --gradle 옵션으로 실행 파일 경로를 지정하세요."
        )
    return [gradle]


def _run(command: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ExecutionError(f"실행 파일을 찾을 수 없습니다: {command[0]} ({exc})") from None
    except subprocess.TimeoutExpired:
        raise ExecutionError(f"테스트가 시간 초과되었습니다 ({timeout}s).") from None
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _has_jacoco(repo_root: Path) -> bool:
    for name in _BUILD_FILES:
        candidate = repo_root / name
        if not candidate.is_file():
            continue
        try:
            if "jacoco" in candidate.read_text(encoding="utf-8", errors="ignore").lower():
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
#  결과 수집
# ---------------------------------------------------------------------------
def _collect_junit(repo_root: Path, result: ExecutionResult) -> None:
    """build/test-results/test/*.xml 을 합산한다."""
    results_dir = repo_root / _JUNIT_DIR
    if not results_dir.is_dir():
        return

    for xml_file in sorted(results_dir.glob("TEST-*.xml")):
        try:
            root = ET.parse(xml_file).getroot()
        except (ET.ParseError, OSError):
            continue

        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        for suite in suites:
            total = int(suite.get("tests") or 0)
            failures = int(suite.get("failures") or 0)
            errors = int(suite.get("errors") or 0)
            skipped = int(suite.get("skipped") or 0)

            result.total += total
            result.failed += failures + errors
            result.skipped += skipped

            for case in suite.findall("testcase"):
                for tag in ("failure", "error"):
                    node = case.find(tag)
                    if node is None:
                        continue
                    name = f"{case.get('classname', '')}.{case.get('name', '')}".strip(".")
                    message = (node.get("message") or node.tag).strip()
                    result.failures.append(f"{name}: {message}"[:500])

    result.passed = max(result.total - result.failed - result.skipped, 0)


def _collect_jacoco(repo_root: Path, result: ExecutionResult) -> None:
    report = repo_root / _JACOCO_XML
    if not report.is_file():
        return
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError):
        return

    coverage = Coverage()
    for counter in root.findall("counter"):
        covered = int(counter.get("covered") or 0)
        missed = int(counter.get("missed") or 0)
        if counter.get("type") == "LINE":
            coverage.line_covered, coverage.line_missed = covered, missed
        elif counter.get("type") == "BRANCH":
            coverage.branch_covered, coverage.branch_missed = covered, missed
    result.coverage = coverage.to_dict()


# ---------------------------------------------------------------------------
#  파일 조작
# ---------------------------------------------------------------------------
def _safe_target(repo_root: Path, relative_path: str) -> Path:
    """프로젝트 안쪽에만 쓴다 (`..`/심볼릭 링크로 밖을 건드리지 못하게)."""
    root = repo_root.resolve()
    target = (repo_root / relative_path).resolve()
    if not target.is_relative_to(root):
        raise ExecutionError(f"프로젝트 밖 경로에는 쓸 수 없습니다: {relative_path}")
    return target


def _restore(target: Path, previous: str | None, existed: bool) -> None:
    """우리가 만든 테스트 파일만 되돌린다.

    사용자의 실제 작업 트리이므로 다른 파일은 절대 건드리지 않는다.
    """
    try:
        if existed:
            if previous is not None:
                target.write_text(previous, encoding="utf-8")
        elif target.is_file():
            target.unlink()
            # 우리가 만든 빈 디렉터리는 정리한다 (원래 있던 것은 남는다)
            for parent in target.parents:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                else:
                    break
    except OSError:
        pass


def _clip(text: str) -> str:
    return text if len(text) <= _OUTPUT_LIMIT else text[:_OUTPUT_LIMIT] + "\n… (이하 생략)"
