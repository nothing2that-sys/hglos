# 교차 회사 실행기 검증자 (cross-host runner)

공통 인계 양식은 원본 `../../../context-handoff.md`, 독립 설치본 `../context-handoff.md`다. 검증자는 자기 환경에서 정본·revision을 다시 읽고, 이전 대화와 구현자의 발견·결론을 상속하지 않는다. 양식이나 receipt는 승인 증거가 아니다.

메인 세션(Codex 또는 Claude Code)이 명령 하나로 다른 회사 에이전트 CLI를 **읽기 전용 보조 실행기**로 부르는 경로다. 실행기는 원본 `../../../scripts/cross_host.py`, hglos-verify 독립 설치본에서는 `../scripts/cross_host.py`다. 다른 설치본에서는 회차 변환 도구가 없으므로 hglos-verify 설치본의 실행기를 쓴다. 결과는 `validation/<run-id>/`의 파일로 받는다. 사람이 결과를 복사해 옮기지 않는다.

## 이 경로가 하는 일과 하지 않는 일

| 경로 | 문서 | 용도 |
|---|---|---|
| 실행기 | 이 문서 | 모드 A 적대 검토(findings JSON)와 긴 맥락 읽기·확인(Markdown). `codex exec`, `claude -p`, `agy -p` |
| 기준 경로 | `validator.md` | 모드 A 교차 검증 1회와 실행기 실패 시 우회. `cross_host.py`를 부르지 않는다. 이 slice에서 고치지 않는다 |
| 모드 B | `native-validator.md` 또는 사람이 시작한 다른 회사 새 세션 | slice 종결 검증. 실행기는 모드 B를 하지 않는다(보고서가 findings 스키마에 담기지 않는다) |

실행기 경로의 `codex exec`는 기준 경로와 두 가지가 다르다. `--ephemeral`을 쓰지 않는다(모델 확인용 rollout이 필요하다). `-m <모델 ID>`를 항상 명시한다. 기억 기능은 `--disable memories -c memories.use_memories=false -c memories.generate_memories=false`로 끈다.

## 선행 조건 (실행기가 호출 전에 확인하고 거절한다)

- **전송 선언** `<repo>/.hglos/cross-host-egress.json`: 사람이 한 번 선언하고 Git으로 추적한다. 필드는 `schema_version: 1`, `vendors`(anthropic/openai/google), `repo`(저장소 폴더 이름), `exclude_globs`(비어 있지 않은 문자열의 **목록**), `account_data`, `declared_by`, `date`, `quote`(사람 발화 인용)다. 파일이 없거나, 벤더가 빠졌거나, 인용이 비었거나, `exclude_globs`가 목록이 아니면 `egress_refused`다. `--scope`·`--canon`은 저장소 기준 상대 경로만 받고 `..`·절대 경로는 호출 전에 거절한다. 필요한 벤더는 codex→openai, claude→anthropic, agy→google이고, agy가 Claude 모델을 부르면 google과 anthropic 둘 다다.
- **입력 상한**: Codex·Claude `request_<N>.md`는 UTF-8 400,000바이트 이하, agy는 `-p` 8,000자 이하이고 패킷 합계 2,000,000바이트 이하다. 넘으면 CLI를 부르지 않고 `input_too_large`(종료 코드 2)다. 범위를 줄여 사람이 다시 지시한다.
- **사용량**: 호출 전에 토큰을 쓰지 않는 수단으로 잰다. Codex는 `app-server`의 `account/rateLimits/read`, agy는 `-p /usage`(셸 없이 호출)다. Claude는 측정 불가이므로 `unknown`이다. 관련 창 중 하나라도 잔여가 10% 미만이면 `limit`으로 거절하고, `usage-after_<N>.json`에 그 도구를 `exhausted: true`로 남긴다.
- **사용하지 않는 모델**: agy GPT-OSS는 어떤 작업에도 쓰지 않는다. agy Gemini는 검토에 쓰지 않는다(긴 맥락 확인 전용).
- **Codex 메인 세션**: 샌드박스 안에서는 다른 CLI가 인증·DNS·홈 폴더 쓰기에 실패한다. 그래서 실행기나 외부 CLI를 부를 때마다 **처음부터** 샌드박스 밖 실행 승인을 요청하고(`sandbox_permissions: require_escalated`와 이유), 명령에 시간 제한을 둔다(실행기 검토는 900초 + 여유, 예: 1,000,000ms). 승인을 요청하지 않고 샌드박스 안에서 부르면 실패하지 않고 응답 없이 멈춘다. 사람은 승인만 누르며, 결과는 파일로 받으므로 사람 중계가 아니다. 대화형 Codex 세션(`approval_policy on-request`)에서 승인 요청과 승인 뒤 exit 0을 2026-11-02에 확인했다(doc/33 U23, rollout 근거는 저장소 밖).
- **셸에서 `claude -p`를 직접 부를 때**: `claude -p`는 표준 입력이 열려 있으면 EOF까지 기다린다. PowerShell에서는 `$null | claude -p ...`처럼 표준 입력을 닫는다. 실행기는 지시문을 stdin으로 넘긴 뒤 닫으므로 따로 할 일이 없다.

