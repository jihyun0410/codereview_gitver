"""
TUI 렌더러.

결과 화면 구성
    | 결과                  |      |
    | 기능 중요도           | LOW  |
    | TEST CODE             | 보기 |
    | TEST RESULT           | PASS |
    | TEST RESULT 상세 보기 |      |

  (1) TEST CODE '보기'        → 테스트 대상 코드 + 작성 근거
  (2) 'TEST RESULT 상세 보기' → 결과 값 + 적절성 판단과 근거
  (3) 기능 중요도             → HIGH / MID / LOW
"""

from __future__ import annotations

import sys

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


def print_info(message: str) -> None:
    console.print(f"[cyan]>[/] {message}")


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
    has_test_code: bool = True,
    has_detail: bool = True,
) -> None:
    """정의서에 명시된 결과 표를 출력한다."""
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("항목", style="bold", width=22)
    table.add_column("값", width=30)

    table.add_row("결과", "")
    table.add_row(
        "기능 중요도",
        Text(importance or "-", style=IMPORTANCE_STYLE.get((importance or "").upper(), "white")),
    )
    table.add_row("TEST CODE", Text("보기", style="cyan underline") if has_test_code else "-")
    if test_result is None:
        table.add_row("TEST RESULT", Text("미실행", style="dim"))
    else:
        table.add_row(
            "TEST RESULT",
            Text(test_result, style=RESULT_STYLE.get(test_result.upper(), "white")),
        )
    table.add_row(
        "TEST RESULT 상세 보기",
        Text("보기", style="cyan underline") if has_detail else "-",
    )

    console.print()
    console.print(Panel(table, border_style="cyan"))


def print_test_code(language: str, test_code: str, target_code: str, rationale: str) -> None:
    """(1) TEST CODE '보기' — 테스트 대상 코드와 작성 근거."""
    console.print()
    if target_code:
        console.print(Panel(_markup(target_code), title="[bold]테스트를 진행한 코드[/]",
                            border_style="blue"))
    console.print(
        Panel(
            Syntax(test_code or "-", language or "text", theme="ansi_dark", word_wrap=True),
            title="[bold]TEST CODE[/]",
            border_style="cyan",
        )
    )
    console.print(
        Panel(rationale or "-", title="[bold]Test Code 작성 근거[/]", border_style="magenta")
    )


def print_result_detail(
    result: str, verdict: str, verdict_rationale: str, details: str, output: str
) -> None:
    """(2) 'TEST RESULT 상세 보기' — 결과 값과 적절성 판단 결과·근거."""
    console.print()
    console.print(
        Panel(
            Text(result or "-", style=RESULT_STYLE.get((result or "").upper(), "white")),
            title="[bold]TEST RESULT[/]",
            border_style="cyan",
        )
    )
    if output:
        console.print(Panel(_markup(output), title="[bold]결과 값 (실행 출력)[/]",
                            border_style="blue"))
    if details:
        console.print(Panel(details, title="[bold]결과 상세[/]", border_style="blue"))
    console.print(
        Panel(
            f"[bold]판단[/]: {verdict or '-'}\n\n{verdict_rationale or '-'}",
            title="[bold]적절성 판단 결과 및 근거[/]",
            border_style="magenta",
        )
    )


def prompt_view(has_test_code: bool, has_detail: bool) -> str | None:
    """
    표 아래에서 '보기' 를 선택받는다.

    파이프/리다이렉트 등 비대화형 환경에서는 묻지 않고 종료한다.
    """
    if not sys.stdin.isatty():
        return None

    options = []
    if has_test_code:
        options.append("[c] TEST CODE 보기")
    if has_detail:
        options.append("[r] TEST RESULT 상세 보기")
    if not options:
        return None
    options.append("[q] 종료")

    console.print("  ".join(options), style="dim")
    try:
        choice = input("선택> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return choice or None


def _markup(text: str, limit: int = 4000) -> Text:
    """rich 마크업으로 해석되지 않도록 순수 텍스트로 감싼다."""
    clipped = text if len(text) <= limit else text[:limit] + "\n… (이하 생략)"
    return Text(clipped)
