"""
로컬 빌드 실행기 (Gradle / Maven).

정의서: "JaCoCo와 @SpringBootTest 를 사용하여 Test Code 실행"

테스트는 **명령을 입력한 이 PC 의 프로젝트에서** 돌린다. 개발자가 방금 고친
코드가 그대로 들어 있는 작업 트리라 별도 사본을 만들거나 변경분을 덮어쓸 필요가
없다. `@SpringBootTest` 주입은 코드 기반 작업이라 MCP 가 해 주고, 여기서는 그
결과를 받아 파일로 쓰고 빌드 도구를 돌린 뒤 집계만 한다.

**복구 주의**: 서버 사본과 달리 여기는 사용자의 실제 작업 트리다.
`git checkout -- .` 같은 되돌리기는 미커밋 변경분을 날리므로 절대 하지 않는다.
이 실행이 새로 만든 파일만 지운다.

**폴더 구조는 가정하지 않는다.** 테스트를 쓸 자리·리포트 경로·실행 명령은 전부
`project_layout` 이 실제 디렉터리를 보고 정한다 (멀티 모듈·Maven·저장소 하위 빌드
루트 지원). 이 모듈은 그렇게 정해진 자리에 파일을 쓰고, 명령을 돌리고, 집계만 한다.
"""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from codetest import project_layout
from codetest.project_layout import Layout, LayoutError

#: gradle 출력이 리포트에 통째로 실리지 않도록 자른다
_OUTPUT_LIMIT = 20_000

#: javac 오류 한 줄: `/경로/Foo.java:52: error: not a statement`
_JAVAC_ERROR = re.compile(r"^(?P<file>\S+\.(?:java|kt)):(?P<line>\d+):\s*(?:error|오류):\s*(?P<message>.+)$")
#: 컴파일 외의 실패 이유는 gradle 이 이 블록에 적는다
_WHAT_WENT_WRONG = re.compile(r"^\* What went wrong:\s*$")


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
    #: 테스트가 시작조차 못한 이유 (컴파일 오류 등). 테스트 실패와는 다르다.
    build_errors: list[str] = field(default_factory=list)
    #: 어디서 무엇으로 돌렸는지 — 구조가 제각각인 프로젝트에서 원인을 찾는 단서
    build_tool: str = project_layout.GRADLE
    module: str = ""

    @property
    def tests_ran(self) -> bool:
        """이번 실행에서 테스트가 실제로 돌았는가.

        gradle 이 컴파일 단계에서 멈추면 exit code 만 1 이고 집계는 전부 0 이다.
        '실패 0건인데 FAIL' 로 보이는 상태를 이 값으로 구분한다.
        """
        return self.total > 0

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
            "build_errors": self.build_errors,
            "build_tool": self.build_tool,
            "module": self.module,
        }


def run_tests(
    repo_root: Path,
    test_source: str,
    test_file_path: str,
    package: str = "",
    springboot_applied: bool = True,
    applied: list[str] | None = None,
    gradle_command: str = "gradle",
    maven_command: str = "mvn",
    layout: Layout | None = None,
    timeout: int = 900,
) -> ExecutionResult:
    """
    이 PC 의 프로젝트에서 @SpringBootTest 를 실행한다.

    :param test_source:    MCP 가 @SpringBootTest 주입을 마친 Java 소스
    :param test_file_path: MCP 가 계산해 준 저장소 기준 경로. **단서로만 쓴다** —
                           실제 자리는 `project_layout` 이 이 PC 의 디렉터리를 보고
                           정한다 (멀티 모듈이면 MCP 의 추정과 다를 수 있다).
    :param package:        테스트 소스의 package 선언. 모듈을 고르는 단서다.
    :param layout:         이미 탐지해 둔 레이아웃 (없으면 여기서 탐지한다)
    """
    if not repo_root.is_dir():
        raise ExecutionError(f"프로젝트 경로가 없습니다: {repo_root}")

    class_name = _class_name(test_file_path)
    package = package or _package_of(test_file_path)
    try:
        layout = layout or project_layout.detect(
            repo_root, package=package, hint_path=test_file_path
        )
    except LayoutError as exc:
        raise ExecutionError(str(exc)) from None

    resolved_path = layout.relative_test_file(package, class_name)
    target = _safe_target(repo_root, resolved_path)
    existed = target.is_file()
    previous = target.read_text(encoding="utf-8") if existed else None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test_source, encoding="utf-8")

        # 지난 실행이 남긴 리포트를 먼저 치운다. 안 그러면 이번에 컴파일이 깨져
        # 테스트가 한 건도 안 돌아도 옛 XML 이 그대로 집계돼
        # "FAIL 인데 총 3 / 성공 3" 같은 리포트가 나온다.
        _clear_stale_reports(layout, class_name)

        try:
            command = layout.command(class_name, gradle_command, maven_command)
        except LayoutError as exc:
            raise ExecutionError(str(exc)) from None
        returncode, output = _run(command, layout.build_root, timeout)

        result = ExecutionResult(
            exit_code=returncode,
            output=_clip(output),
            test_file_path=resolved_path,
            springboot_applied=springboot_applied,
            applied=list(applied or []),
            command=command,
            jacoco_enabled=layout.jacoco,
            build_tool=layout.tool,
            module=layout.module_path,
        )
        _collect_junit(layout, result, class_name)
        _collect_jacoco(layout, result)
        if returncode != 0 and not result.tests_ran:
            # 테스트가 시작조차 못했다 — 왜 FAIL 인지 출력에서 찾아 남긴다.
            result.build_errors = _build_errors(output)
        return result
    finally:
        # 실제 작업 트리다. 우리가 만든 것만 되돌린다.
        _restore(target, previous, existed)


