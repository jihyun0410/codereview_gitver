# Code Test AI Agent — Local Client

터미널에서 `codetest` 명령을 실행해 변경된 코드의 **Test Code 를 생성하고
`@SpringBootTest` 로 실행한 뒤 결과를 표시**하는 CLI 입니다.

## 전체 구성 (정의서: LLM = Agent / 코드 기반 = MCP, FastAPI 송·수신)

```
┌───────────────────────────────┐
│ local-client (이 저장소)      │  CLI 명령 · TUI 결과 · git diff 수집
│   codetest run [--stage]      │
│   codetest generate           │
│   codetest test               │
└───────────────┬───────────────┘
                │ MCP tools/call (X-API-Key)
                ▼
┌───────────────────────────────┐
│ MCP (codetest-MCP)      :80   │  코드 기반 처리 (LLM 미사용)
│   · Git clone + AST → 개요 DB │
│   · Diff + AST 변경 단위 식별 │
│   · 기능 중요도 High/Mid/Low  │    ← 판단 근거도 함께 만든다
│   · @SpringBootTest 주입      │
│   · Gradle + JaCoCo 실행      │
└───────────────┬───────────────┘
                │ REST (X-API-Key)
                ▼
┌───────────────────────────────┐
│ Agent   (codetest)      :8000 │  LLM 판단
│   · 변경 의도 파악            │    기능 추가 / 조건 변경 / 성능 개선 …
│   · 사고의 사슬(생각 과정)    │
│   · 정상/실패 케이스 판단     │
│   · @SpringBootTest 코드 생성 │
│   · 결과 적절성 판단과 근거   │
└───────────────────────────────┘
```

클라이언트는 **MCP 만** 알면 됩니다. MCP ↔ Agent 통신은 MCP 가 처리합니다.

### 테스트는 이 PC 에서 실행됩니다

`run` / `test` 의 Gradle 실행은 **명령을 입력한 이 PC 의 프로젝트**에서 이뤄집니다.
방금 고친 코드가 그대로 있는 작업 트리라 사본을 만들 필요가 없고, 서버에 JDK·Gradle
을 둘 필요도 없습니다.

```
codetest run / test
  1. prepare_test      MCP  @SpringBootTest 주입 + 저장 경로 추정
  2. 빌드 실행         CLI  이 PC 의 프로젝트에서 실행  ← JDK·빌드 도구가 여기 필요
  3. report_execution  MCP  중요도 재판정 → Agent 적절성 판정
```

* 이 PC 에 **JDK** 와 **Gradle 또는 Maven**(또는 프로젝트의 `gradlew`/`mvnw`)이 필요합니다
* 래퍼가 없으면 시스템 `gradle`/`mvn` 을 씁니다. `--gradle` / `--maven` 으로 경로를 지정할 수 있습니다
* 실행이 끝나면 **주입했던 테스트 파일만** 지웁니다. 미커밋 변경분은 건드리지 않습니다

### 폴더 구조는 프로젝트마다 다릅니다

테스트를 쓸 자리·리포트 경로·실행 명령을 상수로 두면 **"Gradle 단일 모듈이 저장소
루트에 있는"** 프로젝트에서만 동작합니다. 그래서 실행 직전에 실제 디렉터리를 보고
정합니다 (`codetest/project_layout.py`).

| 항목 | 어떻게 정하나 |
|---|---|
| 빌드 도구 | `build.gradle[.kts]` → Gradle, `pom.xml` → Maven (모듈에서 루트로 올라가며 확인) |
| 모듈 | **테스트 대상 패키지의 `src/main` 을 가진 모듈**. 그 다음 서버가 준 경로, 소스를 가진 유일한 모듈 순 |
| 테스트 파일 | `<모듈>/src/test/java/<패키지>/<클래스>.java` |
| 실행 위치 | 래퍼(`gradlew`/`mvnw`)와 `settings.gradle`/최상위 `pom.xml` 이 있는 빌드 루트 |
| 실행 명령 | Gradle `:<모듈>:test --tests *<클래스>` / Maven `-pl <모듈> -am test -Dtest=<클래스>` |
| JUnit 리포트 | Gradle `<모듈>/build/test-results/test` / Maven `<모듈>/target/surefire-reports` |
| 커버리지 | Gradle `<모듈>/build/reports/jacoco/test/jacocoTestReport.xml` / Maven `<모듈>/target/site/jacoco/jacoco.xml` |

그래서 이런 구조가 모두 됩니다.

