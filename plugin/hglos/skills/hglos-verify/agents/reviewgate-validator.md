# ReviewGate 병행 경로 (reviewgate bridge)

공통 인계 양식은 원본 `../../../context-handoff.md`, 독립 설치본 `../context-handoff.md`다. 검증자는 자기 환경에서 정본·revision을 다시 읽고, 이전 대화와 구현자의 발견·결론을 상속하지 않는다.

메인 세션(Codex 또는 Claude Code)이 명령 하나로 외부 프로그램 **ReviewGate CLI**를 불러 다른 회사 CLI·API 모델의 검수(`review`)를 받거나, 별도 브랜치에 구현을 맡기는(`implement`) 경로다. 브리지는 원본 `../../../scripts/reviewgate_bridge.py`, 독립 설치본 `../scripts/reviewgate_bridge.py`다. 회차 변환(`round_<N>.json`)은 hglos-verify 번들에서만 된다. 다른 번들에서 부르면 정식 변환 없이 `round_tools_missing`으로 기록된다.

기존 교차 회사 실행기(`cross-host-validator.md`)와 기준 경로(`validator.md`)를 대체하지 않는 **병행 경로**다. HGLOS는 API 키를 읽거나 HTTP를 직접 부르거나 비용을 계산하지 않는다. 모델 호출·키·비용 상한·허용 규칙은 ReviewGate와 사람이 관리하고, 브리지는 stdout JSON과 그 JSON이 절대 경로로 가리키는 파일만 읽는다(02 J4).

## 선행 조건

- **실행 파일**: 환경 변수 `REVIEWGATE_CLI`(절대 경로)만 본다. PATH와 기본 경로는 보지 않는다. 없거나 파일이 없으면 `reviewgate_unavailable`(사유 `not_installed`, 종료 4)이고, 기존 경로(`cross_host.py`, `validator.md`, native, MAIN 직접)로 내려간다. 같은 회차에서 다시 부르지 않는다.
- **허용 규칙**: 검수 규칙·구현 규칙의 발급과 확대는 사람만 한다. 브리지는 `status`(읽기)만 한다. 사람이 규칙 폐기를 요청하면 MAIN이 ReviewGate 폐기 명령을 직접 실행한다.
- **전송 선언** `<repo>/.hglos/cross-host-egress.json`: `cross-host-validator.md`와 같은 선언이다. 구독 CLI(`codex-cli`→openai, `claude-cli`→anthropic)는 벤더가 `vendors`에, API(`openai`→openai, `together`→together)는 `vendors`와 **`api_vendors`** 둘 다에 있어야 한다. 검수자와 작업자를 모두 확인한다. `--input`·`--reference`(폴더면 추적 파일 전체)가 `exclude_globs`에 걸리면 거절한다. CLI 검수자·작업자는 폴더 전체를 읽을 수 있어 제외 glob이 강제되지 않는다.
- **보류 제공자**: `agy-cli`, `google`은 J3에 따라 늘 `provider_on_hold`로 거절한다.
- **Codex 메인 세션**: ReviewGate는 다른 CLI를 부르므로 `cross_host.py`처럼 처음부터 샌드박스 밖 실행 승인을 요청하고 시간 제한을 둔다.

## 명령

```text
python <브리지> review --run-dir <repo>/validation/<run-id> --repo <대상 저장소> --instruction <지시문 파일> \
    --reviewer <제공자[=전체 모델 ID]> [--reviewer ...] --input <상대 경로>... [--reference <상대 경로>...] \
    --profile code|document --implementer-model <구현자 모델 ID> [--diff-base <rev>] [--timeout <초>]
python <브리지> implement --run-dir <...> --repo <저장소> --instruction <지시문> --worker <제공자[=모델]> \
    --reviewer <제공자[=모델]>... [--input ...] [--reference ...] [--check "<규칙에 있는 명령>"...] \
    [--base <rev> | --branch reviewgate/impl-...]
python <브리지> discard --run-dir <...> --repo <저장소> --branch reviewgate/impl-... --confirmed-by "<사람 발화 인용>"
python <브리지> status --run-dir <...>
```

- API 제공자는 `제공자=모델 ID`가 필수다(`model_required`). CLI 제공자는 제공자만 써도 되며(ReviewGate 대표 모델), 그 회차는 요청 모델 `default:<제공자>`로 기록되고 늘 참고 검수다.
- review는 `--input`이 1개 이상 필요하다(폴더 전체 검수 안 함). `--diff-base`를 주면 그 rev 대비 입력 diff와 미추적 입력 파일 이름을 지시문에 붙인다.
- implement는 검수자가 1개 이상 필요하고, 작업자와 같은 벤더의 검수자는 거절한다(`same_vendor`). API 작업자는 `--input`이 필요하다. 시간 상한은 ReviewGate 구현 규칙의 값을 따른다(브리지는 넘기지 않는다).
- 기록은 대상 저장소가 아니라 `--run-dir`에 남는다. 회차 번호 N은 그 폴더의 호출 순번이다. 기존 기록 파일을 덮어쓰지 않는다.
- 브리지는 재시도·모델 fallback(`--no-fallback`)·결과 브랜치 병합·부분 내보내기를 하지 않는다.

