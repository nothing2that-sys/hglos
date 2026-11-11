# HGLOS 사용 설명서

## 설치와 확인

HGLOS는 사용하는 AI에게 절차와 도구를 제공하는 스킬입니다.
Git, Python 3.10 이상과 Codex 또는 Claude Code를 준비하고 호스트에 로그인합니다.
기본 설치에는 pip·API 키가 필요 없습니다. 호스트 구독·모델 이용 조건은 별도입니다.
[README](../README.md)의 설치 명령을 실행하세요. 대상 프로젝트 폴더는 먼저 만들어 둡니다.
macOS/Linux는 `D:/my-project`를 `/path/to/my-project`로 바꾸면 됩니다.

| 설치 범위 | Codex | Claude Code |
|---|---|---|
| 현재 프로젝트 | `--target codex --project "D:/my-project"` | `--target claude --project "D:/my-project"` |
| 모든 프로젝트 | `--target codex --user` | `--target claude --user` |
| 설치 위치 | 프로젝트 `.agents/skills/`, 사용자 `~/.agents/skills/` | 프로젝트 `.claude/skills/`, 사용자 `~/.claude/skills/` |

위 인자는 `python tools/install_skills.py` 뒤에 붙입니다. `--target all`은 Antigravity까지 포함합니다.
새 대화에서 스킬 목록과 이름을 명시한 호출을 확인하세요. 파일 설치와 실제 모델 수행은 별개입니다.
plugin과 개별 스킬은 중복 설치하지 않는 편이 좋습니다.
설치기는 기존 `AGENTS.md`·`CLAUDE.md`·hooks를 편집하지 않습니다.
항상 적용할 규칙은 [RULES.md](../plugin/hglos/RULES.md)의 붙여넣기 블록을 필요한 만큼 기존 지침에 병합하세요.

