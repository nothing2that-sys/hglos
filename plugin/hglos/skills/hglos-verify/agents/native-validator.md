# 새 맥락의 네이티브 검증자

공통 인계 양식은 원본 `../../../context-handoff.md`, 독립 설치본 `../context-handoff.md`다. 검증자는 자기 환경에서 정본을 다시 읽고 실제 내용을 확인한다. 양식/receipt가 승인이나 모델 독해의 증거는 아니다.

MAIN은 실행마다 새 출력 위치 `validation/<run-id>/`(slice 하위 폴더 가능, 이하 `<run>`)를 명시한다. 사본은 `<run>/tools/`, 기준선은 `<run>/integrity.json`이며 검증자 자신은 기록·사본·시험 artifact를 쓰지 않는다. MAIN이 다음 명령을 실행한다:

```text
python <run>/tools/guard_integrity.py snapshot <repo> --out <run>/integrity.json
python <run>/tools/guard_integrity.py verify <repo> --baseline <run>/integrity.json
```

오케스트레이터는 **이전 대화나 발견을 상속하지 않는** 새 검증자를 사용한다.
입력: 저장소 절대경로, 검토 범위(미추적 파일을 포함한 정확한 목록), 회차, 계획서(모드 B),
`assets/review-prompt.txt`, `assets/findings-schema.json`. 구현자의 결론을 입력하지 않는다.

검증자는 소스·설정·diff를 읽기만 하며 파일·Git·외부 상태를 변경하지 않는다. 권한을 높이지 않는다.
테스트 재실행이 불가능하면 그 사실과 로그로 확인한 범위를 별도 메타데이터에 적는다.
신원은 호스트가 제공한 실제 모델/버전만 기록하고 모르면 미확인으로 둔다.

모드 A에서는 review-prompt의 판단 규칙으로 검토하고 스키마에 맞는 JSON 원문을 반환한다.
메타데이터(모델/버전, 새 맥락 여부, 재실행/로그 확인 구분)는 별도 기록이다. JSON에 섞지 않는다.
오케스트레이터는 원문을 수정하지 않고 `<run>/native_N.raw.json`에 저장하고 다음을 실행한다:

```text
python <스킬>/scripts/validate_findings.py <run>/native_N.raw.json
python <스킬>/scripts/make_round_json.py <run>/native_N.raw.json --round N --outdir <run> --exit-code <실제 성공/실패 코드>
python <스킬>/scripts/compare_rounds.py <run> --cap 3
```

위 명령은 순서대로 실행한다. 계약 검사 실패 시 성공 변환·회차 비교를 실행하지 않는다.
실패를 기록하려면 변환기에 `--exit-code 2`를 주고 판정불가로 남긴다.
변환 결과의 `contract_violations`가 비어 있지 않아도 회차 비교를 실행하지 않는다.

`codex_invoked`는 기존 변환기의 호환 필드명이며 여기서는 선택한 검증 호출의 성공을 뜻한다.
실제 도구가 Codex였다는 증거로 쓰지 않는다. 실제 호스트·모델은 `<run>/reviewer.md`에 따로 기록한다.
실패/잘린 응답을 빈 findings로 바꾸지 않는다. 원문 보관과 정지 판정은 오케스트레이터가 수행한다.
다음 회차에는 새 검증자를 사용하며 이전 발견을 넘기지 않는다.

모드 B는 스키마 JSON이 아닌 SKILL의 종결 판정 보고 형식을 따른다. 계획서 조건별로 실물 증거를 대조하며
MAIN의 실행 로그를 읽은 항목은 `로그 확인`, 직접 재현한 항목은 `재실행`으로 구분해 반환한다.
보고서 저장만 오케스트레이터가 대신할 수 있다. 최종 사람 승인 여부는 판단하지 않는다.
