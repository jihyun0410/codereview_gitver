"""
프로젝트 레이아웃 탐지 — 폴더 구조를 가정하지 않는다.

예전에는 실행에 필요한 경로가 전부 상수였다.

    src/test/java/<패키지>/<클래스>.java        테스트를 쓸 자리
    build/test-results/test                      JUnit 리포트
    build/reports/jacoco/test/…Report.xml        JaCoCo 리포트
    <저장소 루트>/gradlew                        실행기
    gradle test --tests *<클래스>                실행 명령

이 값들은 **"Gradle 단일 모듈이 Git 저장소 루트에 있다"** 일 때만 맞는다. 멀티 모듈
(`api/`, `batch/`), Maven, 저장소 하위에 빌드 루트가 있는 구조에서는 전부 빗나간다 —
테스트가 엉뚱한 모듈에 쓰이고, 리포트를 못 찾아 "실패 0건인데 FAIL" 이 된다.

그래서 실제 디렉터리를 보고 정한다.

  1. 빌드 파일(build.gradle[.kts] / pom.xml)이나 src/main 을 가진 디렉터리를 모은다
  2. **테스트 대상 패키지의 main 소스를 가진 모듈**을 고른다 (가장 확실한 신호)
  3. 그 모듈 기준으로 테스트 경로·리포트 경로·실행 명령을 만든다
  4. 래퍼(gradlew/mvnw)와 빌드 루트는 모듈에서 저장소 루트까지 거슬러 올라가며 찾는다

자동 탐지로 정할 수 없는 구조(빌드 스크립트에서 sourceSets 를 직접 바꾼 경우 등)는
`.codetest/config.json` 의 `module` / `test_source_root` 또는 `--module` /
`--test-root` 로 직접 지정한다.

테스트 소스 디렉터리는 **언제나 `src/test/java`** 다. 생성물이 Java 이고, Gradle 의
java 플러그인과 Maven 의 기본 `testSourceDirectory` 가 둘 다 이 경로를 쓴다 —
Kotlin 프로젝트에서도 `.java` 는 여기로 가야 컴파일된다.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

GRADLE = "gradle"
MAVEN = "maven"

_GRADLE_BUILD_FILES = ("build.gradle", "build.gradle.kts")
_GRADLE_SETTINGS = ("settings.gradle", "settings.gradle.kts")
_MAVEN_BUILD_FILE = "pom.xml"

#: 소스 디렉터리 이름 (JVM 언어)
_LANGUAGES = ("java", "kotlin", "groovy")
#: 생성하는 테스트는 Java 다 — Gradle·Maven 모두 이 경로를 컴파일한다
_TEST_LANGUAGE = "java"

#: 모듈을 찾을 때 들어가지 않는 디렉터리 (산출물·의존성·소스 트리 내부)
_SKIP_DIRS = frozenset({
    "build", "out", "target", "node_modules", "bin", "src",
    "venv", "__pycache__", "gradle", "buildSrc",
})
#: 저장소 루트에서 몇 단계까지 모듈을 찾을지
_MAX_DEPTH = 4


class LayoutError(RuntimeError):
    """테스트를 어디에 쓰고 어떻게 돌릴지 정할 수 없는 상태."""


# ===========================================================================
#  레이아웃
# ===========================================================================
@dataclass(frozen=True)
class Layout:
    """이 프로젝트에서 테스트를 어디에 쓰고 어떻게 돌리는지."""

    repo_root: Path
    #: 테스트가 속할 빌드 모듈 (단일 모듈이면 repo_root 와 같다)
    module_dir: Path
    #: 명령을 실행할 디렉터리 — 래퍼와 settings 가 있는 빌드 루트
    build_root: Path
    tool: str
    test_source_root: Path
    junit_dir: Path
    coverage_report: Path
    jacoco: bool

    # --- 경로 ----------------------------------------------------------
    @property
    def module_path(self) -> str:
        """빌드 루트 기준 모듈 상대 경로. `""` 면 빌드 루트 자신이다."""
        relative = _relative(self.module_dir, self.build_root)
        return "" if relative in (".", "") else relative

    def test_file(self, package: str, class_name: str) -> Path:
        directory = self.test_source_root
        if package:
            directory = directory.joinpath(*package.split("."))
        return directory / f"{class_name}.java"

    def relative_test_file(self, package: str, class_name: str) -> str:
        """저장소 루트 기준 상대 경로 (리포트·화면에 그대로 쓴다)."""
        return _relative(self.test_file(package, class_name), self.repo_root)

    # --- 실행 ----------------------------------------------------------
    def command(self, class_name: str, gradle_command: str, maven_command: str) -> list[str]:
        """이 테스트 클래스 하나만 돌리고 커버리지 리포트까지 만드는 명령."""
        if self.tool == MAVEN:
            return self._maven_command(class_name, maven_command)
        return self._gradle_command(class_name, gradle_command)

    def _gradle_command(self, class_name: str, fallback: str) -> list[str]:
        # 멀티 모듈에서 `test` 만 부르면 모든 모듈이 돌아간다. `:api:test` 로 짚는다.
        prefix = f":{self.module_path.replace('/', ':')}:" if self.module_path else ""
        command = [*self.launcher(GRADLE, fallback), f"{prefix}test", "--tests", f"*{class_name}"]
        if self.jacoco:
            command.append(f"{prefix}jacocoTestReport")
        command.append("--console=plain")
        return command

    def _maven_command(self, class_name: str, fallback: str) -> list[str]:
        command = [*self.launcher(MAVEN, fallback), "-B"]
        if self.module_path:
            # -am 은 의존 모듈까지 빌드한다. -Dtest 에 걸리는 테스트가 없는 모듈은
            # failIfNoTests=false 덕에 그냥 지나간다.
            command += ["-pl", self.module_path, "-am"]
        command += ["test", f"-Dtest={class_name}", "-DfailIfNoTests=false"]
        if self.jacoco:
            command.append("jacoco:report")
        return command

    def launcher(self, tool: str, fallback_command: str) -> list[str]:
        """래퍼(gradlew/mvnw) 우선, 없으면 PATH 의 실행 파일."""
        wrappers = (
            (("gradlew.bat", []), ("gradlew", ["sh"]))
            if tool == GRADLE
            else (("mvnw.cmd", []), ("mvnw", ["sh"]))
        )
        for directory in _ancestors(self.build_root, self.repo_root):
            for name, prefix in wrappers:
                wrapper = directory / name
                if wrapper.is_file():
                    return [*prefix, str(wrapper)]

        executable = shutil.which(fallback_command)
        if executable is None:
            wrapper_names = " / ".join(name for name, _ in wrappers)
            raise LayoutError(
                f"{tool} 을(를) 찾을 수 없습니다 ({fallback_command}).\n"
                f"  · 프로젝트에 {wrapper_names} 를 두거나\n"
                f"  · {tool} 을 설치해 PATH 에 넣거나\n"
                f"  · --{'gradle' if tool == GRADLE else 'maven'} 옵션으로 실행 파일 경로를 지정하세요."
            )
        return [executable]

    # --- 표시용 --------------------------------------------------------
    def describe(self) -> str:
        name = "Gradle" if self.tool == GRADLE else "Maven"
        where = f"모듈 {self.module_path}" if self.module_path else "단일 모듈"
        return f"{name} · {where}"


# ===========================================================================
#  탐지
# ===========================================================================
def detect(
    repo_root: Path,
    package: str = "",
    hint_path: str = "",
    module_override: str = "",
    test_root_override: str = "",
) -> Layout:
    """
    실제 디렉터리를 보고 레이아웃을 정한다.

    :param package:            테스트가 속할 Java 패키지 (모듈을 고르는 가장 확실한 단서)
    :param hint_path:          MCP 가 계산해 준 저장소 기준 경로 (보조 단서)
    :param module_override:    자동 탐지 대신 쓸 모듈 경로 (저장소 기준 상대)
    :param test_root_override: 자동 탐지 대신 쓸 테스트 소스 루트 (저장소 기준 상대)
    """
    root = repo_root.resolve()
    if not root.is_dir():
        raise LayoutError(f"프로젝트 경로가 없습니다: {repo_root}")

    modules = find_modules(root)
    if module_override:
        module_dir = _inside(root, module_override, "module")
        if not module_dir.is_dir():
            raise LayoutError(f"지정한 모듈 경로가 없습니다: {module_override}")
    else:
        module_dir = _pick_module(root, modules, package, hint_path)

    tool = _detect_tool(module_dir, root)
    build_root = _build_root(module_dir, root, tool)
    test_source_root = (
        _inside(root, test_root_override, "test_source_root")
        if test_root_override
        else module_dir / "src" / "test" / _TEST_LANGUAGE
    )

    if tool == MAVEN:
        junit_dir = module_dir / "target" / "surefire-reports"
        coverage = module_dir / "target" / "site" / "jacoco" / "jacoco.xml"
    else:
        junit_dir = module_dir / "build" / "test-results" / "test"
        coverage = module_dir / "build" / "reports" / "jacoco" / "test" / "jacocoTestReport.xml"

    return Layout(
        repo_root=root,
        module_dir=module_dir,
        build_root=build_root,
        tool=tool,
        test_source_root=test_source_root,
        junit_dir=junit_dir,
        coverage_report=coverage,
        jacoco=_has_jacoco(module_dir, build_root),
    )


def find_modules(repo_root: Path) -> list[Path]:
    """빌드 파일이나 src/main 을 가진 디렉터리 — 즉 빌드 단위가 될 수 있는 곳."""
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if _is_module(directory):
            found.append(directory)
        if depth >= _MAX_DEPTH:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            walk(child, depth + 1)

    walk(repo_root, 0)
    return found


# ---------------------------------------------------------------------------
def _is_module(directory: Path) -> bool:
    """빌드 파일이 있거나(보통) 루트 스크립트로만 구성된 모듈이거나."""
    for name in (*_GRADLE_BUILD_FILES, _MAVEN_BUILD_FILE):
        if (directory / name).is_file():
            return True
    return bool(_source_dirs(directory, "main"))


def _source_dirs(module: Path, kind: str) -> list[Path]:
    """`src/<kind>/<언어>` 중 실제로 있는 것."""
    base = module / "src" / kind
    return [base / language for language in _LANGUAGES if (base / language).is_dir()]


def _owns_package(module: Path, package: str) -> bool:
    """이 모듈의 main 소스에 해당 패키지 디렉터리가 있는가."""
    if not package:
        return False
    tail = package.split(".")
    return any(source.joinpath(*tail).is_dir() for source in _source_dirs(module, "main"))


def _pick_module(repo_root: Path, modules: list[Path], package: str, hint_path: str) -> Path:
    """테스트가 속할 모듈을 고른다. 확실한 신호부터 차례로 본다."""
    # 1) 대상 패키지의 main 소스를 가진 모듈 — 테스트는 대상과 같은 모듈에 있어야 한다
    owners = [module for module in modules if _owns_package(module, package)]
    if owners:
        return min(owners, key=lambda module: _depth(module, repo_root))

    # 2) MCP 가 준 경로가 실제 소스 모듈을 가리키면 그것
    hinted = _module_of_hint(repo_root, modules, hint_path)
    if hinted is not None:
        return hinted

    # 3) 테스트 소스를 이미 가진 모듈이 하나뿐이면 그것
    with_tests = [module for module in modules if _source_dirs(module, "test")]
    if len(with_tests) == 1:
        return with_tests[0]

    # 4) main 소스를 가진 모듈이 하나뿐이면 그것
    with_main = [module for module in modules if _source_dirs(module, "main")]
    if len(with_main) == 1:
        return with_main[0]

    # 5) 남은 후보가 여럿이면 찍지 않는다 — 엉뚱한 모듈에 쓰면 원인 찾기가 더 어렵다
    candidates = with_main or modules
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return repo_root
    listed = ", ".join(_relative(module, repo_root) for module in candidates[:10])
    raise LayoutError(
        "테스트를 넣을 모듈을 정하지 못했습니다 "
        f"(패키지 {package or '(없음)'} 를 가진 모듈이 없습니다).\n"
        f"  · 후보: {listed}\n"
        "  · `--module <경로>` 로 지정하거나 .codetest/config.json 의 "
        '"module" 에 적어 두세요.'
    )


def _module_of_hint(repo_root: Path, modules: list[Path], hint_path: str) -> Path | None:
    """`api/src/test/java/…` 처럼 모듈이 실린 경로에서 모듈을 읽는다."""
    if not hint_path:
        return None
    normalized = hint_path.replace("\\", "/").strip("/")
    marker = "src/test/"
    if marker not in normalized:
        return None
    head = normalized.split(marker, 1)[0].strip("/")
    candidate = (repo_root / head) if head else repo_root
    if candidate in modules and (_source_dirs(candidate, "main") or _source_dirs(candidate, "test")):
        return candidate
    return None


def _detect_tool(module_dir: Path, repo_root: Path) -> str:
    """모듈에서 저장소 루트로 올라가며 처음 만나는 빌드 파일로 정한다."""
    for directory in _ancestors(module_dir, repo_root):
        if any((directory / name).is_file() for name in _GRADLE_BUILD_FILES):
            return GRADLE
        if (directory / _MAVEN_BUILD_FILE).is_file():
            return MAVEN
    return GRADLE


def _build_root(module_dir: Path, repo_root: Path, tool: str) -> Path:
    """명령을 실행할 디렉터리 — 같은 빌드에 속하는 가장 바깥 디렉터리."""
    markers = (
        (*_GRADLE_SETTINGS, "gradlew", "gradlew.bat")
        if tool == GRADLE
        else ("mvnw", "mvnw.cmd", _MAVEN_BUILD_FILE)
    )
    root = module_dir
    for directory in _ancestors(module_dir, repo_root):
        if any((directory / name).exists() for name in markers):
            root = directory          # 더 바깥에서 찾으면 그쪽이 빌드 루트다
    return root


def _has_jacoco(module_dir: Path, build_root: Path) -> bool:
    """모듈과 빌드 루트의 빌드 스크립트에서 jacoco 적용 여부를 본다.

    멀티 모듈에서는 루트의 `subprojects { apply plugin: 'jacoco' }` 로 한 번에
    거는 경우가 많아 두 곳을 모두 본다.
    """
    seen: set[Path] = set()
    for directory in (module_dir, build_root):
        if directory in seen:
            continue
        seen.add(directory)
        for name in (*_GRADLE_BUILD_FILES, _MAVEN_BUILD_FILE):
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                if "jacoco" in candidate.read_text(encoding="utf-8", errors="ignore").lower():
                    return True
            except OSError:
                continue
    return False


# ---------------------------------------------------------------------------
def _ancestors(start: Path, stop: Path) -> list[Path]:
    """start 부터 stop 까지(양끝 포함). stop 아래가 아니면 start 하나만."""
    chain: list[Path] = []
    for path in (start, *start.parents):
        chain.append(path)
        if path == stop:
            return chain
    return [start]


def _depth(path: Path, base: Path) -> int:
    """base 로부터 몇 단계 아래인지 (base 자신은 0)."""
    relative = _relative(path, base)
    return 0 if relative in (".", "") else len(relative.split("/"))


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _inside(repo_root: Path, relative_path: str, label: str) -> Path:
    """프로젝트 안쪽 경로만 허용한다 (`..`·심볼릭 링크로 밖을 건드리지 못하게)."""
    target = (repo_root / relative_path).resolve()
    if not target.is_relative_to(repo_root):
        raise LayoutError(f"{label} 은 프로젝트 안쪽이어야 합니다: {relative_path}")
    return target
