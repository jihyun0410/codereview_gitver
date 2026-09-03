"""결과 출력 흐름 검증 — cli._show 가 화면에 무엇을 넘기는지."""

from __future__ import annotations

from pathlib import Path

import pytest

from codetest import cli
from codetest.tui import renderer as ui


@pytest.fixture
def captured(monkeypatch) -> dict:
    """print_report 인자만 가로채고, 선택 루프는 즉시 끝낸다."""
    seen: dict = {}
    monkeypatch.setattr(cli.ui, "print_report", lambda *a, **kw: seen.update(kw, args=a))
    monkeypatch.setattr(cli.ui, "prompt_view", lambda **kw: None)
    return seen


GENERATED = {
    "test_code": "class GeneratedOrderTest {}",
    "importance": "LOW",
    "importance_rationale": "- 생성 시점 근거",
}


def test_generate_screen_carries_the_rationale_and_the_file(captured, tmp_path):
    test_file = tmp_path / "src" / "test" / "test.txt"
    cli._show(GENERATED, report=None, test_file=test_file)

    assert captured["args"] == ("LOW",)
    assert captured["importance_rationale"] == "- 생성 시점 근거"
    assert captured["test_file"] == test_file
    assert captured["test_result"] is None      # generate 는 실행하지 않는다
    assert captured["has_detail"] is False


def test_report_importance_wins_over_the_cached_one(captured, tmp_path):
    """codetest test 의 generated 는 낡은 로컬 캐시다 — 이번 실행 판정을 쓴다."""
    report = {
        "result": "PASS",
        "importance": "HIGH",
        "importance_rationale": "- 이번 실행 근거",
    }
    cli._show(GENERATED, report=report, test_file=tmp_path / "test.txt")

    assert captured["args"] == ("HIGH",)
    assert captured["importance_rationale"] == "- 이번 실행 근거"
    assert captured["has_detail"] is True


def test_falls_back_to_the_generated_rationale(captured, tmp_path):
    import typer

    report = {"result": "FAIL"}          # MCP 가 중요도를 싣지 않은 응답
    with pytest.raises(typer.Exit):      # FAIL 은 종료 코드 2 로 끝난다
        cli._show(GENERATED, report=report, test_file=tmp_path / "test.txt")

    assert captured["args"] == ("LOW",)
    assert captured["importance_rationale"] == "- 생성 시점 근거"


def test_view_choice_opens_the_saved_file(monkeypatch, tmp_path):
    """'보기' 선택은 화면 출력이 아니라 test.txt 열기로 이어진다."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("class T {}", encoding="utf-8")
    opened: list[Path] = []

    monkeypatch.setattr(cli.ui, "print_report", lambda *a, **kw: None)
    monkeypatch.setattr(ui, "open_test_file", lambda path: opened.append(path) or True)
    choices = iter(["c", None])
    monkeypatch.setattr(cli.ui, "prompt_view", lambda **kw: next(choices))

    cli._show(GENERATED, report=None, test_file=test_file)

    assert opened == [test_file]