## 절차

1. **사용량과 경로**: `usage`로 잰 뒤 `route`로 도구를 고른다. 같은 입력이면 같은 결과가 나온다. 건너뛴 경로와 이유가 출력에 남는다. `route`에 앞 회차의 `usage-after_<N>.json`을 주면 `exhausted: true`인 도구는 임계 미달로 보고 건너뛴다.

   ```text
   python <실행기> usage --tools codex,agy,claude [--claude-usage-note "<사람이 본 값>"] > <run 밖 임시>/usage.json
   python <실행기> route --usage <usage.json> --implementer-model <구현자 모델 ID> --main codex|claude --kind review|context
   ```

   검토 경로는 구현자 패밀리로 정한다. 구현자 OpenAI·메인 Codex는 `claude-p` → `agy-claude` → `native-codex` → 전달문 순이다. 구현자 Claude·메인 Claude는 `codex-exec` → `native-claude` → 전달문이다. 구현자 Claude·메인 Codex는 `native-codex` → `codex-exec` → 전달문이다. 구현자 OpenAI·메인 Claude는 `native-claude` → `claude-p` → 전달문이다. `native-*`와 전달문은 실행기가 아니라 `native-validator.md`나 새 세션용 전달문으로 진행한다. `agy-claude`는 agy Claude 모델 신원이 측정되기 전(doc/33 U7)에는 쓰지 않는다.

2. **인계**: MAIN이 공통 인계 양식을 채운다. 저장소, revision/dirty, 정본 해시, 미추적을 포함한 정확한 범위, 읽기 전용, 미확인을 넣는다. 이전 회차 발견과 구현자 결론은 넣지 않는다.

