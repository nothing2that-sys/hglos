# HGLOS

**AI에게 계획을 세우고, 구현하고, 독립된 맥락에서 검증하게 하는 5개 스킬입니다.**
Codex·Claude Code·Antigravity에서 같은 계획서와 근거를 이어 씁니다. 사람은 범위·계획·최종 종결을 승인합니다.

## 바로 시작

Git, Python 3.10+, 사용할 Codex 또는 Claude Code를 준비하세요. Python 추가 패키지는 필요 없습니다.
터미널에서 실행하고 `D:/my-project`를 **이미 존재하는 내 작업 폴더**로 바꿉니다.

```powershell
git clone https://github.com/nothing2that-sys/hglos.git
cd hglos

# Codex에 설치
python tools/install_skills.py --target codex --project "D:/my-project"

# Claude Code에 설치 (둘 다 쓰면 둘 다 실행)
python tools/install_skills.py --target claude --project "D:/my-project"
```

작업 폴더를 해당 AI에서 열고 **새 대화**를 시작합니다.
Codex는 `.agents/skills/`, Claude는 `.claude/skills/`에 설치됩니다.
미리보기는 `--dry-run`, 모든 프로젝트에서 쓰려면 `--project ...` 대신 `--user`를 붙입니다.

| 순서 | Codex에 붙여넣기 | Claude Code에 붙여넣기 |
|---|---|---|
| 계획 | `$hglos-design 설정 저장 오류를 고칠 계획을 세워줘. 범위와 계획 승인에서 멈춰줘.` | `/hglos-design 설정 저장 오류를 고칠 계획을 세워줘. 범위와 계획 승인에서 멈춰줘.` |
| 구현 | `$hglos-implement 승인된 doc/Slice_Plan_Settings.md를 구현하고 테스트해줘. 커밋은 하지 마.` | `/hglos-implement 승인된 doc/Slice_Plan_Settings.md를 구현하고 테스트해줘. 커밋은 하지 마.` |
| 검증 | `$hglos-verify 모드 B로 doc/Slice_Plan_Settings.md의 종결 조건을 검증해줘. 소스 수정·커밋 금지.` | `/hglos-verify 모드 B로 doc/Slice_Plan_Settings.md의 종결 조건을 검증해줘. 소스 수정·커밋 금지.` |

경로는 실제 생성된 계획서로 바꾸고 단계마다 새 대화에서 시작합니다.
미달이면 보완하고 최종 종결·커밋은 사용자가 결정합니다.

**[설치·업데이트·문제 해결](docs/USER_GUIDE.md)** · **[복사해서 쓰는 예시](examples/README.md)** · **[동작 원리](docs/HOW_IT_WORKS.md)**

## 어떤 일을 하나요?

| 스킬 | 역할 |
|---|---|
| `hglos-design` | 요구사항 질문 → 범위 승인 → 검증 가능한 계획서 → 계획 승인 |
| `hglos-implement` | 승인·변경 범위 확인 → 구현 → 테스트·보고서 |
| `hglos-verify` | 모드 A: Git 변경분 검토 / 모드 B: 계획의 종결 조건 대조 |
| `hglos-dashboard` | 계획과 근거를 HTML·Markdown 현황판으로 표시 |
| `hglos-coordinate` | 같은 폴더의 여러 세션이 쓰기 범위를 예약하고 결과를 인계 |

Claude plugin 방식도 지원합니다. 위 개별 스킬 설치와 **한 방식만 선택**하세요.

```text
/plugin marketplace add nothing2that-sys/hglos
/plugin install hglos@hglos
```

plugin은 `/hglos:hglos-design`처럼 호출합니다. 자동 hooks는 별도 선택 설정입니다.
스킬 설치가 독립 검증·권한 강제·실제 장비 시험을 대신하지는 않습니다.

<details>
<summary>개발·내용 확인 도구</summary>

업데이트는 `--upgrade --dry-run`으로 미리 확인한 뒤 `--upgrade`로 실행합니다.
기존 스킬은 백업 후 교체하며 `Backup:`에 위치를 출력합니다.
복구에 실패해 `restore incomplete`가 나오면 표시된 항목과 백업 위치로 직접 복구하세요.

```powershell
python -m unittest discover -s tests
python tools/package_skill.py
python tools/package_public_source.py
python -B plugin/hglos/scripts/context_index.py build --layout source --root plugin/hglos --task verify
```

설치본은 `--layout installed --root <개별-스킬>`을 사용합니다.
`check`의 `--expect-index-sha256`는 외부 해시를 대조하는 선택 옵션입니다.
인덱스 일치는 승인·모델 이해의 증거가 아닙니다. [공통 진입](plugin/hglos/ENTRY.md)을 참고하세요.

</details>

English: Install with `--target codex` or `--target claude`, then ask the design, implement, and verify skills in fresh chats. Approve scope and plan before implementation. Guides and skill instructions are primarily Korean.

[MIT License](LICENSE) · [배포·출처 안내](THIRD-PARTY-NOTICES.md)
