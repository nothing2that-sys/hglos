---
name: hglos-coordinate
description: Coordinate Claude Code, Antigravity and Codex sessions sharing a physical project folder; register participants, reserve write scopes, inspect conflicts, and hand off versioned artifacts. Antigravity participates through project instructions and manual CLI. Use for concurrent-session coordination and monitoring, not project approval or automatic job scheduling.
---

# HGLOS 세션 조정

공통 진입을 먼저 읽는다: 원본 `../../ENTRY.md`, 설치본 `ENTRY.md`. 필수 공통 규칙을 생략하지 않는다.

서로 다른 로컬 세션이 같은 폴더에서 작업할 때 참여 세션과 선점 범위를 확인한다.
먼저 협업 규약 `COORDINATION.md`와 호스트 경계 `HOST.md`를 읽는다. 공통 규칙은 `RULES.md`다.
설치 번들에서는 이 skill 폴더, 원본 Claude plugin에서는 두 단계 위
(`../../COORDINATION.md`, `../../HOST.md`, `../../RULES.md`)가 문서 위치다.
스크립트 경로도 실제 파일 존재로 고른다: 설치본은 `<skill>/scripts`, 원본 plugin은 `<skill>/../../scripts`다.
아래 `<scripts>`는 이렇게 확인한 절대 경로다. 사용자 작업·권한 범위를 지킨다.
규약이 설치되었는지와 실제 hooks가 작동하는지는 별개다. 자동 보호를 실측 없이 주장하지 않는다.

## 처음 설치

Python 3.10+ 표준 라이브러리만 사용한다. 설치·hook 실행에 같은 실제 Python 실행 파일을 쓴다.

```text
python <scripts>/setup_coordination.py --repo <project> --host both
python <scripts>/setup_coordination.py --repo <project> --host both --apply
```

Antigravity에서는 `--host antigravity`, 세 호스트를 함께 준비하려면 `--host all`을 쓴다.
Antigravity 선택은 공통 규약·registry와 `GEMINI.md` 지침만 준비하며 다른 호스트의 hook 설정은 만들지 않는다.

첫 명령은 변경할 파일 목록만 출력한다. `--apply`는 이 작업으로 프로젝트 hooks/지침 변경을 요청받았을 때만
사용한다. 기존 host 설정과 지침은 보존하고 HGLOS 항목을 추가한다. 이전 HGLOS hook 항목은 미리보기에
교체 대상(`replaced_hooks`)으로 보이고 `--apply`에서 교체된다. `.hglos/` 정책 문서는 `.hglos/bin/manifest.json`에
기록된 해시(manifest 이전 설치는 d844186 배포판 해시)와 같은 미수정 관리 사본이면 교체하고 미리보기의
`replaced_policies`에 표시한다. 로컬 수정본은 unified diff를 보여 주고 중단하며 파일을 바꾸지 않는다.
hooks는 `.hglos/bin/` 고정 사본을 실행하므로 스킬 갱신 후 사본 갱신은 setup `--apply` 재실행으로 한다.
공통 `RULES.md`와 실행 협업 `COORDINATION.md`를 `.hglos/`에 함께 배치해 다른 LLM도 같은 규칙을 읽게 한다.
기본은 전체 폴더 작성자 한 명이다. 처음부터 범위별 병렬 편집이 필요한 프로젝트는 설치 시 `--mode scoped`를
명시한다. 실행 중 모드 변경은 지원하지 않는다. 기존 대화에 hook가 소급 적용되었다고 가정하지 않는다.

Codex는 프로젝트/정의 신뢰 절차가 필요하다. Claude의 `onFailure: block`은 v2.1.295+가 필요하며
설치 버전의 PowerShell 지원과 실제 실패 차단도 확인한다.
호스트의 신뢰/권한 검사를 우회하거나 설정을 전역으로 설치하지 않는다. Antigravity 및 다른 호스트는
공식 hook 연동을 가정하지 않고 아래 수동 CLI로 참여한다. Gemini Gem은 로컬 runtime에 참여하지 않는다.

## 작업과 현황

```text
python <scripts>/coordination.py --repo <project> register --host antigravity --task "UI 검토"
python <scripts>/coordination.py --repo <project> claim --session <id> --version 1
python <scripts>/coordination.py --repo <project> status --watch --interval 3 --count 20
python <scripts>/coordination.py --repo <project> release --session <id> --version 1 --quiescent
python <scripts>/coordination.py --repo <project> snapshot
```

register 출력의 ID/version을 사용한다. hook가 이미 등록한 세션이면 hook context의 ID로 작업 목적만 갱신한다.
hooks를 적용한 프로젝트의 대화 안 제어 명령은 hook context에서 `$ `로 시작하는 줄(`status`, `release`)을 `$ `만 떼고
그대로 한 번에 하나씩 실행한다. 다른 하위 명령(`claim`, `register`, `rebaseline`)은 같은 접두어(고정 사본
`<project>/.hglos/bin/coordination.py`와 `--repo`)에 하위 명령과 인자를 붙인 단독 명령으로 실행하며 파이프·연결은 쓰지 않는다.
다른 위치의 `coordination.py`(스킬의 `<scripts>/` 포함)는 내용이
같아도 제어 명령으로 인정되지 않고 일반 shell 호출로 취급된다. Python은 스크립트 폴더의 모듈을 먼저 불러오기 때문이다.
scoped 모드에서는 `claim`에 반복 가능한 `--write FILE`, `--tree DIR`, `--resource NAME`을 쓸 수 있다.
범위 생략은 전체 선점이다. shell과 알 수 없는 도구는 그 호출 동안 전체를 선점하므로 다른 작성자의
선점이 있으면 대기한다. 호출이 끝나면(Post 또는 응답 종료 정리) 해제된다.
release는 자식 writer가 모두 정지했음을 확인한 뒤 수행한다. 반환된 새 version을 다음 요청에 사용한다.
같은 branch의 커밋 전진은 자동 반영된다. branch 전환·HEAD 재작성 뒤 쓰기가 거절되면 작성 정지를 확인하고
`coordination.py --repo <project> rebaseline --session <id> --version <n> --quiescent`로 같은 ID를 복구한다.

세션이 비정상 종료되면 시간이 지나도 선점을 유지한다. 복구 담당자는 작성 프로세스 정지를 확인한 후
`recover --session ID --version N --quiescent --reason "정지 확인 근거"`를 별도 운영 터미널에서 수행한다.

## 결과 인계

```text
python <scripts>/coordination.py --repo <project> handoff --session <id> --version <n> --artifact doc/result.md --note "실행 근거와 남은 제한"
python <scripts>/coordination.py --repo <project> verify-handoff --id <handoff-id>
```

후속 세션은 해시 일치와 실제 내용을 확인한다. 정확성·PASS·사람 승인은 별도 근거로 판단한다.
후속 세션 자동 실행이나 단계 전이는 하지 않는다. 상세 동작·제약·호스트 문서 출처는
[coordination reference](references/coordination.md)를 참조한다.
