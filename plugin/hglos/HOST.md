# 호스트별 실행 규칙

기본 스킬 실행/설치에 외부 SDK import·pip·키 등록·유료 호출을 추가하지 않는다.

공통 진입 [ENTRY.md](ENTRY.md)를 먼저 읽고 명시 작업의 스킬·참조를 확인한다. 목차/내용 식별 확인은 실제 모델 독해·승인 증거가 아니다.

설치본은 각 스킬 루트의 `HOST.md`와 `RULES.md`를 읽는다. 원본 작업 시 이 파일은
`plugin/hglos/HOST.md`에 있다. 스크립트는 설치본 `scripts/`를 사용한다.
Python 실행 파일은 먼저 확인한다. Windows의 `python`이 Store 별칭이면 실제 설치된 Python 경로를 쓴다.

## 공통 계약

- 사용자·프로젝트의 권한과 위임 규칙이 우선한다. 설치는 권한을 늘리지 않는다.
- 범위·계획·종결의 사람 승인을 유지한다. 이미 받은 명시적 승인은 재사용하며, 스킬 사용 자체를 새 승인으로 해석하지 않는다.
- Git 커밋은 사용자 지시에 포함된 경우만 한다. 커밋 금지 환경에서는 계획서와 승인 발화를 보존하되
  승인 시점 커밋을 지어내지 않는다.
- 커밋 금지 환경에서는 승인 시점에 `plan_hash.py`로 `Current State` 절 밖의 해시를 기록하고, 구현 시작 시 `--expect`로 대조한다.
- 넓은 작업 요청(예: "알아서 진행해")은 계획 승인으로 재사용하지 않는다. 승인은 계획서 승인란에 인용된 발화만 인정한다.
- 모델명·회사·버전·권한·샌드박스 상태를 추정하지 않는다. 호스트 메타데이터나 실제 도구 출력에서 확인한다.
  확인할 수 없으면 `미확인`으로 기록한다. 같은 도구라도 선택한 모델에 따라 회사가 다를 수 있다.

## Codex / Antigravity / Claude

**Claude Code·Antigravity·Codex는 다섯 스킬 모두의 필수 지원 호스트다.** 새 기능도 같은
`SKILL.md`·Python 표준 라이브러리 스크립트로 제공한다. 특정 호스트 도구를 필수 실행기로 고정하지 않는다.
사용하는 셸에서 확인한 Python 절대 경로와 실제 스킬 경로로 실행하며, 경로에 공백이 있으면 인용한다.

| 호스트 | 프로젝트 설치 | 명시 호출 | 프로젝트 상시 지침 |
|---|---|---|---|
| Claude Code | `.claude/skills/` | `/hglos-design` | `CLAUDE.md` |
| Antigravity App/IDE/CLI | `.agents/skills/` | `/hglos-design` | `GEMINI.md` 또는 지원되는 `AGENTS.md` |
| Codex | `.agents/skills/` | `$hglos-design` | `AGENTS.md` |