# ---------------------------------------------------------------------------
#  실행
# ---------------------------------------------------------------------------
def _class_name(test_file_path: str) -> str:
    return Path(test_file_path).stem


def _package_of(test_file_path: str) -> str:
    """`…/src/test/java/com/example/demo/FooTest.java` → `com.example.demo`.

    package 선언을 따로 받지 못했을 때의 보조 수단이다. 언어 디렉터리를 하나로
    못 박지 않으려고 `src/test/<언어>/` 다음부터를 패키지로 읽는다.
    """
    normalized = test_file_path.replace("\\", "/")
    marker = "src/test/"
    if marker not in normalized:
        return ""
    tail = normalized.split(marker, 1)[1].split("/")
    return ".".join(tail[1:-1])          # 언어 디렉터리와 파일명을 뺀 가운데


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


# ---------------------------------------------------------------------------
#  결과 수집
# ---------------------------------------------------------------------------
def _collect_junit(layout: Layout, result: ExecutionResult, class_name: str) -> None:
    """이번에 돌린 클래스의 JUnit XML 만 합산한다.

    디렉터리에 있는 XML 을 전부 더하면 우리가 돌리지도 않은 다른 테스트 클래스의
    지난 결과까지 이번 실행의 집계로 둔갑한다 (한 클래스만 돌리므로 나머지는
    갱신되지도 않는다). 디렉터리 위치는 빌드 도구와 모듈에 따라 다르다 —
    Gradle `<모듈>/build/test-results/test`, Maven `<모듈>/target/surefire-reports`.
    """
    results_dir = layout.junit_dir
    if not results_dir.is_dir():
        return

    for xml_file in sorted(_report_files(results_dir, class_name)):
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


def _report_files(results_dir: Path, class_name: str) -> list[Path]:
    """`TEST-<패키지>.<클래스>.xml` 중 이번에 돌린 클래스의 것만 고른다.

    중첩 클래스(`Outer$Inner`)도 같은 실행의 산출물이므로 함께 센다.
    """
    matched: list[Path] = []
    for xml_file in results_dir.glob("TEST-*.xml"):
        simple = xml_file.stem[len("TEST-"):].rsplit(".", 1)[-1]
        if simple == class_name or simple.startswith(f"{class_name}$"):
            matched.append(xml_file)
    return matched


def _build_errors(output: str) -> list[str]:
    """테스트가 시작도 못한 이유를 gradle 출력에서 뽑는다.

    컴파일 오류가 있으면 그것을, 없으면 gradle 의 '* What went wrong' 블록을 쓴다.
    이게 없으면 리포트에 "실패 0건인데 FAIL" 만 남아 원인을 알 수 없다.
    """
    compile_errors: list[str] = []
    for line in output.splitlines():
        match = _JAVAC_ERROR.match(line.strip())
        if match:
            name = Path(match.group("file")).name
            compile_errors.append(
                f"{name}:{match.group('line')}: {match.group('message').strip()}"[:500]
            )
    if compile_errors:
        return compile_errors[:20]

    reasons: list[str] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if not _WHAT_WENT_WRONG.match(line.strip()):
            continue
        for following in lines[index + 1:]:
            stripped = following.strip()
            if not stripped or stripped.startswith("*"):
                break
            reasons.append(stripped[:500])
        break
    return reasons[:20]


def _collect_jacoco(layout: Layout, result: ExecutionResult) -> None:
    report = layout.coverage_report
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
def _clear_stale_reports(layout: Layout, class_name: str) -> None:
    """이번에 갱신될 리포트만 미리 지운다.

    사용자의 실제 작업 트리이므로 산출물 디렉터리를 통째로 날리지 않는다. 우리가
    돌릴 클래스의 JUnit XML 과, 이번 실행이 다시 만들 커버리지 리포트만 치운다.
    """
    results_dir = layout.junit_dir
    targets = _report_files(results_dir, class_name) if results_dir.is_dir() else []
    targets.append(layout.coverage_report)
    for path in targets:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass          # 지우지 못해도 실행은 계속한다 (집계가 낡을 뿐)


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
