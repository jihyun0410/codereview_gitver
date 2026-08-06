"""
`codetest` CLI.

정의서 (1) 의 명령어:
  codetest run               staging 에 올라가지 않은 파일 → 생성 + 실행 + report
  codetest run --stage       staging 에 올라간 파일       → 생성 + 실행 + report
  codetest generate          Working Tree 변경 파일       → Test Code 생성만
  codetest test              src/test/test.txt 의 Test Code 실행 + report

등록/삭제 (서버에 프로젝트 개요를 만들기 위한 준비 명령):
  codetest project register / codetest project delete

역할 분담: Test Code 생성·의도 파악·적절성 판정은 Agent(LLM)가,
@SpringBootTest 주입과 JaCoCo 실행은 MCP(코드 기반)가 담당한다.
"""

from __future__ import annotations

from pathlib import Path

import typer

from codetest import config as config_module
from codetest import runner
from codetest.api_client import EXECUTE_TIMEOUT, AgentClient, ApiError
from codetest.git_local import GitError, collect_changes, find_repo_root, git_user, read_files
from codetest.tui import renderer as ui

app = typer.Typer(help="Code Test AI Agent", add_completion=False, no_args_is_help=True)
project_app = typer.Typer(help="프로젝트 등록/삭제", no_args_is_help=True)
app.add_typer(project_app, name="project")


def _fail(message: str) -> None:
    ui.print_error(message)
    raise typer.Exit(code=1)


def _repo() -> Path:
    try:
        return find_repo_root()
    except GitError as exc:
        _fail(str(exc))
        raise  # 도달하지 않음 (typer.Exit)


def _client(repo_root: Path, timeout: float) -> tuple[AgentClient, str]:
    """서버 클라이언트와 이 저장소의 project_id 를 준비한다."""
    cfg = config_module.load(repo_root)
    if not cfg.project_id:
        _fail("등록된 프로젝트가 없습니다. `codetest project register` 를 먼저 실행하세요.")
    return AgentClient(cfg.server_url, cfg.api_key, timeout=timeout), cfg.project_id  # type: ignore[return-value]


def _collect(repo_root: Path, scope: str) -> tuple[str, list[dict]]:
    """대상 변경분을 모아 (diff, sources) 를 만든다."""
    try:
        changes = collect_changes(scope, repo_root)
    except GitError as exc:
        _fail(str(exc))
        raise

    if changes.is_empty:
        ui.print_warning("대상 변경이 없습니다.")
        raise typer.Exit(code=0)

    ui.print_changed_files(changes.files, scope)
    sources = [
        {"path": path, "content": content}
        for path, content in read_files(repo_root, [f.path for f in changes.files])
    ]
    return changes.diff, sources


# ===========================================================================
#  project register / delete
# ===========================================================================
@project_app.command("register")
def project_register(
    name: str | None = typer.Option(None, "--name", "-n", help="프로젝트 명 (기본: 디렉터리 이름)"),
    owner: str | None = typer.Option(None, "--owner", "-o", help="담당자 (기본: git user.name)"),
    github_token: str | None = typer.Option(None, "--token", "-t", help="Github API Token"),
) -> None:
    """명령어를 입력한 환경의 Project 를 등록하고 필요한 정보를 서버로 전달한다."""
    repo_root = _repo()
    changes = collect_changes("staged", repo_root)  # 원격 URL / 브랜치 조회용

    if not changes.remote_url:
        _fail("origin 원격이 없습니다. `git remote add origin <URL>` 후 다시 실행하세요.")

    payload = {
        "name": name or repo_root.name,
        "git_url": changes.remote_url,
        "owner": owner or git_user(repo_root),
        "github_token": github_token,
        "default_branch": changes.branch,
    }

    cfg = config_module.load(repo_root)
    ui.print_header("codetest project register", payload["git_url"])
    try:
        created = AgentClient(cfg.server_url, cfg.api_key, timeout=120.0).create_project(**payload)
    except ApiError as exc:
        _fail(str(exc))
        return

    config_module.save_project_id(repo_root, created["id"])
    ui.print_success(f"등록 완료: {created['name']} ({created['id']})")
    ui.print_info("MCP 가 전체 소스를 AST 로 파싱해 프로젝트 개요를 만들고 있습니다.")