## 결과 읽기

브리지 stdout은 JSON 한 개(`classification`, `exit`, `round`)다. 기록은 `call_<N>.json`, `request_<N>.md`, `reviewgate_<N>.stdout.json`(원문), `reviewgate_<N>.stderr.txt`, export를 불렀으면 `export_<N>.*`, 정식 회차면 `reviewgate_<N>.raw.json`과 `round_<N>.json`이다.

| 분류 | 종료 | MAIN이 할 일 |
|---|---:|---|
| `ok` | 0 | review는 아래 신원·정식 판정을 본다. implement는 결과 브랜치를 기록하고 멈춘다 |
| `approval_required` | 3 | `call_<N>.json`의 `violations` 원문을 그대로 사람에게 보이고 멈춘다. 규칙을 넓힐지는 사람이 정한다 |
| `checkout_changed` | 3 | 호출한 쪽 작업 폴더가 바뀌었다. 멈추고 사람에게 알린다 |
| `reviewgate_unavailable` | 4 | 기존 경로로 강등하고 사유(`not_installed`, `cli_not_found`, `credential_missing`)를 기록한다 |
| `review_partial` | 1 | 일부 단위만 끝난 review. 신원은 기록되지만 지적은 참고 검수다 |
| `partial`, `checks_failed` | 1 | implement 결과 브랜치는 있으나 성공이 아니다. 사람에게 보인다 |
| `timeout`, `failed`, `reviewgate_busy`, `needs_reconciliation` | 1 | 실패 회차. 남은 worktree·비용 예약은 브리지가 정리하지 않으므로 사람에게 알린다 |
| `input_too_large` | 2 | 범위를 줄여 새 회차로 다시 지시한다 |
| `contract`, 호출 전 거절 | 2 | 원문을 기록한다. 거절(`egress_refused` 등)은 run 폴더에 아무것도 쓰지 않는다 |

## 신원과 정식 회차

- **정식 회차 후보**는 검수자 1개를 `제공자=전체 모델 ID`로 지정한 `--profile code` review 하나뿐이다. 그 밖(검수자 2개, 제공자만, document, implement의 교차 검수)은 늘 참고 검수다.
- ReviewGate가 attempt마다 돌려주는 `model_identity`를 doc/33 6.3 판정(`일치`/`불일치`/`로그 없음`/`최종 작성자 미확인`)으로 attempt마다 보고, 모든 attempt가 `일치`여야 회차가 `일치`다. 제공자만 넘긴 attempt는 `로그 없음(요청 모델 default)`이다.
- Codex 검수자는 브리지가 attempt마다 `model_identity.source.path`의 rollout을 읽기 전용으로 열어 새 맥락(F3)을 확인한다. rollout 본문은 기록하지 않는다.
- 정식 회차는 후보이면서 분류 `ok`, 회차 `일치`, 순위표 제외 아님, 같은 run 앞 review가 모두 정식이고 관측 모델이 같음, attempt `session_id`가 모두 새것, F3 통과(Codex), fallback·resume 없음, 요청 해시 일치, 호출 전후 대상 저장소 상태 같음, export 완전(`hglos.findings.live.json`의 절대 경로·SHA-256·크기 일치), `validate_findings`·`make_round` 계약 통과일 때만이다. `call_<N>.json`의 `formal_round`와 `formal_reasons`에 결과가 남는다.
- export가 미완료(exit 13·14·20)면 분류는 `ok`로 두고 `export_incomplete:<error_code>`로 기록한다. 부분 내보내기는 쓰지 않는다.
- 보고 머리줄은 `call_<N>.json`의 `header`를 그대로 옮긴다. 정식이 아니면 끝에 `정식 회차 아님`이 붙고 회차 상한·수렴 비교(`compare_rounds.py`)·순위 집계에서 뺀다.
- 참고 검수의 지적은 `evidence`가 "인용·위치 확인"이어도 사실로 단정하지 않는다. MAIN이 원문을 직접 확인한 뒤에만 발견으로 채택한다.

## 결과 브랜치 (implement)

- 받아들이기(병합)는 그 브랜치 diff를 대상으로 hglos-verify 독립 검증을 따로 하고, 사람이 명시적으로 승인한 뒤에만 사람 또는 MAIN이 한다. 브리지는 병합하지 않는다.
- 버리기는 사람 확인 발화를 `--confirmed-by`로 인용해 `discard`한다.
- 다시 시키기(`--branch`)는 계획 범위 안에서 MAIN이 정한다. 같은 결과 브랜치에 재지시는 2회까지다.

## 하지 않는 것

- ReviewGate 참고 검수만으로 자기 구현을 정식 판정하지 않는다. slice 종결 검증(모드 B)은 `native-validator.md`나 사람이 시작한 다른 회사 새 세션으로 한다.
- ReviewGate 상태 폴더나 worktree 폴더를 직접 읽거나 쓰지 않는다.
- 브리지 자체를 ReviewGate로 구현하지 않는다(자기 경로로 자기를 만드는 순환).
