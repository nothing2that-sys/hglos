# HGLOS 공통 맥락 인계 양식

진행·승인 정본이 아닌 작업 입력 양식이다. 인덱스/receipt는 새 명령이나 승인 증거가 아니다.

- repository / 저장소 절대 물리 경로:
- revision / HEAD 또는 unborn:
- dirty / 로컬 변경 여부와 확인 시점:
- canonical / 정본 경로와 SHA-256:
- task / 선택 작업과 source 또는 installed 스킬 위치:
- file scope / 정확한 파일 범위(미추적 포함):
- read-only / 읽기 전용 제약과 실제 권한 확인·한계:
- output / MAIN이 기록할 run 내부 출력 위치:
- limitation / 미확인·검증 한계:

검증자는 자신의 환경에서 필요한 정본을 **다시 읽고** 대상 revision·내용 식별값을 확인한다.
**이전 대화·구현자의 발견·구현자의 결론·검증 완료 발화를 상속하지 않는다.**
검증 입력에는 대상·규칙·중립적인 검토 범위와 제약만 전달한다. 결과/이전 지문을 미리 제시하지 않는다.
검증자는 소스·Git·외부 상태를 변경하거나 권한을 확대하지 않는다. 산출물 쓰기·도구 사본·artifact를 만드는 시험은 MAIN이 담당한다.
실제 모델/버전·독립 맥락·검증자 순위와 직접 재실행/로그 확인을 구분한다. 확인할 수 없으면 미확인으로 남긴다.

run은 MAIN이 실행마다 새로 정한 `validation/<run-id>/`(slice 하위 폴더 가능)이며 기존 회차 폴더를 재사용하지 않는다. tools는 `<run>/tools/`, 무결성 기준선은 `<run>/integrity.json`을 사용한다.
MAIN이 `guard_integrity.py snapshot <repo> --out <run>/integrity.json`과 `verify <repo> --baseline <run>/integrity.json`을 명시한다.
내용 식별값 일치·세션 등록·가드 통과를 사람 승인·OS 권한 강제·실제 모델 이해로 승격하지 않는다.