@project_app.command("delete")
def project_delete(
    yes: bool = typer.Option(False, "--yes", "-y", help="확인 없이 삭제"),
) -> None:
    """등록한 project 에 대한 정보를 삭제한다."""
    repo_root = _repo()
    cfg = config_module.load(repo_root)
    if not cfg.project_id:
        _fail("이 저장소에 등록된 프로젝트가 없습니다.")

    if not yes and not typer.confirm(f"프로젝트({cfg.project_id}) 정보를 삭제할까요?"):
        raise typer.Exit(code=0)

    try:
        AgentClient(cfg.server_url, cfg.api_key, timeout=60.0).delete_project(cfg.project_id)
    except ApiError as exc:
        _fail(str(exc))
        return

    config_module.save_project_id(repo_root, None)
    ui.print_success("프로젝트 정보를 삭제했습니다.")


# ===========================================================================
#  run / generate / test
# ===========================================================================
@app.command("run")
def run(
    stage: bool = typer.Option(
        False, "--stage", help="staging 단계에 올라간 파일을 대상으로 실행"
    ),
    timeout: float = typer.Option(EXECUTE_TIMEOUT, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """변경 파일로 Test Code 를 생성하고 @SpringBootTest 로 실행한 뒤 report 를 표시한다."""
    scope = "staged" if stage else "unstaged"
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest run", "staging 포함 변경" if stage else "staging 미포함 변경")
    diff, sources = _collect(repo_root, scope)

    ui.print_info("Test Code 생성 및 실행 중… (의도 분석 → 생성 → Gradle/JaCoCo 실행)")
    try:
        payload = client.run_tests(project_id, diff, sources, scope, timeout=timeout)
    except ApiError as exc:
        _fail(str(exc))
        return

    generated, report = payload["generated"], payload["report"]
    _save(repo_root, generated)
    _show(generated, report)


@app.command("generate")
def generate(
    timeout: float = typer.Option(300.0, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """Git Working Tree 기반 변경 파일에 대하여 Test Code 만 생성한다."""
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest generate", "Working Tree 변경분")
    diff, sources = _collect(repo_root, "worktree")

    ui.print_info("Test Code 생성 중… (의도 분석 → 생성)")
    try:
        generated = client.generate_tests(project_id, diff, sources, "worktree")
    except ApiError as exc:
        _fail(str(exc))
        return

    if not generated.get("test_code"):
        _fail("서버가 Test Code 를 생성하지 못했습니다.")

    _save(repo_root, generated)
    _show(generated, report=None)


@app.command("test")
def test(
    timeout: float = typer.Option(EXECUTE_TIMEOUT, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """src/test/test.txt 의 Test Code 를 @SpringBootTest 로 실행하고 report 를 표시한다."""
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest test", runner.TEST_FILE.as_posix())
    try:
        test_code, meta = runner.load_test(repo_root)
    except (FileNotFoundError, OSError) as exc:
        _fail(str(exc))
        return

    # 실행 대상 코드가 최신이 되도록 Working Tree 변경분을 함께 보낸다.
    try:
        changes = collect_changes("worktree", repo_root)
        sources = [
            {"path": path, "content": content}
            for path, content in read_files(repo_root, [f.path for f in changes.files])
        ]
    except GitError:
        sources = []

    ui.print_info("테스트 실행 중… (MCP: @SpringBootTest 주입 → Gradle/JaCoCo)")
    try:
        report = client.execute_tests(
            project_id,
            test_code=test_code,
            sources=sources,
            base_package=meta.get("base_package"),
            intent=meta.get("intent", ""),
            intent_rationale=meta.get("intent_rationale", ""),
            timeout=timeout,
        )
    except ApiError as exc:
        _fail(str(exc))
        return

    _show({**meta, "test_code": test_code}, report)


# ===========================================================================
#  공통 출력 흐름
# ===========================================================================
def _save(repo_root: Path, generated: dict) -> None:
    saved = runner.save_test(
        repo_root, generated.get("test_code", ""), runner.meta_from_generated(generated)
    )
    ui.print_success(f"Test Code 저장: {saved.relative_to(repo_root).as_posix()}")

    for warning in generated.get("analysis_warnings") or []:
        ui.print_warning(warning)


def _show(generated: dict, report: dict | None) -> None:
    """정의서 [결과 양식] 출력 + '보기' 선택 루프."""
    importance = generated.get("importance", "-")
    ui.print_report(
        importance,
        test_result=(report or {}).get("result"),
        has_detail=report is not None,
    )

    while True:
        choice = ui.prompt_view(has_test_code=True, has_detail=report is not None)
        if choice == "c":
            ui.print_test_code(
                generated.get("test_code", ""),
                generated.get("target_code", ""),
                generated.get("thinking", ""),
                generated.get("test_cases", ""),
                generated.get("rationale", ""),
            )
        elif choice == "r" and report is not None:
            ui.print_result_detail(report)
        else:
            break

    if report is not None and (report.get("result") or "").upper() == "FAIL":
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
