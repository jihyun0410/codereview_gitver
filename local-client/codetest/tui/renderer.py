"""
TUI 렌더러.

정의서 [결과 양식]
    | 결과                  |      |
    | 기능 중요도           | LOW  |
    | 중요도 판단 근거      | …    |
    | TEST CODE             | 보기 |
    | TEST RESULT           | PASS |
    | TEST RESULT 상세 보기 | 보기 |

  (1) TEST CODE '보기'        → **생성된 test.txt 를 연다** (클릭 또는 [c])
                                작성 근거(사고의 사슬 · 정상/실패 케이스)는 터미널에 함께 출력
  (2) 'TEST RESULT 상세 보기' → **test-result.txt 를 연다** (클릭 또는 [r])
                                결과 값 + 적절성 판단 결과와 근거
                                + 파악한 변경 의도와 근거  (정의서 (2))
  (3) 기능 중요도             → HIGH / MID / LOW **와 그렇게 판단한 근거**

두 '보기' 는 **똑같이 동작한다** — 파일 링크(OSC 8)로 그려서, IntelliJ 터미널·
Windows Terminal 등에서는 클릭하면 해당 파일이 열린다. 링크를 지원하지 않는
터미널에서는 아래 선택 프롬프트의 [c] / [r] 로 같은 파일을 연다. 파일을 아예
열 수 없는 환경(원격 셸 등)에서만 내용을 터미널에 대신 출력한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unicodedata import east_asian_width

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


def _configure_stdio_encoding() -> None:
    """
    표준 출력을 UTF-8 로 고정한다.

    한국어 Windows 콘솔(cp949)에서 출력을 파일이나 파이프로 넘기면
    '—', 이모지 등에서 UnicodeEncodeError 로 프로세스가 죽는다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_configure_stdio_encoding()

console = Console()

#: 기능 중요도 → 색
IMPORTANCE_STYLE = {"HIGH": "bold red", "MID": "bold yellow", "LOW": "bold green"}
RESULT_STYLE = {"PASS": "bold green", "FAIL": "bold red"}


# ---------------------------------------------------------------------------
#  공통
# ---------------------------------------------------------------------------
def print_header(title: str, subtitle: str = "") -> None:
    console.print()
    console.rule(f"[bold cyan]{title}[/]" + (f" [dim]{subtitle}[/]" if subtitle else ""))


def print_error(message: str) -> None:
    console.print(Panel(message, title="[bold red]오류[/]", border_style="red"))


def print_info(message: str, soft_wrap: bool = False) -> None:
    # soft_wrap=True : URL 처럼 중간에 줄바꿈이 끼면 안 되는 값에 사용
    console.print(f"[cyan]>[/] {message}", soft_wrap=soft_wrap)


def print_success(message: str) -> None:
    console.print(f"[green]v[/] {message}")


def print_warning(message: str) -> None:
    console.print(f"[yellow]![/] {message}")


def print_changed_files(files: list, scope: str) -> None:
    """대상 변경 파일 목록."""
    if not files:
        console.print("[dim]대상 파일이 없습니다.[/]")
        return

    scope_label = {
        "unstaged": "staging 미포함 변경",
        "staged": "staging 포함 변경",
        "worktree": "Working Tree 전체 변경",
    }.get(scope, scope)

    table = Table(title=f"대상 파일 ({scope_label})", title_style="bold", header_style="bold cyan")
    table.add_column("상태", width=10)
    table.add_column("파일", overflow="fold")
    table.add_column("+", justify="right", style="green", width=6)
    table.add_column("-", justify="right", style="red", width=6)

    status_style = {"added": "green", "modified": "yellow", "removed": "red"}
    for item in files:
        status = getattr(item, "status", "modified")
        table.add_row(
            Text(status, style=status_style.get(status, "white")),
            getattr(item, "path", ""),
            str(getattr(item, "additions", 0)),
            str(getattr(item, "deletions", 0)),
        )
    console.print(table)


