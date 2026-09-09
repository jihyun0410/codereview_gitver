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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_open_file_uses_the_os_handler(tmp_path, monkeypatch):
    path = _test_file(tmp_path)
    launched: list[list[str]] = []
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))

    assert ui.open_file(path) is True
    assert launched and launched[0][0] == "xdg-open"
    assert launched[0][1] == str(path)


def test_open_file_reports_a_missing_file(tmp_path):
    assert ui.open_file(tmp_path / "없는파일.txt") is False


def test_open_file_reports_a_failed_launch(tmp_path, monkeypatch):
    path = _test_file(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("xdg-open 없음")

    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.subprocess, "Popen", _boom)

    assert ui.open_file(path) is False


def test_test_code_view_opens_the_file_instead_of_printing_it(tmp_path, monkeypatch):
    """파일이 열리면 코드를 화면에 다시 찍지 않는다 — 근거만 남는다."""
    path = _test_file(tmp_path)
    console = Console(force_terminal=True, width=120, record=True)
    monkeypatch.setattr(ui, "console", console)
    monkeypatch.setattr(ui, "open_file", lambda p: True)

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
    monkeypatch.setattr(ui, "open_file", lambda p: False)

    ui.print_test_code(
        test_code="class GeneratedOrderTest {}", target_code="", test_file=path
    )
    out = console.export_text()

    assert "파일을 열 수 없어" in out
    assert "GeneratedOrderTest" in out


# --- (3) 'TEST RESULT 상세 보기' → test-result.txt -------------------------------
#
# TEST CODE '보기' 와 **똑같이** 동작해야 한다 — 표의 '보기' 가 파일 링크이고,
# 클릭(또는 [r])하면 그 파일이 열린다.
REPORT = {
    "result": "PASS",
    "intent": "조건 변경",
    "intent_rationale": "- `quantity > 10` 분기가 추가됨",
    "total": 3, "passed": 3, "failed": 0, "skipped": 0,
    "exit_code": 0,
    "springboot_applied": True,
    "test_file_path": "src/test/java/com/example/demo/GeneratedOrderTest.java",
    "coverage": {"line_rate": 80.0, "branch_rate": 100.0, "line_covered": 16, "line_missed": 4},
    "verdict": "적절",
    "verdict_rationale": "- 경계값이 모두 검증됨",
    "details": "- 3건 모두 통과",
    "output": "BUILD SUCCESSFUL",
}


def _result_file(tmp_path: Path) -> Path:
    path = tmp_path / "src" / "test" / "test-result.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("상세", encoding="utf-8")
    return path


def test_detail_view_is_a_link_just_like_the_test_code_one(tmp_path):
    result_file = _result_file(tmp_path)
    out = _render(
        importance="MID", test_result="PASS",
        test_file=_test_file(tmp_path), result_file=result_file,
    )

    assert "TEST RESULT 상세 보기" in out
    # 두 파일 모두 "클릭하면 … 를 엽니다" 안내가 붙는다
    assert "TEST CODE '보기' 를 클릭하면" in out
    assert "TEST RESULT 상세 '보기' 를 클릭하면" in out
    assert result_file.name in out


def test_detail_cell_carries_the_result_file_uri(tmp_path):
    link = ui._view_link(_result_file(tmp_path))
    assert link.plain == "보기"
    assert _result_file(tmp_path).resolve().as_uri() in str(link.style)


def test_detail_cell_is_a_dash_before_any_run(tmp_path):
    out = _render(
        importance="LOW", test_result=None, test_file=_test_file(tmp_path), has_detail=False
    )
    assert "TEST RESULT 상세 보기" in out
    assert "미실행" in out


def test_detail_view_opens_the_file_instead_of_printing_it(tmp_path, monkeypatch):
    console = Console(force_terminal=True, width=120, record=True)
    monkeypatch.setattr(ui, "console", console)
    monkeypatch.setattr(ui, "open_file", lambda p: True)

    ui.print_result_detail(REPORT, _result_file(tmp_path))
    out = console.export_text()

    assert "결과 상세 파일을 열었습니다" in out
    assert "경계값이 모두 검증됨" not in out      # 내용은 파일로 본다


def test_detail_view_falls_back_to_printing(tmp_path, monkeypatch):
    """파일을 열 수 없는 환경에서는 예전처럼 화면에 출력한다."""
    console = Console(force_terminal=True, width=120, record=True)
    monkeypatch.setattr(ui, "console", console)
    monkeypatch.setattr(ui, "open_file", lambda p: False)

    ui.print_result_detail(REPORT, _result_file(tmp_path))
    out = console.export_text()

    assert "파일을 열 수 없어" in out
    assert "경계값이 모두 검증됨" in out
    assert "조건 변경" in out


def test_detail_view_without_a_file_still_prints(monkeypatch):
    """파일 저장에 실패했어도 [r] 로는 볼 수 있어야 한다."""
    console = Console(force_terminal=True, width=120, record=True)
    monkeypatch.setattr(ui, "console", console)

    ui.print_result_detail(REPORT)
    assert "경계값이 모두 검증됨" in console.export_text()


# --- test-result.txt 본문 -------------------------------------------------------
def test_result_file_carries_every_section():
    text = ui.render_result_detail(REPORT)

    assert "TEST RESULT : PASS" in text
    assert "[변경 의도와 근거]" in text and "조건 변경" in text
    assert "[결과 값]" in text
    assert "총 3 / 성공 3 / 실패 0 / 건너뜀 0" in text
    assert "라인 80.0% (16/20)" in text
    assert "[결과 상세]" in text
    assert "[실행 출력]" in text
    assert "[적절성 판단 결과 및 근거]" in text and "경계값이 모두 검증됨" in text
    assert "[실패 내역]" not in text            # 실패가 없으면 절도 없다


def test_result_file_keeps_the_whole_gradle_output():
    """터미널은 4000자에서 자르지만 파일은 자르지 않는다 — 파일로 여는 이유다."""
    long_output = "\n".join(f"line {i}" for i in range(2000))
    text = ui.render_result_detail({**REPORT, "output": long_output})

    assert "line 1999" in text
    assert "이하 생략" not in text


def test_result_file_lists_failures():
    text = ui.render_result_detail(
        {**REPORT, "result": "FAIL", "failed": 1, "failures": ["OrderTest.경계값 실패"]}
    )
    assert "[실패 내역]" in text
    assert "- OrderTest.경계값 실패" in text


def test_terminal_table_and_file_share_the_same_rows():
    """한쪽만 고쳐 두 화면이 갈라지지 않도록 같은 목록을 쓴다."""
    rows = ui._summary_rows(REPORT)
    text = ui.render_result_detail(REPORT)
    for label, value, _ in rows:
        assert label in text and value in text


def test_result_file_columns_line_up_with_korean_labels():
    """한글은 터미널에서 두 칸을 차지한다 — 글자 수로 채우면 콜론이 어긋난다."""
    text = ui.render_result_detail(REPORT)
    block = text[text.index("[결과 값]"):text.index("[결과 상세]")]

    columns = {
        sum(2 if ui.east_asian_width(ch) in "WF" else 1 for ch in line.split(":", 1)[0])
        for line in block.splitlines()
        if ":" in line and not line.startswith("[")
    }
    assert len(columns) == 1, f"콜론 위치가 어긋난다: {columns}"
