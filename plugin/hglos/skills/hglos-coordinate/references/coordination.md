# Coordination implementation contract

공용 `scripts/coordination.py`가 로컬 SQLite의 한 `BEGIN IMMEDIATE` transaction에서
충돌 조회와 선점 추가 전체를 처리한다. 세션별 JSON 읽기/쓰기 경쟁으로 소유권을 정하지 않는다.
실패한 범위 확장은 기존 선점을 보존하며 일부 추가 범위도 얻지 않는다.

`exclusive`에서는 전체 폴더 선점만 사용한다. `scoped`에서는 exact file, subtree, 자원 이름을 사용한다.
subtree는 아직 없는 파일도 포함한다. 경로 비교는 대소문자를 접어 보수적으로 처리하며 glob을 받지 않는다.
관측용 Read/Glob/Grep/LS는 쓰기 선점·호출 기록·상태 변경 없이 허용한다. 일관성 있는 읽기 snapshot을 뜻하지 않는다.
shell/MCP/불명 도구는 그 호출 동안 전체 폴더를 선점한다(`claims.call`=tool_use_id, schema 2). Post 또는
끝난 에이전트의 Stop·SubagentStop·UserPromptSubmit 정리에서 해제한다(`operations.agent`=호스트 agent_id, 주 에이전트는 ''). 파일 도구는 실제 대상 경로를 검사하고 유지 선점(`call`='')을 얻는다.
schema 1 registry는 처음 열 때 유지 선점으로 자동 이관한다.

작업 공간은 초기화한 실제 root의 파일시스템 식별자로 구분한다. 하위 cwd는 가장 가까운 marker의
registry를 사용한다. root alias는 resolve하며 내부 reparse path와 hardlink 파일은 거절한다.
같은 branch에서 HEAD가 기록된 HEAD의 자손이면(`git merge-base --is-ancestor`) baseline을 갱신하고
`baseline-advanced` event를 남긴다. branch 전환·비조상 HEAD는 새 쓰기를 거절하며 `rebaseline --quiescent`가
같은 세션 ID의 baseline을 바꾸고 version을 올린다. 이 검사는 외부 checkout의 예방 경계가 아니다.
같은 컴퓨터의 로컬 디스크가 대상이다. NFS/SMB/동기화 디렉터리와 worktree 간 공용 자원 registry는 지원하지 않는다.

`.hglos/coordination.json`은 root 식별자와 mode만 담는 로컬 설정이다. `.hglos/runtime/`은
SQLite·스냅샷을 담으며 둘 다 Git ignore 대상이다. `.hglos/COORDINATION.md`는 공유 규약이다.
진행률/승인/PASS를 DB로 옮기지 않는다. 기록의 `working`은 도구 활동/선점 상태이지 구현 완료 상태가 아니다.

release는 version 검사와 미종료 operation 검사를 수행하고 version을 증가시킨다. recovered ID는
재등록/heartbeat/쓰기가 거절된다. heartbeat 만료는 suspect 표시뿐이며 회수 조건이 아니다.
SessionEnd도 선점을 유지한다. 정상 release/복구의 `--quiescent`는 참여자의 정지 확인 진술이다.
분리된 background child의 생존을 자동 증명하지 않는다. DB/프로그램/OS/사용자까지 모두 신뢰하는
협조형 중재이며 보안 sandbox 또는 실제 파일 write broker가 아니다.

hook 제어 CLI 우회는 설치한 Python + 해당 coordination.py의 단독 호출만 허용한다.
repo·session을 일치시키고 shell chaining/파이프/변수 확장/다른 실행 파일은 허용하지 않는다.
자기 세션의 `rebaseline`은 허용한다. 운영 복구(recover)·설치·임의 출력 파일 쓰기는 허용하지 않는다.
호스트 권한 결정은 바꾸지 않는다. hook 맥락은 유지 선점·참여 세션 변화의 지문이 바뀔 때만 1,000자 이하로 넣는다.
session_id가 공유되는 하위 에이전트는 동일 소유자로 취급되므로 독립 쓰기 격리를 주장하지 않는다.

## 호스트 검증

adapter JSON 테스트는 실제 호스트가 hook를 활성화하고 도구를 거절했다는 증거가 아니다.
설치 후 각각 SessionStart context, 겹치는 파일 편집 거절, 비겹침 편집, shell 충돌,
hook 프로세스 오류/timeout, 정상 도구 완료, 종료 후 잔여 claim을 실제 호스트에서 확인한다.
Codex의 일부 tool path와 write_stdin은 hook 경계가 아니며 callback 실패가 도구를 통과시킬 수 있다.
오류를 처리할 수 있는 adapter는 명시적 deny JSON을 반환하지만 프로세스 시작 실패는 막을 수 없다.
Claude의 `onFailure: block` 지원과 실제 동작도 설치 버전에서 확인한다.

공식 출처(2026-10-22 조회):

- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
- [SQLite network filesystem cautions](https://www.sqlite.org/useovernet.html)
- [Git worktree](https://git-scm.com/docs/git-worktree)
