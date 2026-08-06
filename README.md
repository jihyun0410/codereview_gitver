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
                │ REST (X-API-Key)
                ▼
┌───────────────────────────────┐
│ Agent   (codetest)      :8000 │  LLM 판단
│   · 변경 의도 파악            │    기능 추가 / 조건 변경 / 성능 개선 …
│   · 사고의 사슬(생각 과정)    │
│   · 정상/실패 케이스 판단     │
│   · @SpringBootTest 코드 생성 │
│   · 기능 중요도 High/Mid/Low  │
│   · 결과 적절성 판단과 근거   │
└───────────────┬───────────────┘
                │ REST (X-API-Key)
                ▼
┌───────────────────────────────┐
│ MCP (codetest-MCP)      :8100 │  코드 기반 처리 (LLM 미사용)
│   · Git clone + AST → 개요 DB │
│   · Diff + AST 변경 단위 식별 │
│   · @SpringBootTest 주입      │
│   · Gradle + JaCoCo 실행      │
└───────────────────────────────┘
```

클라이언트는 **Agent 만** 알면 됩니다. Agent ↔ MCP 통신은 Agent 가 처리합니다.

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
codetest project register --token <GitHub_API_Token>   # 최초 1회
codetest project delete
```

## 결과 양식 (정의서 [UI])

```
╭──────────────────────────────────────────╮
│ 결과                                     │
│ 기능 중요도             HIGH             │   ← (4) High / Mid / Low
│ TEST CODE               보기             │   ← (2)
│ TEST RESULT             FAIL             │
│ TEST RESULT 상세 보기   보기             │   ← (3)
╰──────────────────────────────────────────╯
```

* `[c] TEST CODE 보기` — 테스트를 진행한 코드, 생성된 `@SpringBootTest` 코드,
  **생각 과정(사고의 사슬)**, 정상/실패 케이스 판단, 작성 근거
* `[r] TEST RESULT 상세 보기` — 결과 값, **파악한 변경 의도와 근거**,
  실행 집계, JaCoCo 커버리지, 실패 내역, 적절성 판단 결과와 근거

## 설정

`~/.codetest/config.json` (전역) 또는 `<repo>/.codetest/config.json` (저장소별).
환경변수가 파일보다 우선합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CODETEST_SERVER_URL` | `http://localhost:8000` | Agent 주소 |
| `CODETEST_API_KEY` | (없음) | Agent 인증 키 |

## 산출물

| 경로 | 내용 |
|---|---|
| `src/test/test.txt` | 생성된 Test Code (정의서 (1) `codetest test` 대상) |
| `.codetest/config.json` | 저장소별 `project_id` |
| `.codetest/last_test.json` | 파악한 의도·중요도·기준 패키지 등 재실행용 정보 |

이 파일들은 "수정이 발생한 소스 코드" 수집 대상에서 자동으로 제외됩니다
(생성한 테스트가 다음 실행의 테스트 대상이 되는 자기 오염 방지).
