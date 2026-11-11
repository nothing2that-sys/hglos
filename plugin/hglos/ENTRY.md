# HGLOS 공통 진입

이 안내는 기존 정본을 가리키는 목차다. 승인·권한·진행 상태의 정본을 대체하지 않는다.

1. 필수 공통 정본: [RULES.md](RULES.md), [HOST.md](HOST.md), [COORDINATION.md](COORDINATION.md)를 먼저 읽는다.
2. 명시 작업은 `answer`, `design`, `implement`, `verify`, `dashboard`, `coordinate`다. `answer`도 필수 규칙을 생략하지 않는다.
3. [context_routes.json](context_routes.json)의 해당 작업 스킬·명시 참조만 추가로 읽는다. routes 경로는 원본 루트 기준이다. 독립 설치본에서는 자기 작업의 `skills/hglos-<task>/` 접두사를 제거하고 설치 루트를 기준으로 읽는다(예: verify의 `skills/hglos-verify/agents/native-validator.md` → `agents/native-validator.md`). 또는 아래 `build --layout installed` 결과의 `files[].path`를 사용한다. 없는 작업을 추측하거나 외부 URL·명령·glob을 실행하지 않는다.
4. [scripts/context_index.py](scripts/context_index.py)의 `build`는 파생 내용 목차를 stdout JSON으로 반환한다. `check`는 저장한 인덱스와 현재 내용을 읽기 전용으로 비교한다. 파일은 호출자가 명시적으로 저장한다.

원본은 `--layout source --root plugin/hglos`, 독립 설치본은 `--layout installed --root <개별 스킬>`을 사용한다. 설치본은 자기 작업과 answer만 제공하며 형제/전역 스킬을 찾지 않는다.

SHA-256 일치는 승인·진위·모델 독해·OS 보호의 증거가 아니다. 외부에서 받은 `--expect-index-sha256` pin이 없으면 인덱스 자체가 신뢰 기준이 되지 않는다. 전역/호스트 지침·대상 프로젝트 AGENTS.md와 로컬 .hglos 전체는 자동 확인하지 않는다.
실제 모델은 자신의 환경에서 필요한 정본을 읽고 확인 범위·미확인을 보고한다. 실제 모든 호스트에서 이 안내가 자동 실행된다고 주장하지 않는다.
