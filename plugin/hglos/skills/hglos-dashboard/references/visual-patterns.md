# 공개 스킬에서 참고한 시각화 패턴

## 2026-10-23 시각 중심 개정

Tabler/shadcn/AdminLTE의 카드·시각 계층과 Plane/AppFlowy/Grafana 등의 관계/상세 표현을
아이디어로 참고했다. 상류 코드·CSS·아이콘·이미지·폰트·JS를 복사하지 않고 HGLOS용으로 작성했다.
라이선스와 참고 범위는 스킬 동봉 `LICENSE`, `THIRD-PARTY-NOTICES.md`를 따른다.

HTML은 상태 요약 → 각 계획의 현재/다음 행동 → 큰 after 관계도 → compact 작업 details →
세션 신고/선점 상세 → 원문 순서다. 원문·SHA는 상세 안에 보존한다. 상태 카드는 집계 목록으로,
HTML 관계도의 노드는 작업 상세로 이동한다. fragment 이동 후 summary를 눌러 펼친다.
독립 SVG에는 HTML 내부 링크를 넣지 않는다. 좁은 화면에서는 관계도 영역을 스크롤한다.
Markdown Mermaid도 같은 상태 분류·색을 쓰며 알 수 없는 원문에 미검증 표기를 함께 남긴다.

요약은 계획서별 작업 정의 행 집계다. 다중 계획의 같은 이름을 합치거나 작업↔세션을 유사도로
연결하지 않는다. 도식을 먼저 준비해 순환 오류도 상단 집계 전에 확인한다.
날짜 이력이 없는 타임라인, 실시간 폴링, 자동 상태 전이·승인, 진행률/소요시간 추정은 추가하지 않았다.

조사일: 2026-10-22. 아래 원문·README·LICENSE를 확인했다. 원문 코드·CSS·템플릿을 번들에
복사하지 않고 표현 아이디어를 HGLOS의 문서 정본과 승인 계약에 맞춰 독자 구현했다.
아래 링크는 main/master 기준이며 조사 시 HEAD SHA는 확인하지 못했다. 상위 문서 변경 가능성이 있다.

