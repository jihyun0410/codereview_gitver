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
  1. prepare_test      MCP  @SpringBootTest 주입 + 저장 경로 계산
  2. gradle test       CLI  이 PC 의 프로젝트에서 실행  ← JDK·Gradle 이 여기 필요
  3. report_execution  MCP  중요도 재판정 → Agent 적절성 판정
```

* 이 PC 에 **JDK 와 Gradle**(또는 프로젝트의 `gradlew`)이 필요합니다
* `gradlew` 가 없으면 시스템 `gradle` 을 씁니다. `--gradle` 로 경로를 지정할 수 있습니다
* 실행이 끝나면 **주입했던 테스트 파일만** 지웁니다. 미커밋 변경분은 건드리지 않습니다

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

## 산출물

| 경로 | 내용 |
|---|---|
| `src/test/test.txt` | 생성된 Test Code (정의서 (1) `codetest test` 대상) |
| `src/test/test-result.txt` | 실행 결과 상세 (`TEST RESULT 상세 보기` 가 여는 파일) |
| `.codetest/config.json` | 저장소별 `project_id` |
| `.codetest/last_test.json` | 파악한 의도·중요도와 근거·기준 패키지 등 재실행용 정보 |

이 파일들은 "수정이 발생한 소스 코드" 수집 대상에서 자동으로 제외됩니다
(생성한 테스트가 다음 실행의 테스트 대상이 되는 자기 오염 방지).
