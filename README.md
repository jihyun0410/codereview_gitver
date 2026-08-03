# Code Test AI Agent

변경된 코드에 대해 **Test Code 를 생성·실행하고 결과를 판정**하는 에이전트입니다.
기능 중요도는 AST(Tree-sitter) 기반 코드 그래프의 영향도 분석으로 산출합니다.

---

## 배포 단위 (3분할)

| 디렉터리 | 배포 위치 | 역할 |
|---|---|---|
| [`local-client/`](local-client/) | **사용자 로컬** (git clone) | `codetest` CLI + TUI. git 조회 · 테스트 실행 · 결과 표시 |
| [`agent-server/`](agent-server/) | **별도 관리 서버** | FastAPI REST. 저장소 수집 · AST/Graph · Test Code 생성 · 결과 판정 · PR Webhook |
| [`mcp-server/`](mcp-server/) | **별도 관리 서버** | MCP(stdio). Headless AST/Linter 검증 |

```
┌──────────────────────────┐        ┌─────────────────────────────────────┐
│ local-client (로컬)      │  REST  │ agent-server (관리 서버)            │
│                          │ ─────> │                                     │
│ codetest project register│        │ POST /projects        등록 + 수집   │
│ codetest run [--stage]   │        │ POST /tests/generate  Test Code 생성│
│ codetest generate        │        │ POST /tests/report    결과 판정     │
│ codetest test            │        │ POST /webhooks/github PR 분석       │
│   └ 테스트 실행 (로컬)   │        │        │ MCP(stdio)                 │
│   └ TUI 출력             │        │        v  mcp-server (AST/Linter)   │
└──────────────────────────┘        └─────────────────────────────────────┘
```

테스트 **실행**만 로컬입니다 — 개발자 환경의 의존성이 필요하기 때문입니다.

---

## 빠른 시작

```bash
# 1) Agent Server
cd agent-server && pip install -e . && cp .env.example .env   # ANTHROPIC_API_KEY 입력
uvicorn app.main:app --port 8000

# 2) MCP Server (agent-server 가 stdio 로 자동 기동)
cd mcp-server && pip install -e .

# 3) Local Client
cd local-client && pip install -e .
export CODETEST_SERVER_URL=http://localhost:8000
codetest project register
codetest run
```

---

## 명령어

| 명령 | 동작 |
|---|---|
| `codetest project register` | 현재 환경의 Project 등록 → 서버가 전체 소스 수집·Graph/Workflow 생성 |
| `codetest project delete` | 등록 정보 삭제 |
| `codetest run` | staging 미포함 변경 → Test Code 생성 + 실행 + report |
| `codetest run --stage` | staging 포함 변경 → Test Code 생성 + 실행 + report |
| `codetest generate` | Working Tree 변경 → Test Code 생성만 |
| `codetest test` | `src/test/test.txt` 실행 + report |

## TUI

```
| 결과                  |      |
| 기능 중요도           | LOW  |   ← HIGH / MID / LOW
| TEST CODE             | 보기 |   ← 대상 코드 + 작성 근거
| TEST RESULT           | PASS |
| TEST RESULT 상세 보기 | 보기 |   ← 결과 값 + 적절성 판단·근거
```

---

## 기능 → 구현 위치

| 기능 | 위치 |
|---|---|
| Test Code 생성 / 결과 판정 | [agent-server/app/services/testgen/service.py](agent-server/app/services/testgen/service.py) |
| 기능 중요도 (그래프 영향도) | [agent-server/app/services/graph/impact.py](agent-server/app/services/graph/impact.py) |
| AST 파싱 (Tree-sitter) | [agent-server/app/services/parsing/](agent-server/app/services/parsing/) |
| Workflow 생성 · PR 동기화 | [agent-server/app/services/workflow/generator.py](agent-server/app/services/workflow/generator.py), [graph/sync.py](agent-server/app/services/graph/sync.py) |
| PR Webhook 분석 | [agent-server/app/api/v1/webhooks.py](agent-server/app/api/v1/webhooks.py) |
| 이중 검증 (AST & Linter MCP) | [mcp-server/codetest_mcp/](mcp-server/codetest_mcp/) |
| 감사 로그 | [agent-server/app/services/audit/audit_log.py](agent-server/app/services/audit/audit_log.py) |
| 테스트 실행 (로컬) | [local-client/codetest/runner.py](local-client/codetest/runner.py) |
| TUI | [local-client/codetest/tui/renderer.py](local-client/codetest/tui/renderer.py) |

## 지원 범위

- **언어**: Java / JavaScript / TypeScript / Python / MyBatis XML / SQL(DML)
- **프레임워크**: Spring Boot / Spring MVC / Spring Security / Spring JPA / MyBatis
- **Graph Node**: `File` / `Class` / `Method` / `Variable` / `SQL`
- **Graph Edge**: `Contains` / `Calls` / `Uses` / `Executes`