# ---------------------------------------------------------------------------
#  결과 화면
# ---------------------------------------------------------------------------
def print_report(
    importance: str,
    test_result: str | None,
    importance_rationale: str = "",
    test_file: Path | None = None,
    has_detail: bool = True,
    result_file: Path | None = None,
) -> None:
    """정의서에 명시된 결과 표를 출력한다.

    :param importance_rationale: 중요도를 그렇게 판단한 근거. 등급 바로 아래에 붙는다.
    :param test_file:   생성된 Test Code 파일(`src/test/test.txt`).
                        주어지면 'TEST CODE' 의 '보기' 가 이 파일을 여는 링크가 된다.
    :param result_file: 실행 결과 상세 파일(`src/test/test-result.txt`).
                        주어지면 'TEST RESULT 상세 보기' 도 같은 방식의 링크가 된다.
    """
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("항목", style="bold", width=22)
    table.add_column("값", overflow="fold")

    table.add_row("결과", "")
    table.add_row(
        "기능 중요도",
        Text(importance or "-", style=IMPORTANCE_STYLE.get((importance or "").upper(), "white")),
    )
    # 정의서: 등급만이 아니라 "어떠한 근거로 표시하는지" 를 함께 보여야 한다.
    table.add_row("중요도 판단 근거", _rationale_text(importance_rationale))

    table.add_row("TEST CODE", _view_link(test_file))
    if test_result is None:
        table.add_row("TEST RESULT", Text("미실행", style="dim"))
    else:
        table.add_row(
            "TEST RESULT",
            Text(test_result, style=RESULT_STYLE.get(test_result.upper(), "white")),
        )
    if not has_detail:
        table.add_row("TEST RESULT 상세 보기", Text("-", style="dim"))
    elif result_file is not None:
        table.add_row("TEST RESULT 상세 보기", _view_link(result_file))
    else:
        # 파일로 남기지 못한 경우 — 클릭은 안 되지만 [r] 로는 볼 수 있다
        table.add_row("TEST RESULT 상세 보기", Text("보기", style="cyan underline"))

    console.print()
    console.print(Panel(table, border_style="cyan"))

    for label, path in (("TEST CODE", test_file), ("TEST RESULT 상세", result_file)):
        if path is not None:
            console.print(
                f"[dim]{label} '보기' 를 클릭하면 {path} 를 엽니다.[/]", soft_wrap=True
            )


def _rationale_text(rationale: str) -> Text:
    """중요도 판단 근거. 여러 줄이면 그대로 여러 줄로 보여 준다."""
    cleaned = (rationale or "").strip()
    if not cleaned:
        return Text("-", style="dim")
    return Text(cleaned, style="dim")


def _view_link(path: Path | None) -> Text:
    """
    표의 '보기' 한 칸. TEST CODE 와 TEST RESULT 상세가 같은 함수를 쓴다.

    파일 경로를 알면 OSC 8 하이퍼링크로 그려 **클릭하면 그 파일이 열리게** 한다.
    (IntelliJ 터미널·Windows Terminal 등이 지원한다. 미지원 터미널은 [c]/[r] 로 연다.)
    """
    if path is None:
        return Text("-", style="dim")
    try:
        uri = path.resolve().as_uri()
    except (OSError, ValueError):
        return Text("보기", style="cyan underline")
    return Text("보기", style=f"cyan underline link {uri}")


def open_file(path: Path) -> bool:
    """
    파일을 OS 기본 프로그램으로 연다 (test.txt / test-result.txt 공용).

    '보기' 클릭을 지원하지 않는 터미널에서 같은 동작을 하는 경로다.
    열지 못하면 False 를 돌려 호출하는 쪽이 터미널 출력으로 대신하게 한다.
    """
    if not path.is_file():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows 전용
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, AttributeError):
        return False
    return True


def print_test_code(
    test_code: str,
    target_code: str,
    thinking: str = "",
    test_cases: str = "",
    rationale: str = "",
    test_file: Path | None = None,
) -> None:
    """
    (1) TEST CODE '보기'.

    생성된 Test Code 는 **`src/test/test.txt` 파일을 열어서** 본다.
    파일을 열지 못한 터미널에서만 코드를 화면에 대신 출력한다.

    정의서: "실제 Test를 진행한 Code와 Test Code를 작성한 근거가 보여짐"
    작성 근거(사고의 사슬 · 정상/실패 케이스 판단)는 언제나 터미널에 함께 싣는다.
    """
    console.print()

    opened = test_file is not None and open_file(test_file)
    if opened:
        print_success(f"Test Code 파일을 열었습니다: {test_file}")

    if target_code:
        console.print(
            Panel(_plain(target_code), title="[bold]테스트를 진행한 코드[/]", border_style="blue")
        )

    if not opened:
        # 파일을 열 수 없는 환경(원격 셸 등)에서는 코드를 그대로 보여 준다.
        if test_file is not None:
            print_warning(f"파일을 열 수 없어 화면에 출력합니다: {test_file}")
        console.print(
            Panel(
                Syntax(test_code or "-", "java", theme="ansi_dark", word_wrap=True),
                title="[bold]TEST CODE (@SpringBootTest)[/]",
                border_style="cyan",
            )
        )

    if thinking:
        console.print(
            Panel(_plain(thinking), title="[bold]생각 과정 (사고의 사슬)[/]", border_style="magenta")
        )
    if test_cases:
        console.print(
            Panel(_plain(test_cases), title="[bold]정상 / 실패 케이스 판단[/]", border_style="magenta")
        )
    console.print(
        Panel(_plain(rationale or "-"), title="[bold]Test Code 작성 근거[/]", border_style="magenta")
    )


