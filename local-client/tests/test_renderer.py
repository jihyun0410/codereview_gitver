"""결과 화면 검증 — 중요도 근거 표시와 'TEST CODE 보기' 의 test.txt 열기."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from codetest.tui import renderer as ui


def _render(**kwargs) -> str:
    """print_report 출력을 문자열로 받는다 (링크 이스케이프 포함)."""
    console = Console(force_terminal=True, width=120, record=True)
    original = ui.console
    ui.console = console
    try:
        ui.print_report(**kwargs)
    finally:
        ui.console = original
    return console.export_text()


def _test_file(tmp_path: Path) -> Path:
    path = tmp_path / "src" / "test" / "test.txt"
    path.parent.mkdir(parents=True)
    path.write_text("class GeneratedOrderTest {}\n", encoding="utf-8")
    return path


# --- (1) 중요도 판단 근거 -------------------------------------------------------
def test_report_shows_the_importance_rationale(tmp_path):
    out = _render(
        importance="MID",
        test_result="PASS",
        importance_rationale="- 영향도 점수 30점 → MID\n- 직접 변경된 그래프 노드 1개",
        test_file=_test_file(tmp_path),
    )

    assert "기능 중요도" in out and "MID" in out
    assert "중요도 판단 근거" in out
    assert "영향도 점수 30점" in out
    assert "직접 변경된 그래프 노드 1개" in out


def test_report_marks_a_missing_rationale(tmp_path):
    out = _render(
        importance="LOW", test_result=None, test_file=_test_file(tmp_path), has_detail=False
    )

    assert "중요도 판단 근거" in out          # 행 자체는 항상 있다
    assert "미실행" in out                    # 아직 실행 전


# --- (2) 'TEST CODE 보기' → test.txt --------------------------------------------
def test_view_is_a_link_to_the_generated_file(tmp_path):
    path = _test_file(tmp_path)
    link = ui._view_link(path)

    assert link.plain == "보기"
    assert path.resolve().as_uri() in str(link.style)


def test_view_link_without_a_file_is_a_dash():
    assert ui._view_link(None).plain == "-"


def test_report_tells_where_the_click_leads(tmp_path):
    path = _test_file(tmp_path)
    out = _render(importance="LOW", test_result=None, test_file=path, has_detail=False)

    assert "클릭하면" in out
    assert path.name in out


def test_open_test_file_uses_the_os_handler(tmp_path, monkeypatch):
    path = _test_file(tmp_path)
    launched: list[list[str]] = []
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))

    assert ui.open_test_file(path) is True
    assert launched and launched[0][0] == "xdg-open"
    assert launched[0][1] == str(path)


def test_open_test_file_reports_a_missing_file(tmp_path):
    assert ui.open_test_file(tmp_path / "없는파일.txt") is False


def test_open_test_file_reports_a_failed_launch(tmp_path, monkeypatch):
    path = _test_file(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("xdg-open 없음")

    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.subprocess, "Popen", _boom)

    assert ui.open_test_file(path) is False


def test_test_code_view_opens_the_file_instead_of_printing_it(tmp_path, monkeypatch):
    """파일이 열리면 코드를 화면에 다시 찍지 않는다 — 근거만 남는다."""
    path = _test_file(tmp_path)
    console = Console(force_terminal=True, width=120, record=True)
    monkeypatch.setattr(ui, "console", console)
    monkeypatch.setattr(ui, "open_test_file", lambda p: True)

    ui.print_test_code(
        test_code="class GeneratedOrderTest { void 검증안된코드() {} }",
        target_code="",
        thinking="수량 10 초과 분기가 새로 생겼다",
        rationale="- 경계값을 검증한다",
        test_file=path,
    )
    out = console.export_text()

    assert "파일을 열었습니다" in out
    assert "검증안된코드" not in out              # 코드는 파일로 본다
    assert "생각 과정" in out                     # 작성 근거는 계속 화면에
    assert "Test Code 작성 근거" in out


def test_test_code_view_falls_back_to_printing(tmp_path, monkeypatch):
    """파일을 열 수 없는 환경에서는 코드를 화면에 대신 출력한다."""
    path = _test_file(tmp_path)
    console = Console(force_terminal=True, width=120, record=True)
    monkeypatch.setattr(ui, "console", console)
    monkeypatch.setattr(ui, "open_test_file", lambda p: False)

    ui.print_test_code(
        test_code="class GeneratedOrderTest {}", target_code="", test_file=path
    )
    out = console.export_text()

    assert "파일을 열 수 없어" in out
    assert "GeneratedOrderTest" in out