```
단일 모듈            멀티 모듈                 빌드 루트가 하위        Maven
repo/                repo/                     repo/                  repo/
├─ build.gradle      ├─ settings.gradle        ├─ docs/               ├─ pom.xml
└─ src/main/java     ├─ api/  ← 여기에 쓴다    └─ backend/            ├─ api/  ← 여기
                     └─ batch/                    ├─ gradlew          └─ batch/
                                                  └─ src/main/java
```

몇 가지 규칙이 더 있습니다.

* 테스트 소스 디렉터리는 언제나 `src/test/java` 입니다. 생성물이 Java 이고, Gradle 의
  java 플러그인과 Maven 의 기본 `testSourceDirectory` 가 둘 다 이 경로를 컴파일하므로
  **Kotlin 프로젝트에서도** 여기에 두어야 합니다.
* 멀티 모듈에서 **어느 모듈인지 정할 수 없으면 찍지 않고 멈춥니다.** 엉뚱한 모듈에
  쓰면 원인을 찾기가 더 어렵기 때문입니다 — 후보를 보여 주고 `--module` 을 권합니다.
* 빌드 스크립트에서 소스 경로를 직접 바꾼 프로젝트(`sourceSets` 재정의 등)는 자동으로
  알 수 없습니다. `--module` / `--test-root` 또는 `.codetest/config.json` 에
  `module` / `test_source_root` 로 지정하세요.

## 설치

```bash
pip install git+https://github.com/jihyun0410/codereview_gitver.git
```

## 명령어 (정의서 (1))

| 명령 | 대상 | 동작 |
|---|---|---|
| `codetest run` | staging 에 올라가지 않은 변경 | 생성 + 실행 + report |
| `codetest run --stage` | staging 에 올라간 변경 | 생성 + 실행 + report |
| `codetest generate` | Git Working Tree 변경 | Test Code 생성만 |
| `codetest test` | `src/test/test.txt` | 실행 + report |

준비 명령:

```bash
codetest project register                              # 최초 1회
codetest project delete
```

### `register` 가 커밋된 소스를 함께 올리는 이유

`generate`/`run`/`test` 는 `git diff HEAD` 기준이라 **미커밋 변경분만** 보냅니다.
그것만 Agent 에 넘기면 LLM 이 변경 지점이 호출하는 **커밋된 구현**을 못 봐서
구조만 보고 테스트를 만들게 됩니다.

그래서 `register` 가 커밋된 소스(`git ls-files`)를 함께 올려 MCP 에 저장해 둡니다.
이후 실행에서 MCP 가 그 스냅샷 위에 미커밋 변경분을 덮어 "현재 코드" 를 만들어
Agent 에 넘깁니다.

* 대상 확장자: `.java .kt .xml .sql .py .js .ts .tsx .jsx .gradle .properties .yml .yaml`
* 상한: 파일 800개 / 총 8MB. 초과하면 경고를 띄우고 잘라서 보냅니다
* 커밋이 쌓여 스냅샷이 낡았으면 `codetest project register` 를 다시 실행하면 갱신됩니다

## 결과 양식 (정의서 [UI])

```
╭──────────────────────────────────────────────────────────────────────╮
│ 결과                                                                 │
│ 기능 중요도             HIGH                                         │  ← (4) High / Mid / Low
│ 중요도 판단 근거        - 영향도 점수 62점 → HIGH (기준: 55점 이상…) │  ← 등급이 나온 이유
│                         - 승격: 사용자 노출 진입점 1개와 SQL 실행…   │
│ TEST CODE               보기                                         │  ← (2) 클릭 → test.txt
│ TEST RESULT             FAIL                                         │
│ TEST RESULT 상세 보기   보기                                         │  ← (3) 클릭 → test-result.txt
╰──────────────────────────────────────────────────────────────────────╯
```

* **기능 중요도 / 중요도 판단 근거** — MCP 가 코드 그래프로 확정한 등급과, 그 등급이
  나온 이유(점수 기준, 진입점·SQL 승격 사유, 변경 규모)를 함께 보여 줍니다.
* **`TEST CODE` 의 `보기`** — 클릭하면 생성된 `src/test/test.txt` 가 OS 기본
  프로그램으로 열립니다. 링크 클릭을 지원하지 않는 터미널에서는 아래 프롬프트의
  `[c]` 가 같은 파일을 엽니다. 파일과 별개로 **생각 과정(사고의 사슬)**,
  정상/실패 케이스 판단, 작성 근거는 터미널에 이어서 출력됩니다.