3. **회차 호출**: 회차마다 새 호출이다. resume·fork·이어가기를 하지 않는다.

   ```text
   python <실행기> call --repo <repo> --run <run-id> --round <N> --tool codex|claude|agy --kind review|context \
       --instruction <지시문 파일> --scope <범위 경로>... --canon <정본 경로>... \
       --implementer-model <구현자 모델 ID> [--model <ID>] [--effort <Codex effort>] [--timeout <초>] \
       [--downgraded-from <이전 run>]
   ```

   기본 모델은 Codex `gpt-6.1-sol`(effort `medium`), Claude `claude-opus-5-5`이고 agy는 `--model`이 필수다. 바꾼 값이 그대로 요청 모델이 된다. `--scope`는 필수다(없으면 검토 자료가 빠지므로 호출 전에 거절한다).

   한 run은 한 도구와 한 검토 대상만 다룬다. 실행기는 HEAD·범위·정본 목록과 정본 파일 내용·제외 glob·범위 diff·범위 파일 내용으로 `target_sha256`을 만든다(이 run 자신의 산출 폴더는 범위에서 늘 뺀다). run 1회차 값이 기준이고, 이후 회차가 다르면 CLI를 부르지 않고 `integrity` 실패(정식 회차 아님)로 기록한 뒤 run을 멈춘다. 같은 회차 안에서도 지시문을 만든 뒤(사용량 조회 등) 호출 직전에 다시 계산해 달라졌으면 같은 처리를 한다. run id의 각 부분에는 실행기 예약 이름(`agy-packet-<N>`, `agy-schema-<N>.json`, `canary`)을 쓸 수 없다. 대상이 바뀌었으면 새 run으로 1회차부터 시작한다.

   실행기는 범위 diff, 미추적 범위 파일 목록, 정본 경로, 제외 glob을 지시문에 붙인다(제외 파일의 diff는 넣지 않는다). Codex·Claude는 지시문을 stdin으로 받는다. agy는 저장소 밖 패킷 폴더(`%TEMP%\hglos-cross-host\<run-id>\agy-packet-<N>\`)를 작업 폴더로 쓰며, 범위 diff와 범위 파일 사본이 그 폴더에 들어간다. 패킷 폴더가 저장소 안이 되면(TEMP가 저장소 안) 호출 전에 거절한다. symlink(경로 중간 포함)이거나 실제 대상이 저장소 밖·제외 glob인 파일(예: 제외 폴더를 가리키는 junction)은 복사하지 않고 `manifest.md`의 skipped에 적는다. 정본(`--canon`) 파일은 범위 밖이어도 같은 규칙으로 패킷의 `canon/<경로>`에 복사한다. 시간 제한은 검토 900초, 긴 맥락 600초이고(agy `--print-timeout`은 60초 짧게) 넘으면 프로세스 트리째 끝낸다.

4. **판정 읽기**: `call_<N>.json`의 `failure`, `model_check`, `formal`, `rank`, `header`를 본다. 정식 회차(`formal: true`)만 `round_<N>.json`이 생긴다. 이어서 기존 도구로 정지 판정을 한다.

   ```text
   python <스킬>/scripts/compare_rounds.py <run> --cap 3
   ```

5. **실패와 강등**: 실패는 6.4 분류로 기록된다(`auth`, `limit`, `timeout`, `sandbox_network`, `contract`, `egress_refused`, `input_too_large`, `identity_unconfirmed`). 여기에 실행기 고유 분류 `integrity`가 더해진다: 호출 전후 저장소 상태(`integrity_<N>.json`: HEAD, `git status` 항목과 파일 내용, Git index 항목)가 다르면 읽기 전용 위반 의심으로 그 회차 결과를 믿지 않는다. CLI를 찾지 못하거나 시작하지 못하면 `contract`로 `call_<N>.json`에 남긴다. 실행기는 재시도·자동 로그인·모델 fallback을 하지 않는다.
   - 분류 근거: 검증자 출력에는 검토한 소스 원문이 섞이므로, Codex는 stderr와 `error`·`turn.failed` 이벤트 메시지로만, agy는 stderr와 envelope의 `response` 밖 필드로만, Claude는 stderr와 마지막 `result` 이벤트로만 분류한다. 서버 용량 오류(`at capacity`)는 `limit`이다. agy가 자기 `--print-timeout`에 이르러 0이 아닌 코드로 끝나면 `timeout`이고, 실행기 시간 초과와 같이 남은 자손 프로세스를 정리한다. 제한 안에 정상 종료(0)했으면 정리하지 않는다. Windows에서는 CLI를 멈춘 상태로 시작해 Job Object에 넣은 뒤 재개하므로, Job을 쓸 수 있으면 모든 자손이 Job 안에서 생긴다(재개하지 못하면 프로세스를 끝내고 시작 실패로 기록한다). 정리할 때는 Job에 더해 부모 PID 계보로도 자손을 찾는다(부모가 먼저 끝나도 정리된다). 계보에는 이 run이 시작된 뒤, 자기 부모보다 뒤, 프로세스 스냅샷보다 앞에 생긴 프로세스만 넣고, 생성 시각을 확인한 핸들을 쥔 채 그 핸들로 끝낸다(PID 재사용으로 무관한 프로세스를 끝내지 않는다). Job을 전혀 쓸 수 없을 때 중간 부모가 먼저 끝난 손자는 찾지 못할 수 있다(잔존 위험). 시간은 프로세스 시작 전부터 잰다.
   - `contract` 뒤에는 같은 도구로 다음 회차를 이어갈 수 있다. 실패 회차는 상한 3에 넣지 않는다. 다만 같은 회차에 관측 증거가 있는 신원 문제가 함께 있으면 `identity_unconfirmed`가 우선한다(이유에 계약 실패도 함께 적는다).
   - `identity_unconfirmed`나 `integrity` 뒤에는 그 run이 멈춘다. `integrity`는 저장소 변경을 사람이 확인한 뒤 새 run으로 다시 한다.
   - 다른 도구로 강등하면 **새 run**을 1회차부터 시작하고 `--downgraded-from`에 이전 run을 적는다. 이전 run의 발견은 넘기지 않는다.
   - 실행기 자체가 실패하면 모드 A는 `validator.md` 기준 경로나 native 새 맥락으로 진행하고, 그 사실을 보고에 적는다.

## 신원과 순위

순위는 메인 호스트나 도구 이름이 아니라 **실제 관측 모델의 회사**로 정한다. 실행기는 모델 전체 ID를 항상 명시하고, CLI가 스스로 남긴 로컬 기록에서 관측 모델을 기계적으로 읽는다.

| 도구 | 관측 출처 | 이 호출의 기록인지 확인하는 방법 |
|---|---|---|
| Codex | `~/.codex/sessions/**/rollout-*-<thread_id>.jsonl`의 모든 `turn_context` `payload.model` | `--json`의 `thread.started.thread_id`와 파일 이름이 같다. 회차마다 thread_id가 달라야 한다(F1). 첫 assistant 앞의 마지막 user 메시지가 `request_<N>.md`와 바이트로 같고, 그 앞에는 developer 메시지와 `# AGENTS.md instructions`·`<environment_context>` user 메시지만 있어야 한다(F3) |
| Claude | stream-json 마지막 `assistant` 이벤트의 `message.model`(최종 응답 모델)과 `result.modelUsage`의 키(관측 모델 집합) | stream 자체가 이 호출의 출력이다 |
| agy | 패킷 폴더 `agy_<N>.cli.txt`(`--log-file`)의 `model_resolver.go … Resolving model <id>` 줄 전부 | 같은 로그의 `Initializing CLI store manager for workspace`가 이 호출의 패킷 폴더다 |

- 판정(`model_check`)은 `일치`, `불일치`, `로그 없음`, `최종 작성자 미확인`(Claude) 중 하나다.
- 순위는 `일치`일 때만 매긴다. 순위표에서 제외된 모델(예: Opus 구현자에 Sonnet, Fable 구현자에 Opus)이거나, run 1회차와 모델이 다르거나, F1·F3이 실패하면 정식 회차가 아니다(`identity_unconfirmed`).
- 순위표는 doc/35 0.3(doc/33 6.3 갱신, doc/02 J5)이고, 동결 시험 `tests/test_verifier_rank.py`가 그 계약을 고정한다. OpenAI 구현자에게 Anthropic의 Fable·Opus·Sonnet은 1순위이고 Haiku는 제외다.
- 같은 회사 다른 모델의 2순위는 사람이 확인해 실행기(`SIMILAR_GRADE`)에 적은 조합만 받는다. 구현자와 비슷하거나 더 큰 등급만 적고, 구현자보다 한 단계 이상 작은 모델은 적지 않는다. 지금 목록은 Opus 5.5 구현자에 Fable 5.2 하나다. 이름·버전으로 등급을 추정하지 않는다. 적혀 있지 않은 Anthropic 모델은 제외다(예: Opus 구현자에 Sonnet, Fable 구현자에 Opus). 적혀 있지 않은 OpenAI 모델은 제외가 아니라 `순위: 미확인(등급 미확인)`(`rank_status: unknown_grade`)이고, 정식 회차 여부는 doc/33 6.3 (e)를 따른다.
- 긴 맥락 확인은 검증 회차가 아니므로 순위를 매기지 않는다.
- 보고 머리줄은 `call_<N>.json`의 `header`를 그대로 옮긴다. 형식은 SKILL.md의 머리줄이고 `구현자와 같은 패밀리` 칸을 포함한다(판정이 `일치`가 아니면 `미확인`). 관측 출처는 `CLI 로컬 기록`이다. 서버가 실제로 어떤 모델로 답했는지는 알 수 없다.

## 기록 파일 (회차 번호 `<N>`, 덮어쓰지 않는다)

`request_<N>.md`, `call_<N>.json`, `usage-before_<N>.json`, `usage-after_<N>.json`, `<tool>_<N>.raw.json`(검토) 또는 `<tool>_<N>.answer.md`(긴 맥락), `<tool>_<N>.stdout.txt`, `<tool>_<N>.stderr.txt`, `claude_<N>.stream.jsonl`, `agy_<N>.envelope.json`, `integrity_<N>.json`, 정식 회차만 `round_<N>.json`.

- 계정·설치 ID 키는 저장 전에 지운다. rollout 주입 본문과 agy 로그 줄은 저장하지 않는다.
- `thread_id`·`conversation_id`는 개인 실행 기록이며 공개 배포물에 넣지 않는다.
- 저장소 밖에는 `%TEMP%\hglos-cross-host\<run-id>\`에만 쓴다. 신원 근거 파일이므로 run이 끝나도 남긴다. 지울 때는 사람이 `python <실행기> clean --run <run-id>`로 그 run이 만든 항목(`agy-packet-<N>`, `agy-schema-<N>.json`, `canary`)만 지운다. 그 아래 중첩된 다른 run 폴더는 남기고 출력의 `kept`에 적는다.

## 금지

- Codex: `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, `--approve-for-me`, `--add-dir`, `-s workspace-write`, `-s danger-full-access`, `--search`, `--ephemeral`(실행기 경로), `--ignore-user-config`, `resume`, `fork`, `--last`, `codex apply`.
- Claude: `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`, `--permission-mode bypassPermissions|acceptEdits|auto`. 실행기는 `--permission-mode plan --restricted --tools Read,Grep,Glob --strict-mcp-config --no-session-persistence`를 쓴다.
- agy: `--dangerously-skip-permissions`, `--mode accept-edits|auto`, 저장소를 가리키는 `--add-dir`. 실행기는 `--mode plan --sandbox`와 저장소 밖 패킷 폴더를 쓴다.
- API 키 과금 경로. 실행기는 자식 환경에서 `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`를 뺀다.
- 보조 실행기에 쓰기나 구현을 맡기는 것. 검증자의 발견에 MAIN의 분석을 덧붙이는 것.

## 알려진 한계

- **agy 읽기**: 사용자 계정이 읽을 수 있는 파일을 패킷 폴더 밖에서도 읽는다. 사용자 MCP 서버와 웹에도 접근한다. 패킷 폴더는 보내는 범위를 줄이는 장치이지 경계가 아니다.
- **agy 쓰기**: `--mode plan`만으로는 쓰기가 막히지 않았다. 그래서 저장소 전후 상태를 `integrity_<N>.json`에 기록하고, 변경이 탐지되면 `integrity` 실패로 run을 멈춘다.
- **Codex 검증자**: read-only에서도 앞 회차 rollout을 읽을 수 있다. 지시문으로만 막고 강제하지 않는다. 사용자 설정의 MCP 접속 시도도 남는다.
- **Codex·Claude 제외 경로**: 둘은 저장소 cwd에서 돌아 저장소 안 파일을 모두 읽을 수 있다. 제외 glob은 지시로만 전달된다.
- **로컬 기록**: Codex rollout과 agy 기록에 지시문이 남는다. 실행기는 지우지 않는다.
- **Claude 사용량**: 구독 잔여량은 비대화형으로 잴 수 없다. 한도 오류(`error_max_budget_usd` 포함)가 나면 `limit`으로 기록하고 강등한다.
