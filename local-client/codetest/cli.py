"""
`codetest` CLI — 5개 명령만 제공한다.

  codetest project register  등록 환경의 Project 를 서버에 등록
  codetest project delete    등록한 project 정보 삭제
  codetest run               staging 에 올라가지 않은 파일 → Test Code 생성 + 실행 + report
  codetest run --stage       staging 에 올라간 파일 → Test Code 생성 + 실행 + report
  codetest generate          Working Tree 변경 파일 → Test Code 생성만
  codetest test              src/test/test.txt 의 Test Code 실행 + report

Test Code 생성과 결과 판정은 Agent Server 가, 테스트 실행은 로컬이 담당한다.
"""

from __future__ import annotations

from pathlib import Path

import typer

from codetest import config as config_module
from codetest import runner
from codetest.api_client import AgentClient, ApiError
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


def _client(repo_root: Path, timeout: float = 300.0) -> tuple[AgentClient, str]:
    """서버 클라이언트와 이 저장소의 project_id 를 준비한다."""
    cfg = config_module.load(repo_root)
    if not cfg.project_id:
        _fail("등록된 프로젝트가 없습니다. `codetest project register` 를 먼저 실행하세요.")
    return AgentClient(cfg.server_url, cfg.api_key, timeout=timeout), cfg.project_id  # type: ignore[return-value]


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
    ui.print_info("서버가 전체 소스를 수집해 Graph / Workflow 를 생성하고 있습니다.")


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
    timeout: float = typer.Option(300.0, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """변경 파일을 참고하여 Test Code 를 생성하고 실행한 뒤 report 를 표시한다."""
    scope = "staged" if stage else "unstaged"
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest run", "staging 포함 변경" if stage else "staging 미포함 변경")
    generated = _generate(client, project_id, repo_root, scope)
    _execute_and_report(client, project_id, repo_root, generated["test_code"], generated,
                        importance=generated["importance"])


@app.command("generate")
def generate(
    timeout: float = typer.Option(300.0, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """Git Working Tree 기반 변경 파일에 대하여 Test Code 만 생성한다."""
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest generate", "Working Tree 변경분")
    generated = _generate(client, project_id, repo_root, "worktree")

    ui.print_report(generated["importance"], test_result=None, has_detail=False)
    if ui.prompt_view(has_test_code=True, has_detail=False) == "c":
        _show_code(generated)


@app.command("test")
def test(
    timeout: float = typer.Option(300.0, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """src/test/test.txt 의 Test Code 를 가져와 실행하고 report 를 표시한다."""
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest test", runner.TEST_FILE.as_posix())
    try:
        test_code, meta = runner.load_test(repo_root)
    except (FileNotFoundError, OSError) as exc:
        _fail(str(exc))
        return

    _execute_and_report(
        client, project_id, repo_root, test_code, meta,
        importance=meta.get("importance", "-"),
    )


# ===========================================================================
#  내부 공통 흐름
# ===========================================================================
def _generate(client: AgentClient, project_id: str, repo_root: Path, scope: str) -> dict:
    """변경분을 모아 서버에 Test Code 생성을 요청하고 test.txt 에 저장한다."""
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

    ui.print_info("Test Code 생성 중…")
    try:
        generated = client.generate_tests(project_id, changes.diff, sources, scope)
    except ApiError as exc:
        _fail(str(exc))
        raise

    if not generated.get("test_code"):
        _fail("서버가 Test Code 를 생성하지 못했습니다.")

    saved = runner.save_test(
        repo_root,
        generated["test_code"],
        {
            "language": generated.get("language", "python"),
            "file_extension": generated.get("file_extension", ".py"),
            "run_command": generated.get("run_command", runner.DEFAULT_META["run_command"]),
            "importance": generated.get("importance", "LOW"),
            "importance_rationale": generated.get("importance_rationale", ""),
            "rationale": generated.get("rationale", ""),
            "target_code": generated.get("target_code", ""),
        },
    )
    ui.print_success(f"Test Code 저장: {saved.relative_to(repo_root).as_posix()}")
    return generated


def _execute_and_report(
    client: AgentClient,
    project_id: str,
    repo_root: Path,
    test_code: str,
    meta: dict,
    importance: str,
) -> None:
    """테스트를 실행하고 서버 판정을 받아 결과 화면을 출력한다."""
    ui.print_info("테스트 실행 중…")
    result = runner.run_test(repo_root, test_code, meta)

    try:
        judged = client.report_tests(
            project_id,
            test_code=test_code,
            output=result.output,
            exit_code=result.exit_code,
            language=meta.get("language", "python"),
        )
    except ApiError as exc:
        _fail(str(exc))
        return

    ui.print_report(importance, judged.get("result"))

    # '보기' 선택 루프 — 비대화형이면 즉시 종료한다.
    while True:
        choice = ui.prompt_view(has_test_code=True, has_detail=True)
        if choice == "c":
            _show_code({**meta, "test_code": test_code})
        elif choice == "r":
            ui.print_result_detail(
                judged.get("result", ""),
                judged.get("verdict", ""),
                judged.get("verdict_rationale", ""),
                judged.get("details", ""),
                result.output,
            )
        else:
            break

    if (judged.get("result") or "").upper() == "FAIL":
        raise typer.Exit(code=2)


def _show_code(payload: dict) -> None:
    ui.print_test_code(
        payload.get("language", "text"),
        payload.get("test_code", ""),
        payload.get("target_code", ""),
        payload.get("rationale", ""),
    )


if __name__ == "__main__":
    app()