설치 경로·호출 참고: [Codex 공식 문서](https://learn.chatgpt.com/docs/build-skills),
[Claude Code 공식 문서](https://code.claude.com/docs/en/skills). 호스트 버전에 따라 표시 방식은 다를 수 있습니다.

## 한 작업을 끝내는 순서

1. **설계**: 목표·증상·보존할 계약을 설명하고 질문에 답합니다. 범위와 계획을 각각 확인·승인합니다. `Slice_Plan`은 한 작업의 목표·종결 조건·범위·승인 문서입니다.
2. **구현**: 새 대화에 승인된 계획서 경로를 줍니다. 승인·변경 여부를 확인한 뒤 구현하고 테스트·보고서를 남깁니다. MAIN만 수정하도록 정한 프로젝트는 MAIN이 직접 구현합니다.
3. **검증**: 구현과 독립된 새 맥락에 계획서·변경 범위·근거 위치를 줍니다. 검증자는 소스를 읽기 전용으로 조사합니다. 산출물을 쓰는 빌드·테스트는 MAIN이 실행하고 검증자는 로그 확인 범위를 명시합니다.
4. **종결**: 통과/미달/조건부/측정 불가/미검증을 읽고 보완 여부와 커밋을 결정합니다. 테스트 통과가 사람의 종결 승인으로 바뀌지는 않습니다.

가능하면 다른 회사 모델로 검증하고 실제 사용한 모델과 한계를 기록합니다.
별도 검증 수단이 없으면 새 세션용 전달문으로 이어갑니다. 자체 점검을 독립 검증으로 표시하지 않습니다.
계획 변경 시 변경 내용을 다시 승인합니다. 유효한 기존 승인은 불필요하게 반복하지 않습니다.
승인 커밋 또는 커밋 금지 환경의 해시 절차는 [HOST.md](../plugin/hglos/HOST.md)를 따릅니다.

## 대시보드만 써 보기

저장소 루트에서 실행합니다. 예제는 승인·실행 결과가 없는 학습용 계획서입니다.

```powershell
python plugin/hglos/skills/hglos-dashboard/scripts/dashboard.py --repo . --plan examples/Slice_Plan_Settings.md --output validation/example-dashboard.html --diagram-dir validation/example-diagrams
```

생성된 `validation/example-dashboard.html`을 브라우저로 엽니다. 서버·JavaScript·외부 폰트가 필요 없습니다.
재생성은 기존 생성물을 확인한 뒤 `--overwrite`를 붙입니다. 문서 스냅샷이며 실시간 감시·자동 완료 판정은 하지 않습니다.

## 같은 폴더에서 Codex와 Claude 함께 쓰기 (선택)

스킬 설치만으로 충돌 차단이 켜지지는 않습니다. 먼저 미리보기를 확인합니다.

```powershell
python plugin/hglos/scripts/setup_coordination.py --repo "D:/my-project" --host both
python plugin/hglos/scripts/setup_coordination.py --repo "D:/my-project" --host both --apply
```

`--apply`는 대상의 HGLOS 규약·호스트 지침·hook 설정과 고정 스크립트 사본을 준비합니다.
기본은 한 명만 작성하고 나머지는 관측합니다. 범위별 병렬 편집은 최초 설치에 `--mode scoped`를 지정합니다.
호스트의 프로젝트/hook 신뢰 절차를 완료하고 새 세션에서 실제 차단을 확인하세요.
Codex hook 지원·Claude 실패 차단은 설치 버전에 의존하므로 HOST.md를 확인하세요.
OS sandbox가 아니며 외부 편집기를 차단하지 않습니다. 종료됐다는 추정으로 다른 세션 선점을 회수하지 마세요.
수동 참여·복구·인계는 [COORDINATION.md](../plugin/hglos/COORDINATION.md)를 참고하세요.

## 업데이트와 제거

```powershell
git pull --ff-only
python tools/install_skills.py --target codex --project "D:/my-project" --upgrade --dry-run
python tools/install_skills.py --target codex --project "D:/my-project" --upgrade
```

Claude는 `--target claude`로 바꿉니다. 동일 파일은 유지하고 기본 설치는 다른 기존 파일을 거절합니다.
`--upgrade`는 로컬 수정본도 백업 후 교체합니다. `Backup:` 경로를 보관하고 `restore incomplete`가 나오면 해당 백업으로 복구합니다.
plugin 갱신은 `claude plugin update hglos@hglos`입니다.
hooks 고정 사본은 스킬 갱신과 별개로 setup `--apply`를 다시 실행해야 갱신됩니다.
제거는 설치 위치의 다섯 `hglos-*` 폴더만 보관 후 제거합니다. 다른 스킬·프로젝트 자료는 지우지 마세요.
hooks를 설정했다면 고정 사본 삭제 전 호스트 설정의 HGLOS hook 항목도 확인합니다.
plugin 제거는 `claude plugin uninstall hglos@hglos`입니다.

## 문제 해결

| 증상 | 조치 |
|---|---|
| Windows에서 `python` 실행 실패 | Store 별칭 대신 `py -3` 또는 실제 Python 경로 사용 |
| 스킬이 안 보임 | 프로젝트·설치 위치 확인 → 새 대화 → 이름을 명시해 호출 |
| `Existing skill differs` | 기존 파일 확인 후 `--upgrade --dry-run`으로 교체 범위 확인 |
| 설치 lock 잔류 | 실제 설치 프로세스가 없음을 확인한 뒤 강제 종료 잔여 lock만 정리 |
| 구현이 승인을 요구함 | 실제 계획서 승인란과 승인 이후 변경 여부 확인 |
| 검증 CLI·인증 실패 | 실패·미검증을 기록하고 새 세션 전달문 사용. 권한 우회 금지 |
| 장비 시험을 못 함 | 장비 조건은 미검증 유지. 단위 테스트로 대체 PASS하지 않음 |

교차 CLI·ReviewGate는 선택 기능입니다. 별도 설치·인증·전송 동의·실행 권한이 필요할 수 있고 API 이용은 과금될 수 있습니다.
기본 시작에는 필요 없습니다. [동작 원리](HOW_IT_WORKS.md) · [예시](../examples/README.md).