def print_result_detail(report: dict, result_file: Path | None = None) -> None:
    """
    (2) 'TEST RESULT 상세 보기'.

    TEST CODE '보기' 와 똑같이 **파일을 연다.** 열리면 화면에 다시 찍지 않는다.
    파일을 열 수 없는 환경(원격 셸 등)에서만 아래 패널로 대신 보여 준다.

    정의서: "결과 값을 보여주고 적절성 여부에 대한 판단 결과, 근거 또한 보여줌"
            "파악한 의도와 근거에 대한 내용을 <Test Result 보기>의 결과값에 넣는다"
    """
    console.print()

    if result_file is not None and open_file(result_file):
        print_success(f"결과 상세 파일을 열었습니다: {result_file}")
        return
    if result_file is not None:
        print_warning(f"파일을 열 수 없어 화면에 출력합니다: {result_file}")

    result = report.get("result", "")
    console.print(
        Panel(
            Text(result or "-", style=RESULT_STYLE.get(result.upper(), "white")),
            title="[bold]TEST RESULT[/]",
            border_style="cyan",
        )
    )

    # --- 파악한 의도 (정의서 (2)) ---
    intent = report.get("intent") or "-"
    console.print(
        Panel(
            f"[bold]의도[/]: {intent}\n\n{_plain(report.get('intent_rationale') or '-')}",
            title="[bold]변경 의도와 근거[/]",
            border_style="green",
        )
    )

    # --- 실행 집계 + JaCoCo ---
    console.print(Panel(_summary_table(report), title="[bold]결과 값[/]", border_style="blue"))

    build_errors = report.get("build_errors") or []
    if build_errors:
        console.print(
            Panel(
                _plain("\n".join(f"- {item}" for item in build_errors)),
                title="[bold]빌드 오류 (테스트 실패가 아님)[/]",
                border_style="red",
            )
        )
    elif _build_failed(report):
        console.print(
            Panel(
                _plain("gradle 이 0 이 아닌 코드로 끝났지만 실행된 테스트가 없습니다.\n"
                       "아래 '실행 출력' 에서 원인을 확인하세요."),
                title="[bold]빌드 실패[/]", border_style="red",
            )
        )

    failures = report.get("failures") or []
    if failures:
        console.print(
            Panel(_plain("\n".join(f"- {item}" for item in failures)),
                  title="[bold]실패 내역[/]", border_style="red")
        )
    if report.get("details"):
        console.print(Panel(_plain(report["details"]), title="[bold]결과 상세[/]", border_style="blue"))
    if report.get("output"):
        console.print(
            Panel(_plain(report["output"]), title="[bold]실행 출력[/]", border_style="blue")
        )

    console.print(
        Panel(
            f"[bold]판단[/]: {report.get('verdict') or '-'}\n\n"
            f"{_plain(report.get('verdict_rationale') or '-')}",
            title="[bold]적절성 판단 결과 및 근거[/]",
            border_style="magenta",
        )
    )


def _summary_rows(report: dict) -> list[tuple[str, str, str]]:
    """실행 집계 · @SpringBootTest 적용 여부 · JaCoCo 커버리지 → (항목, 값, 색).

    터미널 표와 test-result.txt 가 같은 목록을 쓴다. 한쪽만 고쳐 두 화면의
    내용이 갈라지는 일을 막는다.
    """
    rows: list[tuple[str, str, str]] = []

    # 컴파일이 깨지면 테스트가 시작조차 못해 집계가 전부 0 이 된다.
    # "실패 0건인데 FAIL" 로 읽히지 않도록 이유를 맨 위에 박는다.
    if _build_failed(report):
        rows.append((
            "빌드", "실패 — 테스트가 한 건도 실행되지 않았습니다", "bold red",
        ))

    rows += [
        (
            "테스트",
            (
                f"총 {report.get('total', 0)} / 성공 {report.get('passed', 0)} "
                f"/ 실패 {report.get('failed', 0)} / 건너뜀 {report.get('skipped', 0)}"
            ),
            "",
        ),
        ("gradle exit code", str(report.get("exit_code", "-")), ""),
    ]

    if report.get("springboot_applied"):
        rows.append(("@SpringBootTest", "적용됨", "green"))
    else:
        rows.append(("@SpringBootTest", "미적용", "red"))

    if report.get("test_file_path"):
        rows.append(("실행 파일", report["test_file_path"], ""))

    coverage = report.get("coverage")
    if coverage:
        covered = coverage.get("line_covered", 0)
        rows.append((
            "JaCoCo 커버리지",
            (
                f"라인 {coverage.get('line_rate')}% "
                f"({covered}/{covered + coverage.get('line_missed', 0)}), "
                f"분기 {coverage.get('branch_rate')}%"
            ),
            "",
        ))
    elif report.get("jacoco_enabled"):
        # 테스트가 실패하면 gradle 이 jacocoTestReport 까지 가지 않는다.
        rows.append(("JaCoCo", "리포트 없음 (테스트 실패로 커버리지 미집계)", "yellow"))
    else:
        rows.append(("JaCoCo", "프로젝트 build 설정에 미적용", "yellow"))

    for note in report.get("applied") or []:
        rows.append(("주입 작업", note, ""))
    return rows