* **`TEST RESULT 상세 보기` 의 `보기`** — `TEST CODE` 쪽과 **똑같이 동작합니다.**
  클릭하면 `src/test/test-result.txt` 가 열리고, 링크를 지원하지 않는 터미널에서는
  `[r]` 이 같은 파일을 엽니다. 결과 값, **파악한 변경 의도와 근거**, 실행 집계,
  JaCoCo 커버리지, 실패 내역, 적절성 판단 결과와 근거가 들어 있습니다.
  **Gradle 실행 출력은 자르지 않고 전부 넣습니다** — 터미널에서는 길이 때문에
  잘라야 하지만, 파일로 여는 편을 택한 이유가 바로 그것입니다.

`PASS`/`FAIL` 은 **gradle 종료 코드**가 정합니다. 컴파일이 깨지면 테스트가 시작조차
못해 집계가 전부 0 인데도 `FAIL` 이 되므로, 그럴 때는 결과 값 맨 위에
`빌드: 실패 — 테스트가 한 건도 실행되지 않았습니다` 와 함께 컴파일 오류를 따로
보여 줍니다. **테스트 실패와 빌드 실패는 다른 절에 나옵니다.**

집계는 **이번에 실행한 테스트 클래스의 리포트만** 셉니다. 실행 전에 그 클래스의
지난 리포트를 지우므로, 예전 실행의 성공 건수가 이번 결과에 섞이지 않습니다.

두 `보기` 모두 파일을 열 수 없는 환경(원격 셸 등)에서만 내용을 터미널에 대신
출력합니다.

## 설정

`~/.codetest/config.json` (전역) 또는 `<repo>/.codetest/config.json` (저장소별).
환경변수가 파일보다 우선합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CODETEST_SERVER_URL` | (config.py 기본값) | MCP 엔드포인트 주소 |
| `CODETEST_API_KEY` | (없음) | MCP 인증 키 (`X-API-Key`) |

저장소별 설정(`<repo>/.codetest/config.json`)에는 프로젝트 구조를 덮어쓸 수 있습니다.
**평소에는 비워 둡니다** — 자동 탐지가 맞지 않을 때만 씁니다.

| 키 | 기본값 | 설명 |
|---|---|---|
| `module` | (자동 탐지) | 테스트를 넣을 빌드 모듈 (예: `"api"`) |
| `test_source_root` | (자동 탐지) | 테스트 소스 루트 (예: `"api/src/test/java"`) |
| `gradle_command` | `gradle` | `gradlew` 가 없을 때 쓸 실행 파일 |
| `maven_command` | `mvn` | `mvnw` 가 없을 때 쓸 실행 파일 |

## 연결이 끊길 때

    RemoteProtocolError: peer closed connection without sending complete
    message body (incomplete chunked read)

"서버가 응답 본문을 끝맺지 않고 연결을 닫았다" 는 뜻입니다. 보낸 요청이 틀려서가
아니라 **상대가 중간에 사라진 것**이라, CLI 에서 고칠 수 있는 값은 없습니다.

MCP 응답은 SSE 스트림이고, MCP 는 도구 결과를 보낸 **뒤에** 스트림을 닫습니다.
그 마지막 닫힘만 앞단에서 잘리는 경우가 있어, `api_client` 는 끊긴 시점에 내 요청에
대한 응답을 이미 받았으면 **그것을 그대로 씁니다** — 끝난 생성·실행이 실패로 보이지
않습니다. 한 줄도 못 받고 끊긴 경우에만 오류로 올리고, 그때도 트레이스백 대신 어디를
봐야 하는지(서버 로그·프록시 타임아웃) 짚어 줍니다.

볼 곳은 세 군데입니다.

| 확인 | 무엇을 보나 |
|---|---|
| MCP 서버 로그 | 처리 중 예외·OOM·재시작이 있었는지 |
| 앞단 프록시(nginx/LB) | `proxy_read_timeout`, `proxy_buffering off` |
| Agent 로그 | LLM 게이트웨이 구간에서 같은 오류가 났는지 |

## 산출물

| 경로 | 내용 |
|---|---|
| `src/test/test.txt` | 생성된 Test Code (정의서 (1) `codetest test` 대상) |
| `src/test/test-result.txt` | 실행 결과 상세 (`TEST RESULT 상세 보기` 가 여는 파일) |
| `.codetest/config.json` | 저장소별 `project_id` |
| `.codetest/last_test.json` | 파악한 의도·중요도와 근거·기준 패키지 등 재실행용 정보 |

이 파일들은 "수정이 발생한 소스 코드" 수집 대상에서 자동으로 제외됩니다
(생성한 테스트가 다음 실행의 테스트 대상이 되는 자기 오염 방지).
