"""
`codetest` CLI.

정의서 (1) 의 명령어:
  codetest run               staging 에 올라가지 않은 파일 → 생성 + 실행 + report
  codetest run --stage       staging 에 올라간 파일       → 생성 + 실행 + report
  codetest generate          Working Tree 변경 파일       → Test Code 생성만
  codetest test              src/test/test.txt 의 Test Code 실행 + report

등록/삭제 (서버에 프로젝트 개요를 만들기 위한 준비 명령):
  codetest project register / codetest project delete

역할 분담(흐름: CLI → MCP → Agent):
  · MCP  — Git Diff/AST 변경 단위 식별, 기능 중요도 판정과 근거,
           @SpringBootTest 주입, Gradle/JaCoCo 실행 (코드 기반)
  · Agent — 변경 의도 파악, 사고의 사슬, Test Code 작성, 결과 적절성 판정 (LLM)

CLI 는 MCP 하나만 알면 된다. MCP ↔ Agent 통신은 MCP 가 처리한다.
"""

from __future__ import annotations

from pathlib import Path

import typer

from codetest import config as config_module
from codetest import executor, runner
from codetest.api_client import EXECUTE_TIMEOUT, AgentClient, ApiError

#: 등록은 커밋 소스 스냅샷을 함께 올려 본문이 커진다.
REGISTER_TIMEOUT = 600.0
from codetest.git_local import (
    GitError,
    collect_changes,
    collect_committed_files,
    find_repo_root,
    git_user,
    read_files,
)
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
        "default_branch": changes.branch,
    }

    cfg = config_module.load(repo_root)
    client = AgentClient(cfg.server_url, cfg.api_key, timeout=REGISTER_TIMEOUT)
    ui.print_header("codetest project register", payload["git_url"])
    # 프로젝트 정보를 어느 주소로 보내는지 먼저 보여 준다 (서버 주소 오설정을 바로 확인)
    ui.print_info(f"전송 대상: {client.describe('register_project')}", soft_wrap=True)

    # 커밋된 소스를 함께 올린다. generate/run/test 는 미커밋 변경분만 보내므로,
    # 이걸 올려 둬야 MCP 가 그 위에 변경분을 덮어 "현재 코드" 를 Agent 에 넘긴다.
    try:
        committed, warnings = collect_committed_files(repo_root)
    except GitError as exc:
        _fail(str(exc))
        return
    for warning in warnings:
        ui.print_warning(warning)
    ui.print_info(f"커밋된 소스 {len(committed)}개를 함께 전송합니다.")
    payload["sources"] = [{"path": path, "content": content} for path, content in committed]

    try:
        created = client.create_project(**payload)
    except ApiError as exc:
        _fail(str(exc))
        return

    project_id = created.get("id") or created.get("project_id")
    if not project_id:
        _fail(f"서버 응답에 project_id 가 없습니다: {created}")
    config_module.save_project_id(repo_root, project_id)
    ui.print_success(f"등록 완료: {created.get('name', repo_root.name)} ({project_id})")
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

    client = AgentClient(cfg.server_url, cfg.api_key, timeout=60.0)
    ui.print_info(f"전송 대상: {client.describe('delete_project')}", soft_wrap=True)
    try:
        client.delete_project(cfg.project_id)
    except ApiError as exc:
        _fail(str(exc))
        return

    config_module.save_project_id(repo_root, None)
    ui.print_success("프로젝트 정보를 삭제했습니다.")


# ===========================================================================
#  run / generate / test
# ===========================================================================
def _execute_locally(
    repo_root: Path,
    client: AgentClient,
    project_id: str,
    generated: dict,
    diff: str,
    sources: list[dict],
    gradle: str,
    timeout: float,
) -> dict:
    """MCP 로 @SpringBootTest 를 주입받아 **이 PC 의 프로젝트에서** 실행한다.

    실행은 로컬에서 하고, 중요도 재판정과 결과 적절성 판단만 서버에 맡긴다.
    """
    test_code = generated.get("test_code", "")

    ui.print_info("@SpringBootTest 주입 중… (MCP)")
    try:
        prepared = client.prepare_test(project_id, test_code, generated.get("base_package"))
    except ApiError as exc:
        _fail(str(exc))
        raise

    ui.print_info(f"이 PC 에서 테스트 실행 중… (Gradle: {prepared['file_path']})")
    try:
        result = executor.run_tests(
            repo_root,
            test_source=prepared["source"],
            test_file_path=prepared["file_path"],
            springboot_applied=prepared.get("springboot_applied", False),
            applied=prepared.get("applied"),
            gradle_command=gradle,
            timeout=int(timeout),
        )
    except executor.ExecutionError as exc:
        _fail(str(exc))
        raise

    ui.print_info("결과 판정 중… (MCP: 중요도 재판정 → Agent 적절성 판단)")
    try:
        return client.report_execution(
            project_id,
            execution=result.to_dict(),
            test_code=prepared["source"],
            diff=diff,
            sources=sources,
            intent=generated.get("intent", ""),
            intent_rationale=generated.get("intent_rationale", ""),
            timeout=timeout,
        )
    except ApiError as exc:
        _fail(str(exc))
        raise


