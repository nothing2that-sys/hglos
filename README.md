# HGLOS — Human-Gated LLM Orchestration Skills

**설계 → 구현 → 검증**을 사람 승인 게이트로 묶는 Claude 스킬 3개입니다. 별도 앱이나 데몬 없이, 계획서 파일 하나와 보고서 파일로 세 단계가 문맥을 주고받습니다.

> Three Claude skills (design / implement / verify) connected by a single plan file and human approval gates. Works as a Claude Code plugin or as uploadable skills. Docs are in Korean.

현재 버전: **1.0.0** · 대상: **Claude Code**(터미널·데스크탑·VS Code), Claude 앱 스킬 업로드 · 라이선스: **MIT**

## 처음이라면: 가장 쉬운 사용법 (Beginner Quick Start)

**한국어**

코딩·터미널이 낯설어도 됩니다. 사람이 할 일은 "설치 1번, 말 3번, 승인 몇 번"입니다.

1. **준비** — [Claude Code](https://claude.com/claude-code), `git`, `Python 3.10+` 를 설치합니다. 작업할 폴더가 `git` 저장소가 아니면 그 폴더의 터미널에서 `git init` 을 한 번 실행합니다.
2. **설치 (한 번만)** — 그 폴더에서 `claude` 를 실행하고 아래 두 줄을 차례로 입력합니다.
   ```text
   /plugin marketplace add nothing2that-sys/hglos
   /plugin install hglos@hglos
   ```
3. **말로 시키기** — 아래 세 문장을 **각각 새 대화(새 세션)** 에서 말합니다. 새 세션으로 나누는 이유는 만든 쪽이 스스로 검사하지 않게 하기 위해서입니다.

   | 순서 | 이렇게 말하세요 | 그러면 |
   |---|---|---|
   | ① 계획 | `hglos-design 으로 계획 세워줘. 하고 싶은 일: (여기에 평소 말투로 적기)` | Claude 가 질문(인터뷰)을 합니다. 답하고, **"범위"와 "계획"이 맞으면 승인**한다고 말합니다. `doc/` 에 계획서 파일이 생깁니다. |
   | ② 구현 | `hglos-implement 로 doc/ 의 계획서를 구현해줘. 커밋하지 마.` | 계획대로 만들고 보고서 파일을 남깁니다. |
   | ③ 검증 | `hglos-verify 로 slice 종결 검증해줘. 커밋하지 마.` | 새로운 눈으로 다시 확인하고 `통과/미달/조건부/측정 불가/미검증` 중 하나로 판정합니다. |

4. **마무리** — ③ 의 판정을 읽고, 통과면 변경을 직접 커밋합니다. 미달이면 어디가 모자란지 적혀 있으니 ②로 돌아갑니다.

팁: 모르는 단어가 나오면 그냥 "쉽게 설명해줘"라고 하세요. 어디서든 `이 작업들 병렬로 나눠도 돼?` 처럼 물어볼 수 있습니다. 커밋은 항상 사람이 합니다.

**English**

You don't need to be a developer. Your part is: install once, say three sentences, approve a few times.

1. **Prepare** — Install [Claude Code](https://claude.com/claude-code), `git`, and `Python 3.10+`. If your project folder is not a git repository yet, run `git init` once in it.
2. **Install (once)** — Run `claude` in that folder and type:
   ```text
   /plugin marketplace add nothing2that-sys/hglos
   /plugin install hglos@hglos
   ```
3. **Just ask** — Say each sentence below in a **separate new chat (new session)**. Separate sessions are the point: whoever builds something must not be the one who judges it.

   | Step | Say this | What happens |
   |---|---|---|
   | 1. Plan | `Use hglos-design to make a plan. What I want: (describe it in plain words)` | Claude interviews you. Answer, then say you **approve the scope and the plan**. A plan file appears under `doc/`. |
   | 2. Build | `Use hglos-implement to implement the plan in doc/. Do not commit.` | It builds per the plan and leaves a report file. |
   | 3. Check | `Use hglos-verify to verify the slice is done. Do not commit.` | A fresh reviewer re-checks everything and returns one verdict: pass / fail / conditional / unmeasurable / unverified (the skill's output is in Korean). |

4. **Finish** — Read the verdict. If it passes, commit the changes yourself. If not, it says what is missing; go back to step 2.

Tips: if you see an unfamiliar word, just ask "explain simply". Commits are always done by a human.

## 무엇을 하나

| 스킬 | 하는 일 | 사람 게이트 |
|---|---|---|
| `hglos-design` | 인터뷰로 계획서(`Slice_Plan`) 1개를 만든다 — 닫는 운영 주장, 종결 조건(임계값을 측정 전에 기재), 하지 않을 것, 검증 가능 전제, 작업 분할과 병렬 가능 여부. 코드를 쓰지 않는다 | 범위 승인 · 계획 승인 |
| `hglos-implement` | 승인된 계획서로 작업 지시서를 만들고, 이 세션이 오케스트레이터가 되어 작업별 서브에이전트(또는 Codex)에 맡기고, 보고서를 파일로 받는다. 병렬 가능한 작업은 단계별로 묶어 동시에 돌린다 | 미달일 때만 멈춤 |
| `hglos-verify` | 새 맥락의 독립 검증자로 판정한다. (A) git 변경분 적대적 리뷰를 회차로 돌려 수렴까지, (B) slice 종결 시 계획서의 종결 조건을 직접 재현해 판정 보고서 작성. 읽기 전용 | 종결 승인은 사람 |

핵심 원칙

- **승인 전 계획서는 효력이 없다.** 구현 레그는 승인란이 빈 계획서로 시작하지 않고, 승인 시점 커밋 이후 계획이 바뀌었으면 멈춘다.
- **만든 쪽이 스스로 판정하지 않는다.** 검증자 순위: 다른 회사 모델(기본 Codex CLI) → 같은 회사의 비슷한 등급 다른 모델 → 같은 모델. 모든 판정 첫 줄에 검증자를 적는다.
- **판정 어휘는 5개뿐**: 통과 / 미달 / 조건부 / 측정 불가 / 미검증. 증거 없는 통과는 실패다.
- **세는 것은 스크립트로.** 개수·집합·무결성 주장은 스크립트 출력만 근거로 쓴다. 검사 스크립트는 일부러 틀린 입력으로 실패하는지부터 확인한다.

## 요구사항

- **Claude Code** (플러그인 설치) 또는 스킬 업로드가 되는 Claude 앱(Pro/Max/Team/Enterprise, 코드 실행 활성화)
- **git** — 계획 승인 커밋, 변경분 검증에 쓴다
- **Python 3.10 이상** — 판정 스크립트 실행용 (표준 라이브러리만 사용)
- 선택: **[Codex CLI](https://github.com/openai/codex)** — `hglos-verify` 의 1순위 검증자. 없으면 같은 회사의 다른 모델(2순위)로 내려가고 그 사실을 기록한다

`hglos-verify` 와 `hglos-implement` 의 오케스트레이션은 서브에이전트가 필요하므로 **Claude Code / 데스크탑** 에서 쓰는 것을 전제로 한다. 웹 채팅에서는 설계(`hglos-design`)만 실용적이다.

## 설치

### 1. Claude Code 플러그인 (권장)

Claude Code 세션 안에서:

```text
/plugin marketplace add nothing2that-sys/hglos
/plugin install hglos@hglos
```

또는 셸에서:

```bash
claude plugin marketplace add nothing2that-sys/hglos
claude plugin install hglos@hglos
```

설치 후 `/` 를 입력하면 `/hglos:hglos-design`, `/hglos:hglos-implement`, `/hglos:hglos-verify` 가 보인다. 슬래시 명령 없이 "계획 세워줘", "계획대로 구현", "slice 종결 검증" 처럼 말해도 스킬이 선택된다.

업데이트: `claude plugin update hglos@hglos` (또는 `/plugin` → Installed → hglos → Update now).
제거: `claude plugin uninstall hglos@hglos`.

데스크탑 앱 Code 탭에서는 프롬프트 옆 **+** → **Add plugins** 에서 마켓플레이스를 추가하고 설치할 수 있다.

### 2. Claude 앱에 스킬 업로드

1. [`dist/`](dist) 에서 `hglos-design.skill`, `hglos-implement.skill`, `hglos-verify.skill` 을 내려받는다 (각 파일은 zip 이다. 업로더가 확장자를 거부하면 `.zip` 으로 바꾼다)
2. Claude 앱 **Settings → Capabilities → Skills** 에서 **Upload skill** 로 하나씩 올린다
3. 같은 이름의 이전 버전이 있으면 먼저 지운다

번들마다 공용 판정 스크립트(`check_parallel.py`, `set_diff.py`, `stale_ids.py`)가 `scripts/` 에 들어 있다.

### 3. 수동 설치 (Claude Code 개인 스킬 폴더)

```bash
git clone https://github.com/nothing2that-sys/hglos.git
cp -r hglos/plugins/hglos/skills/* ~/.claude/skills/
```

Windows PowerShell:

```powershell
git clone https://github.com/nothing2that-sys/hglos.git
Copy-Item -Recurse -Force hglos\plugins\hglos\skills\* "$env:USERPROFILE\.claude\skills\"
```

## 사용법

### 한 slice 의 흐름

slice = 입력에서 관측 가능한 결과까지 끊기지 않는 작업 한 덩어리. 한 slice 를 아래 순서로 닫는다.

```
[설계 세션]  hglos-design
   범위 초안 ──(게이트 1: 범위 승인)──▶ 계획서 작성 ──(게이트 2: 계획 승인)
   사람: 계획서만 커밋 → 그 해시를 계획서 "Current State" 첫 줄에 적는 한 줄 커밋

[구현 세션]  hglos-implement   ※ 계획서를 쓴 세션과 다른 새 세션
   착수 조건 검사 → 지시서 → 작업별 서브에이전트 → 보고서 파일
   (계획서 규칙에 따른 정지점·미달일 때만 사람에게 온다)

[검증 세션]  hglos-verify (모드 B)   ※ 또 다른 새 세션
   종결 조건을 직접 재현 → verdict.md (첫 줄 검증자 헤더)
   사람: 종결 승인, 커밋 범위 결정, 커밋
```

### 예시 요청

설계:

```text
hglos-design 으로 새 slice 계획서를 세워줘.
[대상] 현재 저장소. [주제] 설정 파일 저장 시 빈 목록이 null 로 바뀌는 문제.
[하지 않을 것] 리팩터링, 문서 갱신.
[조건] 종결 조건 임계값은 측정 전에 박아라. 범위·계획 게이트에서 멈추고 승인 받아.
```

구현 (새 세션):

```text
hglos-implement 로 doc/Slice_Plan_<이름>.md 를 구현해.
착수 조건 검사부터 하고, 실패하면 멈춰. 오케스트레이터로 진행하고 작업은 서브에이전트에 맡겨.
보고서는 report-<작업>.md 로 저장. 커밋하지 마.
```

검증 (새 세션):

```text
hglos-verify 모드 B 로 doc/Slice_Plan_<이름>.md 의 slice 종결 판정을 해줘.
C1~Cn 을 구현자 보고서에 의존하지 말고 직접 재현해. 검증자는 우선순위대로. 커밋 금지.
```

변경분 적대적 리뷰만 필요할 때 (모드 A):

```text
이 커밋 전에 hglos-verify 로 변경분 적대적 리뷰 돌려줘. 고정점까지.
```

병렬 가능 여부만 물을 때:

```text
이 작업들 병렬로 나눠도 돼?
```

### 상시 규칙 (선택이지만 권장)

스킬은 불렸을 때만 읽힌다. 스킬이 안 불린 세션에서도 지킬 규칙은 [`plugins/hglos/RULES.md`](plugins/hglos/RULES.md) 맨 아래 **붙여넣기 블록**을 프로젝트의 `CLAUDE.md` 또는 `AGENTS.md` 에 복사해 둔다.

### 판정 스크립트 (단독 사용 가능)

| 스크립트 | 용도 | 종료 코드 |
|---|---|---|
| `check_parallel.py <tasks.json> [--repo <repo>]` | 작업별 쓰기 범위·공유 자원·선후 의존(`after`)으로 병렬 실행 단계를 판정 | 0 완료, 2 입력 오류 |
| `set_diff.py <기대 목록> <실제 목록>` | 이름 목록 양방향 대조 (개수가 아니라 이름) | 0 일치, 1 차이, 2 입력 오류 |
| `stale_ids.py --ledger <대장.md> <문서 폴더>` | 닫힌 항목 ID 가 "대기·남은·미완료 체크박스" 맥락에 남은 줄 찾기 (문면 노후화 검출) | 0 없음, 1 발견, 2 입력 오류 |

위치: 플러그인 설치 시 각 스킬 폴더의 `scripts/`, 저장소에서는 [`shared/scripts/`](shared/scripts).

## Usage (English)

**What it is.** Three Claude skills joined by one plan file (`Slice_Plan`) and human approval gates. A *slice* is one chunk of work that runs from input to an observable result.

| Skill | Role | Human gate |
|---|---|---|
| `hglos-design` | Interviews you and writes one plan: the claim to close, completion criteria (thresholds fixed *before* measuring), non-goals, checkable assumptions, task split and parallelism. Writes no code. | approve scope, approve plan |
| `hglos-implement` | Turns the approved plan into an instruction file, delegates tasks to subagents (or Codex), collects report files. Parallel-safe tasks run together. | stops only on shortfall |
| `hglos-verify` | Independent fresh-context verifier. Mode A: adversarial review of the git diff in rounds until it converges. Mode B: re-runs the plan's completion criteria and writes a verdict. Read-only. | human approves closing |

**Install.**

```text
/plugin marketplace add nothing2that-sys/hglos
/plugin install hglos@hglos
```

or from a shell: `claude plugin marketplace add nothing2that-sys/hglos` then `claude plugin install hglos@hglos`. Alternatively upload the `.skill` files from [`dist/`](dist) in the Claude app (Settings → Capabilities → Skills), or copy `plugins/hglos/skills/*` into `~/.claude/skills/`.

**Flow for one slice** (each leg in its own new session):

1. Design: `Use hglos-design to write a new slice plan. Goal: … Non-goals: … Stop at the scope and plan gates for my approval.` Then commit the plan and put that commit hash on the first line of the plan's "Current State".
2. Implement: `Use hglos-implement to implement doc/Slice_Plan_<name>.md. Run the start-condition check first and stop if it fails. Do not commit.`
3. Verify: `Use hglos-verify in mode B to judge closing of doc/Slice_Plan_<name>.md. Reproduce every criterion yourself; do not trust the implementer's report. Do not commit.`
4. Diff review only (mode A): `Before this commit, run an adversarial review of my changes with hglos-verify, until fixed point.`
5. Parallelism check: `Can these tasks run in parallel?`

**Key rules.** An unapproved plan has no effect. The builder never judges its own work (verifier order: different-vendor model, default Codex CLI → another model from the same vendor → same model). Verdicts are only pass / fail / conditional / unmeasurable / unverified; no evidence means fail. Counts and set comparisons come from scripts, not from the model.

**Requirements.** Claude Code (or a Claude app that supports skill upload), `git`, Python 3.10+ (standard library only). Optional: [Codex CLI](https://github.com/openai/codex) as the first-choice verifier. Subagent-based legs (`hglos-implement`, `hglos-verify`) need Claude Code / desktop; in web chat only `hglos-design` is practical.

**Optional always-on rules.** Copy the paste block at the bottom of [`plugins/hglos/RULES.md`](plugins/hglos/RULES.md) into your project's `CLAUDE.md` or `AGENTS.md`.

## 저장소 구조

```
.claude-plugin/marketplace.json   # 마켓플레이스 정의 (이 저장소 = 마켓플레이스)
plugins/hglos/                    # 플러그인 본체
  .claude-plugin/plugin.json
  RULES.md                        # 상시 규칙 + 붙여넣기 블록
  skills/hglos-design/            # SKILL.md, 계획서 템플릿
  skills/hglos-implement/         # SKILL.md, 지시서·보고서 템플릿
  skills/hglos-verify/            # SKILL.md, 검증자 에이전트 정의, 회차 판정 스크립트
shared/scripts/                   # 공용 판정 스크립트 정본 (각 스킬 scripts/ 로 복사됨)
dist/*.skill                      # 스킬 업로드용 번들
tools/build.py                    # 공용 스크립트 동기화 + 번들 생성
tests/                            # 판정 스크립트 테스트
```

## 개발

```bash
python -m unittest discover -s tests   # 테스트
python tools/build.py                  # shared/scripts → 각 스킬 동기화, dist/*.skill 재생성 (결정적)
python tools/build.py --check          # 동기화 상태만 검사
claude plugin validate .               # 마켓플레이스·플러그인 형식 검사
```

공용 스크립트는 `shared/scripts/` 만 고치고 `tools/build.py` 로 복사한다. 스킬 폴더의 사본을 직접 고치면 테스트가 실패한다.

## 알려진 한계

- 스킬 문서와 출력은 한국어다.
- Codex CLI 의 `--sandbox read-only` 는 환경에 따라 조용히 빠질 수 있다. 그래서 `hglos-verify` 는 git 기반 무결성 가드로 읽기 전용을 따로 확인한다.
- `stale_ids.py` 는 사람이 훑을 후보 목록을 내는 도구다. 정밀도가 높지 않다 (코드 펜스 안 예시 줄도 잡는다).
- 병렬 동시 실행 수 상한(기본 3)과 작업 크기 하한은 경험값이다.

## 라이선스

[MIT](LICENSE)
