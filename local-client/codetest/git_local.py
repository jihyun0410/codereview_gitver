"""
로컬 git 조회.

명령어별 대상 범위
  · codetest run          → staging 에 올라가지 않은 변경 (unstaged + untracked)
  · codetest run --stage  → staging 에 올라간 변경 (staged)
  · codetest generate     → Working Tree 전체 변경 (staged + unstaged + untracked)

git CLI 를 subprocess 로 호출한다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GIT_TIMEOUT = 60

#: 범위 → (diff 인자, 미추적 파일 포함 여부)
SCOPES: dict[str, tuple[list[str], bool]] = {
    "unstaged": ([], True),          # git diff
    "staged": (["--cached"], False),  # git diff --cached
    "worktree": (["HEAD"], True),     # git diff HEAD
}


class GitError(RuntimeError):
    """git 명령 실패."""


@dataclass
class ChangedFile:
    path: str
    status: str = "modified"   # added | modified | removed | renamed | copied
    additions: int = 0
    deletions: int = 0


@dataclass
class LocalChanges:
    repo_root: Path
    branch: str
    remote_url: str | None
    scope: str
    diff: str
    files: list[ChangedFile] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.diff.strip() and not self.files


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 — 고정 실행 파일(git)
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitError("git 실행 파일을 찾을 수 없습니다. git 설치를 확인하세요.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git 명령이 시간 초과되었습니다: git {' '.join(args)}") from exc

    if completed.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} 실패 (exit={completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def find_repo_root(start: Path | None = None) -> Path:
    return Path(_run_git(["rev-parse", "--show-toplevel"], cwd=start or Path.cwd()).strip())


def current_branch(repo_root: Path) -> str:
    try:
        return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).strip()
    except GitError:
        return "HEAD"


def remote_url(repo_root: Path) -> str | None:
    try:
        return _run_git(["config", "--get", "remote.origin.url"], cwd=repo_root).strip() or None
    except GitError:
        return None


def git_user(repo_root: Path) -> str:
    """담당자 기본값 — git config user.name."""
    try:
        return _run_git(["config", "--get", "user.name"], cwd=repo_root).strip() or "unknown"
    except GitError:
        return "unknown"


#: 커밋이 하나도 없는 저장소에서 HEAD 대신 쓰는 git 빈 트리 객체
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _resolve_head(repo_root: Path) -> str:
    """HEAD 가 없는(=커밋 0개) 저장소에서도 diff 가 동작하도록 빈 트리로 대체한다."""
    try:
        _run_git(["rev-parse", "--verify", "HEAD"], cwd=repo_root)
        return "HEAD"
    except GitError:
        return _EMPTY_TREE


def collect_changes(scope: str = "unstaged", repo_root: Path | None = None) -> LocalChanges:
    """지정한 범위의 변경분을 수집한다."""
    if scope not in SCOPES:
        raise GitError(f"알 수 없는 범위입니다: {scope} (가능: {', '.join(SCOPES)})")

    root = repo_root or find_repo_root()
    diff_args, include_untracked = SCOPES[scope]
    diff_args = [_resolve_head(root) if arg == "HEAD" else arg for arg in diff_args]

    diff = _run_git(["diff", *diff_args, "--unified=3"], cwd=root)

    files: list[ChangedFile] = []
    name_status = dict(
        _parse_name_status(_run_git(["diff", *diff_args, "--name-status"], cwd=root))
    )
    for line in _run_git(["diff", *diff_args, "--numstat"], cwd=root).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions, deletions, path = parts[0], parts[1], parts[-1]
        files.append(
            ChangedFile(
                path=path,
                status=name_status.get(path, "modified"),
                additions=int(additions) if additions.isdigit() else 0,
                deletions=int(deletions) if deletions.isdigit() else 0,
            )
        )

    if include_untracked:
        untracked = [
            path
            for path in _run_git(
                ["ls-files", "--others", "--exclude-standard"], cwd=root
            ).splitlines()
            if path.strip()
        ]
        for path in untracked:
            files.append(ChangedFile(path=path, status="added"))
        extra = _build_untracked_diff(root, untracked)
        if extra:
            diff = (diff + "\n" + extra) if diff.strip() else extra

    return LocalChanges(
        repo_root=root,
        branch=current_branch(root),
        remote_url=remote_url(root),
        scope=scope,
        diff=diff,
        files=files,
    )


def read_files(repo_root: Path, paths: list[str], max_bytes: int = 200_000) -> list[tuple[str, str]]:
    """변경 파일 본문을 읽어 (경로, 내용) 목록으로 돌려준다 (삭제된 파일은 건너뜀)."""
    results: list[tuple[str, str]] = []
    for path in paths:
        target = repo_root / path
        try:
            if not target.is_file() or target.stat().st_size > max_bytes:
                continue
            results.append((path, target.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return results


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    mapping = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "copied"}
    results: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        results.append((parts[-1], mapping.get(parts[0][:1], "modified")))
    return results


#: 미추적 파일 diff 생성 시 파일당 최대 줄 수
_UNTRACKED_LINE_LIMIT = 2000


def _build_untracked_diff(root: Path, paths: list[str]) -> str:
    """미추적 신규 파일을 unified diff 형식으로 합성한다 (서버 diff 파서가 인식하도록)."""
    blocks: list[str] = []
    for path in paths:
        target = root / path
        try:
            if not target.is_file() or target.stat().st_size > 512_000:
                continue
            content = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lines = content.splitlines()[:_UNTRACKED_LINE_LIMIT]
        if not lines:
            continue
        body = "\n".join(f"+{line}" for line in lines)
        blocks.append(
            f"diff --git a/{path} b/{path}\n"
            f"new file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}"
        )
    return "\n".join(blocks)