@app.command("run")
def run(
    stage: bool = typer.Option(
        False, "--stage", help="staging 단계에 올라간 파일을 대상으로 실행"
    ),
    gradle: str = typer.Option("gradle", "--gradle", help="gradlew 가 없을 때 쓸 gradle 실행 파일"),
    timeout: float = typer.Option(EXECUTE_TIMEOUT, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """변경 파일로 Test Code 를 생성하고 @SpringBootTest 로 실행한 뒤 report 를 표시한다.

    생성·판정은 서버가, **테스트 실행은 이 PC 가** 한다.
    """
    scope = "staged" if stage else "unstaged"
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest run", "staging 포함 변경" if stage else "staging 미포함 변경")
    diff, sources = _collect(repo_root, scope)

    ui.print_info("Test Code 생성 중… (MCP 분석 → Agent 생성)")
    try:
        generated = client.generate_tests(project_id, diff, sources)
    except ApiError as exc:
        _fail(str(exc))
        return
    if not generated.get("test_code"):
        _fail("서버가 Test Code 를 생성하지 못했습니다.")

    saved = _save(repo_root, generated)
    report = _execute_locally(
        repo_root, client, project_id, generated, diff, sources, gradle, timeout
    )
    _show(generated, report, saved, _save_result(repo_root, report))


@app.command("generate")
def generate(
    timeout: float = typer.Option(300.0, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """Git Working Tree 기반 변경 파일에 대하여 Test Code 만 생성한다.

    결과 화면의 'TEST CODE 보기' 를 클릭하면 생성된 src/test/test.txt 가 열린다.
    """
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest generate", "Working Tree 변경분")
    diff, sources = _collect(repo_root, "worktree")

    ui.print_info("Test Code 생성 중… (의도 분석 → 생성)")
    try:
        generated = client.generate_tests(project_id, diff, sources)
    except ApiError as exc:
        _fail(str(exc))
        return

    if not generated.get("test_code"):
        _fail("서버가 Test Code 를 생성하지 못했습니다.")

    saved = _save(repo_root, generated)
    _show(generated, report=None, test_file=saved)


@app.command("test")
def test(
    gradle: str = typer.Option("gradle", "--gradle", help="gradlew 가 없을 때 쓸 gradle 실행 파일"),
    timeout: float = typer.Option(EXECUTE_TIMEOUT, "--timeout", help="서버 응답 대기 시간(초)"),
) -> None:
    """src/test/test.txt 의 Test Code 를 @SpringBootTest 로 실행하고 report 를 표시한다.

    실행은 **이 PC 의 프로젝트**에서 이뤄진다. Gradle 과 JDK 가 필요하다.
    """
    repo_root = _repo()
    client, project_id = _client(repo_root, timeout)

    ui.print_header("codetest test", runner.TEST_FILE.as_posix())
    try:
        test_code, meta = runner.load_test(repo_root)
    except (FileNotFoundError, OSError) as exc:
        _fail(str(exc))
        return

    # 중요도를 이번 실행 기준으로 다시 판단하도록 변경분을 함께 보낸다.
    try:
        changes = collect_changes("worktree", repo_root)
        diff = changes.diff
        sources = [
            {"path": path, "content": content}
            for path, content in read_files(repo_root, [f.path for f in changes.files])
        ]
    except GitError:
        diff, sources = "", []

    report = _execute_locally(
        repo_root, client, project_id, {**meta, "test_code": test_code},
        diff, sources, gradle, timeout,
    )
    _show(
        {**meta, "test_code": test_code}, report,
        repo_root / runner.TEST_FILE, _save_result(repo_root, report),
    )


# ===========================================================================
#  공통 출력 흐름
# ===========================================================================
def _save(repo_root: Path, generated: dict) -> Path:
    """Test Code 를 src/test/test.txt 로 남기고 그 경로를 돌려준다.

    결과 화면의 'TEST CODE 보기' 가 여는 파일이 바로 이 경로다.
    """
    saved = runner.save_test(
        repo_root, generated.get("test_code", ""), runner.meta_from_generated(generated)
    )
    ui.print_success(f"Test Code 저장: {saved.relative_to(repo_root).as_posix()}")

    for warning in generated.get("analysis_warnings") or []:
        ui.print_warning(warning)
    return saved


def _save_result(repo_root: Path, report: dict) -> Path | None:
    """실행 결과 상세를 src/test/test-result.txt 로 남기고 그 경로를 돌려준다.

    결과 화면의 'TEST RESULT 상세 보기' 가 여는 파일이다. 저장에 실패해도
    실행 자체는 끝난 상태이므로 경고만 남기고 진행한다 — 그때는 '보기' 가
    파일 대신 터미널 출력으로 넘어간다.
    """
    try:
        saved = runner.save_result(repo_root, ui.render_result_detail(report))
    except OSError as exc:
        ui.print_warning(f"결과 상세 파일을 저장하지 못했습니다: {exc}")
        return None
    ui.print_success(f"결과 상세 저장: {saved.relative_to(repo_root).as_posix()}")
    return saved


def _show(
    generated: dict,
    report: dict | None,
    test_file: Path,
    result_file: Path | None = None,
) -> None:
    """정의서 [결과 양식] 출력 + '보기' 선택 루프.

    두 '보기' 가 똑같이 동작한다 — 화면에 내용을 찍는 대신 각각
    `src/test/test.txt` 와 `src/test/test-result.txt` 를 연다. 표의 '보기' 는 그
    파일을 가리키는 링크라 클릭만으로 같은 동작을 한다.
    """
    # `codetest test` 는 generated 가 로컬 캐시(.codetest/last_test.json)라 값이 낡았다.
    # 이번 실행에서 MCP 가 판단한 중요도가 report 에 있으면 그것을 쓴다.
    importance = (report or {}).get("importance") or generated.get("importance", "-")
    rationale = (report or {}).get("importance_rationale") or generated.get(
        "importance_rationale", ""
    )
    ui.print_report(
        importance,
        test_result=(report or {}).get("result"),
        importance_rationale=rationale,
        test_file=test_file,
        has_detail=report is not None,
        result_file=result_file,
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
                test_file=test_file,
            )
        elif choice == "r" and report is not None:
            ui.print_result_detail(report, result_file)
        else:
            break

    if report is not None and (report.get("result") or "").upper() == "FAIL":
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
