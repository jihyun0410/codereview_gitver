"""프로젝트 레이아웃 탐지 검증.

예전에는 실행 경로가 전부 상수라서 **Gradle 단일 모듈이 저장소 루트에 있는**
프로젝트에서만 동작했다. 여기서는 그 가정을 하나씩 깨는 구조를 만들어, 테스트가
올바른 모듈에 쓰이고 올바른 명령·리포트 경로가 나오는지 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codetest import project_layout
from codetest.project_layout import GRADLE, MAVEN, LayoutError, detect

PACKAGE = "com.example.api"


def _module(root: Path, relative: str, build_file: str, *, package: str = PACKAGE) -> Path:
    """`<relative>` 에 빌드 파일과 main 소스를 가진 모듈을 만든다."""
    module = root / relative if relative else root
    module.mkdir(parents=True, exist_ok=True)
    (module / build_file).write_text("plugins { id 'java' }", encoding="utf-8")
    source = module.joinpath("src", "main", "java", *package.split("."))
    source.mkdir(parents=True, exist_ok=True)
    (source / "Thing.java").write_text(f"package {package};\nclass Thing {{}}\n", encoding="utf-8")
    return module


@pytest.fixture(autouse=True)
def _fake_tools(monkeypatch):
    """래퍼가 없을 때 쓰는 PATH 조회를 고정한다."""
    monkeypatch.setattr(project_layout.shutil, "which", lambda name: f"/usr/bin/{name}")


# --- 단일 모듈 (예전과 같아야 한다) ---------------------------------------------
def test_single_gradle_module_at_the_repository_root(tmp_path):
    _module(tmp_path, "", "build.gradle")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.tool == GRADLE
    assert layout.module_path == ""
    assert layout.relative_test_file(PACKAGE, "FooTest") == (
        "src/test/java/com/example/api/FooTest.java"
    )
    assert layout.junit_dir == tmp_path / "build" / "test-results" / "test"
    assert layout.command("FooTest", "gradle", "mvn")[1:] == [
        "test", "--tests", "*FooTest", "--console=plain",
    ]


# --- 멀티 모듈 -------------------------------------------------------------------
def test_multi_module_gradle_picks_the_module_owning_the_package(tmp_path):
    (tmp_path / "settings.gradle").write_text("include 'api', 'batch'", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("subprojects { apply plugin: 'java' }", encoding="utf-8")
    _module(tmp_path, "api", "build.gradle")
    _module(tmp_path, "batch", "build.gradle", package="com.example.batch")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.module_dir == tmp_path / "api"
    assert layout.build_root == tmp_path              # settings.gradle 이 있는 곳에서 돈다
    assert layout.module_path == "api"
    assert layout.relative_test_file(PACKAGE, "FooTest") == (
        "api/src/test/java/com/example/api/FooTest.java"
    )
    assert layout.junit_dir == tmp_path / "api" / "build" / "test-results" / "test"
    # 모듈을 짚지 않으면 batch 까지 다 돌아간다
    assert layout.command("FooTest", "gradle", "mvn")[1:3] == [":api:test", "--tests"]


def test_jacoco_applied_only_in_the_root_script_is_still_found(tmp_path):
    """멀티 모듈은 루트에서 subprojects 로 한 번에 거는 경우가 많다."""
    (tmp_path / "settings.gradle").write_text("include 'api'", encoding="utf-8")
    (tmp_path / "build.gradle").write_text(
        "subprojects { apply plugin: 'jacoco' }", encoding="utf-8"
    )
    _module(tmp_path, "api", "build.gradle")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.jacoco is True
    assert ":api:jacocoTestReport" in layout.command("FooTest", "gradle", "mvn")


def test_an_ambiguous_multi_module_project_says_so_instead_of_guessing(tmp_path):
    """엉뚱한 모듈에 쓰면 원인 찾기가 더 어렵다 — 후보를 보여 주고 멈춘다."""
    (tmp_path / "settings.gradle").write_text("include 'api', 'batch'", encoding="utf-8")
    _module(tmp_path, "api", "build.gradle")
    _module(tmp_path, "batch", "build.gradle", package="com.example.batch")

    with pytest.raises(LayoutError) as exc:
        detect(tmp_path, package="com.other.unknown")

    assert "api" in str(exc.value) and "batch" in str(exc.value)
    assert "--module" in str(exc.value)


def test_module_override_wins_over_detection(tmp_path):
    (tmp_path / "settings.gradle").write_text("include 'api', 'batch'", encoding="utf-8")
    _module(tmp_path, "api", "build.gradle")
    _module(tmp_path, "batch", "build.gradle", package="com.example.batch")

    layout = detect(tmp_path, package="com.other.unknown", module_override="batch")

    assert layout.module_path == "batch"


# --- Maven ----------------------------------------------------------------------
def test_maven_single_module(tmp_path):
    _module(tmp_path, "", "pom.xml")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.tool == MAVEN
    assert layout.junit_dir == tmp_path / "target" / "surefire-reports"
    assert layout.coverage_report == tmp_path / "target" / "site" / "jacoco" / "jacoco.xml"
    assert layout.command("FooTest", "gradle", "mvn")[1:] == [
        "-B", "test", "-Dtest=FooTest", "-DfailIfNoTests=false",
    ]


def test_maven_multi_module_targets_one_module(tmp_path):
    (tmp_path / "pom.xml").write_text("<project><modules/></project>", encoding="utf-8")
    _module(tmp_path, "api", "pom.xml")
    _module(tmp_path, "batch", "pom.xml", package="com.example.batch")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.build_root == tmp_path
    assert layout.relative_test_file(PACKAGE, "FooTest") == (
        "api/src/test/java/com/example/api/FooTest.java"
    )
    command = layout.command("FooTest", "gradle", "mvn")
    assert command[command.index("-pl") + 1] == "api"
    assert "-am" in command                       # 의존 모듈도 빌드해야 컴파일된다


# --- 빌드 루트가 저장소 루트가 아닌 경우 -------------------------------------------
def test_build_root_below_the_repository_root(tmp_path):
    """저장소에 문서·프런트엔드가 함께 있고 백엔드만 Gradle 프로젝트인 구조."""
    (tmp_path / "docs").mkdir()
    backend = _module(tmp_path, "backend", "build.gradle")
    (backend / "settings.gradle").write_text("rootProject.name='backend'", encoding="utf-8")
    (backend / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.build_root == backend           # gradlew 가 있는 곳에서 돈다
    assert layout.module_path == ""               # 그 빌드 안에서는 루트 모듈이다
    assert layout.relative_test_file(PACKAGE, "FooTest").startswith("backend/src/test/java/")
    assert layout.command("FooTest", "gradle", "mvn")[:2] == ["sh", str(backend / "gradlew")]


# --- Kotlin ----------------------------------------------------------------------
def test_kotlin_sources_still_get_a_java_test_root(tmp_path):
    """생성물은 Java 다. Gradle 의 java 플러그인도 Maven 도 src/test/java 를 컴파일한다."""
    (tmp_path / "build.gradle.kts").write_text("plugins { kotlin(\"jvm\") }", encoding="utf-8")
    source = tmp_path.joinpath("src", "main", "kotlin", *PACKAGE.split("."))
    source.mkdir(parents=True)
    (source / "Thing.kt").write_text(f"package {PACKAGE}\n", encoding="utf-8")

    layout = detect(tmp_path, package=PACKAGE)

    assert layout.tool == GRADLE
    assert layout.relative_test_file(PACKAGE, "FooTest") == (
        "src/test/java/com/example/api/FooTest.java"
    )


# --- 서버가 준 경로는 단서일 뿐 ----------------------------------------------------
def test_the_servers_hint_is_used_only_when_the_package_does_not_decide(tmp_path):
    (tmp_path / "settings.gradle").write_text("include 'api', 'batch'", encoding="utf-8")
    _module(tmp_path, "api", "build.gradle")
    _module(tmp_path, "batch", "build.gradle", package="com.example.batch")

    layout = detect(tmp_path, hint_path="batch/src/test/java/com/example/batch/FooTest.java")

    assert layout.module_path == "batch"


def test_a_stale_single_module_hint_does_not_beat_the_package(tmp_path):
    """MCP 가 단일 모듈로 추정해도, 패키지를 가진 모듈이 있으면 그쪽이 맞다."""
    (tmp_path / "settings.gradle").write_text("include 'api'", encoding="utf-8")
    _module(tmp_path, "api", "build.gradle")

    layout = detect(
        tmp_path, package=PACKAGE, hint_path="src/test/java/com/example/api/FooTest.java"
    )

    assert layout.module_path == "api"


# --- 모듈 탐색이 산출물 디렉터리를 헤집지 않아야 한다 --------------------------------
def test_build_output_is_not_mistaken_for_a_module(tmp_path):
    _module(tmp_path, "", "build.gradle")
    stale = tmp_path / "build" / "tmp" / "sub"
    stale.mkdir(parents=True)
    (stale / "build.gradle").write_text("", encoding="utf-8")

    assert project_layout.find_modules(tmp_path) == [tmp_path]