다른 네 스킬도 같은 접두사로 호출하거나 스킬 이름을 포함한 자연어로 요청한다.
자동 발견 여부는 각 호스트의 스킬 목록에서 확인한다. 설치 파일 존재와 실제 모델 수행은 별도 검증이다.
Claude plugin으로 설치했다면 `/hglos:hglos-design`처럼 plugin namespace가 붙을 수 있다.
설치 경로·호출 근거(2026-10-22): [Claude](https://code.claude.com/docs/en/skills),
[Antigravity](https://antigravity.google/docs/skills/), [Codex](https://learn.chatgpt.com/docs/build-skills).

위임 전에 현재 호스트에서 새 맥락의 에이전트, 읽기 전용 권한, 반환 방식이 지원되는지 확인한다.
지원되면 그 API를 쓴다. Claude의 Task/Bash 이름이나 특정 CLI를 다른 호스트에 있다고 가정하지 않는다.
MAIN만 구현할 수 있는 프로젝트라면 MAIN이 직접 구현하고 보조 에이전트는 읽기 전용 검토만 한다.
순차 구현도 가능한 작업을 서브에이전트 부재만으로 거절하지 않는다.

설계 검토와 종결 검증은 새 맥락이 필수다. 서브에이전트가 없으면 **새 세션용 전달문**을 파일로 작성한다.
전달문에는 대상 저장소, 계획서, 변경 범위, 읽기 전용 제약, 원문 출력 위치, 판정 조건을 넣는다.
현재 세션의 자체 검토를 독립 검증으로 표시하지 않는다. 실제 검증 보고가 오기 전에는 `미검증`이다.

## hglos-verify 실행 경로

1. 구현자와 다른 회사의 모델을 실제로 쓸 수 있으면 우선한다. 사용할 수 있는 모델/도구만 고른다.
   메인이 Codex 또는 Claude Code면 교차 회사 실행기 `scripts/cross_host.py`(`agents/cross-host-validator.md`)로
   `claude -p`·`codex exec`·`agy -p`를 읽기 전용으로 부르고 결과를 파일로 받는다(모드 A·긴 맥락 확인만).
   호출 전 사용량 확인·라우팅·실패 강등·신원 기록은 실행기가 하고, 순위는 CLI 로컬 기록으로 확인한 관측 모델로 정한다.
2. 네이티브 새 검증 에이전트가 있으면 `agents/native-validator.md`를 사용한다. 이전 대화·발견을 넘기지 않는다.
3. 외부 Codex CLI를 직접 부르는 기준 경로는 `agents/validator.md`다. 모드 A 교차 검증과 실행기 실패 시 우회에 쓰며
   실행기를 부르지 않는다. Codex 구현자를 Codex CLI로 검증한다고 다른 회사가 되지는 않는다.
   불필요한 Codex→Codex 중첩 호출보다 네이티브 새 맥락을 우선한다.
4. 모두 없으면 새 세션용 전달문을 만든다. 명령/인증/권한 오류를 숨기거나 샌드박스를 해제하지 않는다.

교차 회사 실행기는 Python 표준 라이브러리만 쓰고 사용자 구독 CLI를 그대로 호출한다. API 키 과금 경로를 쓰지 않으며
자식 환경에서 API 키 변수를 뺀다. 전송 전 사람이 `.hglos/cross-host-egress.json`에 벤더별 전송을 한 번 선언해야 한다.
Codex 메인 세션에서는 샌드박스 안의 다른 CLI가 인증·DNS·홈 폴더 쓰기에 실패하므로 실행기나 외부 CLI를 부를 때마다
**처음부터** 샌드박스 밖 실행 승인을 요청하고(`require_escalated`) 명령 시간 제한을 둔다. 요청하지 않으면 실패하지 않고
멈춘다. 사람은 승인만 하며 결과를 중계하지 않는다. 셸에서 `claude -p`를 직접 부를 때는 표준 입력을 닫는다(`$null |`).
권한·샌드박스 우회 플래그는 쓰지 않는다.

검증자는 소스를 읽기만 한다. 테스트가 build artifact를 쓰면 MAIN이 실행하고 독립 검증자는 로그를 확인한다.
재실행과 로그 확인을 구분한다. 결과 기록 파일은 오케스트레이터만 저장해도 된다.
`guard_integrity.py`는 사후 변경 탐지이며 권한 강제가 아니다. 기존 가드는 untracked/ignored 파일 내용까지
보장하지 않으며 staged 내용도 완전히 대조하지 않으므로 검증 범위 파일 전체의 별도 전후 해시를 남긴다.
회차 JSON은 `scripts/validate_findings.py`로 계약을 검증한 뒤 변환한다. 실패 결과는 성공 회차로 비교하지 않는다.

## ReviewGate 병행 경로 (선택)

외부 프로그램 ReviewGate CLI가 설치되어 있으면 `scripts/reviewgate_bridge.py`로 다른 회사 CLI·API 모델의 검수(`review`)와
별도 브랜치 구현 위임(`implement`)을 받을 수 있다. 위 실행 경로를 대체하지 않는 병행 경로이며 선택 기능이다.
실행 파일은 환경 변수 `REVIEWGATE_CLI`(절대 경로)만 보고, 없으면 "ReviewGate 없음"으로 기록한 뒤 위 경로로 내려간다.
HGLOS는 API 키 읽기·HTTP 직접 호출·비용 계산을 하지 않는다. 키·비용 상한·허용 규칙은 ReviewGate와 사람이 관리한다.
허용 규칙 발급·확대와 결과 브랜치 병합은 사람만 한다. 정식 검증 회차가 되는 조건과 결과 읽기는
hglos-verify `agents/reviewgate-validator.md`를 따른다. Codex 메인 세션에서는 `cross_host.py`처럼 처음부터 샌드박스 밖
실행 승인을 요청하고 시간 제한을 둔다.

## 선택적 세션 조정

`hglos-coordinate`의 `setup_coordination.py`는 로컬 프로젝트의 Claude/Codex hooks 설치를
미리 보여주고 `--apply`에서만 설정·지침 블록을 추가한다. 단순 스킬 설치는 이 작업을 하지 않는다.
`--host antigravity`는 공통 규약·registry와 `GEMINI.md` 참여 지침만 준비한다. Antigravity hook는 만들지 않는다.
`--host all`은 세 호스트의 참여 지침과 지원되는 Claude/Codex hooks를 함께 준비한다. `both`는 기존 Claude/Codex 의미를 유지한다.
실제 hook 신뢰·지원 버전·도구 범위·오류 처리를 각 호스트에서 검증한다. 자동 보호의 근거는
파일 존재가 아니라 실제 hook 동작이다. Antigravity 등은 같은 CLI로 수동 참여하고
공식 hook 지원이나 자동 세션 재개가 있다고 가정하지 않는다. 상세 규약은 `COORDINATION.md`다.

## Gemini Gem

Gem은 Gemini 웹 앱의 별도 기능이다. Antigravity 스킬을 Gem에 설치했다고 표현하지 않는다.
`integrations/gemini/hglos-gem-instructions.md`를 지침에 붙이고 필요한 문서를 Knowledge에 첨부한다.
Gem은 제공된 자료로 설계 인터뷰·계획/보고서/대시보드 초안을 작성한다. 로컬 저장소 접근, CLI 실행,
독립 검증을 실제로 하지 않았으면 했다고 쓰지 않는다. 구현·실행 검증은 로컬 호스트로 넘긴다.
