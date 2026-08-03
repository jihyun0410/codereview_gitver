# Local Client (사용자 로컬 환경)

사용자가 git 으로 내려받아 개인 로컬 개발 환경에 설치하는 영역입니다.
`git 조회 → 서버 REST 호출 → 테스트 실행 → TUI 출력` 만 담당하고,
Test Code 생성과 결과 판정은 Agent Server 가 수행합니다.

## 설치

```bash
cd local-client
pip install -e .

export CODETEST_SERVER_URL=http://<agent-server>:8000
export CODETEST_API_KEY=<API_KEY>
```

## 명령어 (5개)

| 명령 | 설명 |
|---|---|
| `codetest project register` | 명령어를 입력한 환경의 Project 를 등록하고 필요한 정보를 서버로 전달 |
| `codetest project delete` | 등록한 project 에 대한 정보를 삭제 |
| `codetest run` | staging 에 올라가지 않은 파일에 대해 Test Code 생성 → 실행 → report |
| `codetest run --stage` | staging 에 올라간 파일에 대해 Test Code 생성 → 실행 → report |
| `codetest generate` | Git Working Tree 기반 변경 파일에 대하여 Test Code 만 생성 |
| `codetest test` | `src/test/test.txt` 의 Test Code 를 가져와 실행 → report |

```bash
codetest project register          # 최초 1회
codetest run                       # 작업 중인(미스테이징) 변경 테스트
git add .
codetest run --stage               # 스테이징된 변경 테스트
codetest generate                  # 생성만
codetest test                      # 저장된 test.txt 재실행
```

종료 코드: 정상 `0`, 오류 `1`, **테스트 FAIL `2`** — CI 게이트로 쓸 수 있습니다.

## TUI 결과 화면

```
┌──────────────────────────────────────┐
│ 결과                                 │
│ 기능 중요도             LOW          │
│ TEST CODE               보기         │
│ TEST RESULT             PASS         │
│ TEST RESULT 상세 보기   보기         │
└──────────────────────────────────────┘
[c] TEST CODE 보기  [r] TEST RESULT 상세 보기  [q] 종료
선택>
```

- `c` — 실제 테스트를 진행한 코드와 **Test Code 작성 근거**
- `r` — 결과 값과 **적절성 판단 결과 및 근거**
- 기능 중요도는 `HIGH` / `MID` / `LOW`

파이프·리다이렉트 등 비대화형 환경에서는 표만 출력하고 프롬프트를 띄우지 않습니다.

## 생성 산출물

| 경로 | 내용 |
|---|---|
| `src/test/test.txt` | 생성된 Test Code (`codetest test` 가 다시 읽는 파일) |
| `.codetest/last_test.json` | 실행 언어·명령·중요도·근거 등 메타 |
| `.codetest/config.json` | 이 저장소에 등록된 `project_id` |
| `~/.codetest/config.json` | 서버 주소 · API Key |