| 공개 스킬 | 원문 근거 | 차용한 방식 | HGLOS 구현 |
|---|---|---|---|
| nicobailon/visual-explainer | [SKILL](https://github.com/nicobailon/visual-explainer/blob/main/plugins/visual-explainer/SKILL.md), [plan](https://github.com/nicobailon/visual-explainer/blob/main/plugins/visual-explainer/references/plans.md), [recap](https://github.com/nicobailon/visual-explainer/blob/main/plugins/visual-explainer/commands/project-recap.md) | 그림을 먼저 제시하고 상태를 색 외에 형태·문자로도 표현, 근거를 연결 | 단계 흐름도·상태 기호·출처 행·요약 카드 |
| garrytan/gstack diagram | [SKILL](https://github.com/garrytan/gstack/blob/main/diagram/SKILL.md.tmpl) | 편집 가능한 텍스트 도식 원본과 오프라인 벡터 산출물을 함께 제공 | 동일 문서 모델에서 Mermaid 소스와 SVG 생성. Excalidraw·PNG 변환은 미구현 |
| obra/superpowers | [writing-plans](https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md), [executing-plans](https://github.com/obra/superpowers/blob/main/skills/executing-plans/SKILL.md), [verification-before-completion](https://github.com/obra/superpowers/blob/main/skills/verification-before-completion/SKILL.md) | 작업 정의·검증 단위와 실행 관계를 명시 | 쓰기 범위·oracle·검증 계획을 보드에 연결하고 after를 화살표로 표시 |
| OthmanAdi/planning-with-files | [SKILL](https://github.com/OthmanAdi/planning-with-files/blob/master/skills/planning-with-files/SKILL.md), [plan template](https://github.com/OthmanAdi/planning-with-files/blob/master/skills/planning-with-files/templates/task_plan.md), [progress template](https://github.com/OthmanAdi/planning-with-files/blob/master/skills/planning-with-files/templates/progress.md) | 재개할 때 현재 단계·다음 행동을 빠르게 파악하고 계획과 관측 기록을 구분 | Current State 네 필드를 요약 카드로 표시하고 판정 수단과 근거를 분리 |

원문 LICENSE는 네 저장소 모두 MIT다:
[Visual Explainer](https://github.com/nicobailon/visual-explainer/blob/main/LICENSE),
[gstack](https://github.com/garrytan/gstack/blob/main/LICENSE),
[Superpowers](https://github.com/obra/superpowers/blob/main/LICENSE),
[Planning with Files](https://github.com/OthmanAdi/planning-with-files/blob/master/LICENSE).
이는 라이선스 본문 확인 기록이다. 향후 원문 코드·상당 부분을 실제 복사할 때는 해당 판의 notice 보존 조건을 별도로 적용한다.

## 상태 입력 계약

추가 조사: [Anatomy SKILL](https://github.com/wheresryan22/anatomy/blob/1482e8469b0a9e0d8cc18ea2740a01423d29ae80/SKILL.md),
[단일 상태 모델](https://github.com/wheresryan22/anatomy/blob/1482e8469b0a9e0d8cc18ea2740a01423d29ae80/examples/ripple-tank/model.mjs),
[상호작용](https://github.com/wheresryan22/anatomy/blob/1482e8469b0a9e0d8cc18ea2740a01423d29ae80/references/motion.md),
[검증](https://github.com/wheresryan22/anatomy/blob/1482e8469b0a9e0d8cc18ea2740a01423d29ae80/references/verify.md).
2026-10-22 확인, 고정 SHA `1482e8469b0a9e0d8cc18ea2740a01423d29ae80`.
수치·도식·설명을 같은 모델에서 만드는 방식과 선택한 부분을 펼치는 표현을 차용했다.
원문 코드/CSS를 복사하지 않았다. 40개 이상 부품 제작·3D/WebGL·idle tour는 대시보드에 적용하지 않는다.
전이 캡처·접근성 점검은 참고할 검증 방법이며 실제 실행 증거와 구분한다.

## 세션 소유 관계 입력과 표현

명시적으로 선택한 coordination snapshot의 `sessions`만 사용한다. DB나 실제 파일을 열어 상태를 보충하지 않는다.
동일한 읽기 모델에서 세션/선점/미종료 호출/대기·차단 신고/연락 불명 수, 세션 카드,
소유 관계 SVG, 동일 이름별 파일·자원 소유 목록과 Markdown을 생성한다.
native details/summary를 사용하며 JavaScript·추가 권한·실시간 polling이 필요하지 않다.

- SVG 화살표는 세션→선점 범위다. 실행 순서, 현재 차단자 또는 충돌 없음의 증거가 아니다.
- 선점 12개 초과는 도식을 생략하고 전체 이름을 텍스트에 보존한다. 짧은 SVG 이름도 전체 목록으로 확인한다.
- `reason`은 마지막 신고 원문이다. 잘린 JSON·임의 heartbeat 이유를 해석해 현재 대기 그래프를 만들지 않는다.
- `ended`/`suspect`의 남은 선점도 그대로 표시한다. 미종료 호출 0은 release 허가가 아니다.
- 같은 이름별 소유 목록은 부분 경로·전체 영역의 겹침을 판정하지 않는다. 병렬 허용은 coordination CLI로 확인한다.
- schema 1을 유지한다. 기존 optional owner/version이 없는 자료는 상위 세션 소유/미기록으로 표시한다.
  제공된 owner가 상위 세션과 다르거나 ID·선점·호출이 중복되면 거절한다.
- 실제 UI 확인에서는 좁은 화면의 가로 넘침·잘림, 긴 이름, 펼치기, Tab/Enter/Space, focus 표시,
  텍스트/도식 일치와 인쇄를 본다. 자동 XML·HTML 구조 검사만으로 UI/접근성 통과를 선언하지 않는다.

## 계획 상태 입력 계약

기존 계획서의 `작업 분할` 표를 작업 정의로 사용한다. `작업` 값이 ID이며 중복되면 도식은 미검증이다.
`after`는 쉼표/세미콜론 구분 또는 JSON 문자열 배열이다. 작업명에 쉼표가 있으면 JSON 배열을 쓴다.
빈칸·`없음`·`-`·`none`·`[]`는 선행 없음이다. 공백만으로 이름을 분리하지 않는다.
모든 선행 ID가 실제 작업 정의에 있어야 하며 순환은 허용하지 않는다.
도식의 좌우 배치는 선행 관계다. 실행 일정·병렬 가능 여부는 `check_parallel.py`로 별도 판단한다.
24개 초과 작업은 읽기 어려운 단일 도식을 생략하고 보드와 원문을 표시한다.

**상태는 Current State 안에서만 갱신한다.** 승인 후 작업 분할 절의 변경을 상태 갱신 수단으로 쓰지 않는다.
아래 두 표는 선택 사항이다. 표가 없으면 상태는 미기재·미검증이고 문맥에서 추정하지 않는다.

```markdown
## Current State
- 현재 위치: 구현 중
- 다음 한 단계: 회귀 검증
- 결정 필요: 장비 검증 일정
- 확인된 사실: 소스 변경 완료, 실행 로그 미확인

### 단계 현황
| 단계 | 상태 | 근거 |
|---|---|---|
| 설계 | 완료 | 승인 발화와 계획서 위치 |
| 구현 | 진행 중 | 작업 보고서 위치 |
| 검증 | 미검증 | 독립 보고 미수신 |

### 작업 현황
| 작업 | 상태 | 근거 |
|---|---|---|
| A | 완료 | 실행 로그 위치 |
| B | 차단 | 장비 사용 대기 |
```

지원 상태: `대기/pending/todo`, `진행 중/진행중/in_progress`, `완료/done/completed`,
`차단/blocked`, `미검증/unverified`. 원문과 기호를 함께 표시한다. 다른 값은 미검증 칸에 원문 그대로 표시한다.
중복 단계/작업 상태와 정의에 없는 작업 상태는 경고한다. 단계는 설계(계획)/구현/검증만 대응한다.
ATX 제목과 pipe 표를 지원하며 fenced 예제와 HTML 주석은 상태 입력에서 제외한다.
각 표의 `근거`는 문서에 기재된 문자열이며 링크 대상의 실행 결과나 최신성을 자동 검증한 것이 아니다.
작업 완료·종결 승인·게이트 통과는 서로 다르다. 한 작업의 완료에서 다음 단계 승인이나 전체 완료를 추정하지 않는다.

## 출력과 확인

- HTML: 단계 흐름도, 재개 카드, 5개 상태 칸의 작업 보드, after 관계도, 종결 조건/판정 수단/근거표, 우려 표기 막대.
- Markdown: Mermaid 코드 블록과 원문. Mermaid 미지원 뷰어에서도 텍스트 원본은 읽을 수 있다.
- `--diagram-dir`: 계획서별 `workflow`와 `dependencies`의 `.svg`와 `.mmd`. 원본 파일명·SHA-256을 포함한다.
- 명령·임계값은 계획이고 근거 열은 기록이다. 근거가 없으면 실행 증거 미검증이다. 경로만 있다고 PASS로 바꾸지 않는다.
- SVG/CSS는 내장하고 JavaScript·CDN·자동 배포는 사용하지 않는다. 실제 브라우저 시각 확인은 별도 검증이다.
- 원문 정본·승인·보존 범위가 먼저다. 외부 스킬의 자동 커밋·권한 확대·프로젝트 상태 엔진은 도입하지 않았다.