def _build_failed(report: dict) -> bool:
    """테스트가 시작도 못했는가 (컴파일 오류 등).

    gradle 이 컴파일 단계에서 멈추면 exit code 만 1 이고 집계는 전부 0 이다.
    그 상태를 '실패 0건' 으로만 보여 주면 왜 FAIL 인지 알 수가 없다.
    """
    return bool(report.get("build_errors")) or (
        int(report.get("exit_code") or 0) != 0 and int(report.get("total") or 0) == 0
    )


def _summary_table(report: dict) -> Table:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("항목", style="bold", width=22)
    table.add_column("값", overflow="fold")
    for label, value, style in _summary_rows(report):
        table.add_row(label, Text(value, style=style) if style else value)
    return table


def render_result_detail(report: dict) -> str:
    """'TEST RESULT 상세 보기' 가 여는 test-result.txt 의 본문.

    화면과 같은 항목을 담되 **실행 출력은 자르지 않는다** — 터미널에서는 길이
    때문에 잘라야 하지만, 파일로 여는 편을 택한 이유가 바로 그것이다.
    """
    def section(title: str, body: str) -> str:
        return f"[{title}]\n{(body or '-').strip()}\n"

    parts = [
        "=" * 72,
        " TEST RESULT 상세",
        "=" * 72,
        "",
        f"TEST RESULT : {report.get('result') or '-'}",
        "",
        section(
            "변경 의도와 근거",
            f"의도: {report.get('intent') or '-'}\n"
            f"{(report.get('intent_rationale') or '-').strip()}",
        ),
        "[결과 값]",
        *(f"{_pad(label, 18)}: {value}" for label, value, _ in _summary_rows(report)),
        "",
    ]

    build_errors = report.get("build_errors") or []
    if build_errors:
        parts.append(section(
            "빌드 오류 (테스트 실패가 아님)",
            "테스트가 한 건도 실행되지 않았습니다. 아래를 고친 뒤 다시 실행하세요.\n"
            + "\n".join(f"- {item}" for item in build_errors),
        ))

    failures = report.get("failures") or []
    if failures:
        parts.append(section("실패 내역", "\n".join(f"- {item}" for item in failures)))
    if report.get("details"):
        parts.append(section("결과 상세", report["details"]))
    if report.get("output"):
        parts.append(section("실행 출력", report["output"]))

    parts.append(section(
        "적절성 판단 결과 및 근거",
        f"판단: {report.get('verdict') or '-'}\n"
        f"{(report.get('verdict_rationale') or '-').strip()}",
    ))
    return "\n".join(parts).rstrip() + "\n"


def prompt_view(has_test_code: bool, has_detail: bool) -> str | None:
    """
    표 아래에서 '보기' 를 선택받는다.

    표의 '보기' 링크 클릭이 곧 이 선택과 같은 동작이다. 링크를 지원하지 않는
    터미널을 위한 대체 입력이므로 파이프/리다이렉트 등 비대화형 환경에서는
    묻지 않고 종료한다.
    """
    if not sys.stdin.isatty():
        return None

    options = []
    if has_test_code:
        options.append("[c] TEST CODE 보기 (test.txt 열기)")
    if has_detail:
        options.append("[r] TEST RESULT 상세 보기 (test-result.txt 열기)")
    if not options:
        return None
    options.append("[q] 종료")

    console.print("  ".join(options), style="dim")
    try:
        choice = input("선택> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return choice or None


def _pad(label: str, width: int) -> str:
    """표시 폭 기준으로 오른쪽을 채운다.

    f"{label:<18}" 은 **글자 수**로 채우는데 한글은 터미널에서 두 칸을 차지한다.
    그대로 두면 '테스트' 와 'gradle exit code' 의 콜론이 어긋나 파일이 지저분해진다.
    """
    span = sum(2 if east_asian_width(char) in "WF" else 1 for char in label)
    return label + " " * max(width - span, 0)


def _plain(text: str, limit: int = 4000) -> Text:
    """rich 마크업으로 해석되지 않도록 순수 텍스트로 감싼다."""
    clipped = text if len(text) <= limit else text[:limit] + "\n… (이하 생략)"
    return Text(clipped)
