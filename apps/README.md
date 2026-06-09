# Apps

## Streamlit GUI

채팅형 PBS 재스케줄링 프로토타입:

```bash
source /home/hyunjin/accord_env/bin/activate
cd /mnt/c/users/user/desktop/d/지금휴리스틱과rl에서조립착수일먼저하는거제거한상태베스트인데
streamlit run apps/streamlit_scheduler_chat.py
```

기능:
- 왼쪽 sidebar에서 runtime 제약 토글
- LPT / SPT / RL 동시 비교
- 채팅 입력으로 위치 고정 / 선후행 / 베이 지정 / 일일 상한 요청
- freeze prefix 모드 지원
- final audit 기반 설명 / trace / 권고안 표시

주의:
- RL은 모델 경로가 필요하다.
- scheduler와 final audit는 기존 코드를 그대로 사용한다.
- GUI는 interactive wrapper일 뿐, core scheduler를 대체하지 않는다.


교수님 데모용 빠른 실행:

```bash
bash apps/run_professor_demo.sh
```

발표 슬라이드용 LLM 재스케줄링 데모:

```bash
bash apps/run_llm_presentation_demo.sh
```

구성:
- 자연어 긴급 요청
- LLM 구조화 제약
- before/after 현재상태 prefix 비교
- request satisfaction / current-state consistency / final audit 상태
- makespan, primary violation, raw event 변화
- audit-grounded explanation

추가 UI 구성:
- ChatGPT 홈 화면처럼 중앙 시작 화면 제공
- 상세 실행 설정은 접힌 expander로 숨김
- 교수님 캡처용 `샘플 결과 바로 불러오기` 버튼 제공
- 35A/36B bay lane 블록 박스 가시화
- 빠른 데모 버튼: 긴급 4번째 투입, 선후행 변경, 베이 강제, 일일 상한, C/Seam 완화

현재 데모 동작:
- 앱 시작 시 LPT/RL 샘플 결과를 자동으로 불러옵니다.
- 화면 최상단에 각 시퀀스의 makespan, primary, raw, 앞쪽 시퀀스가 표시됩니다.
- `샘플 결과 바로 불러오기` 버튼은 화면을 다시 샘플 상태로 되돌릴 때 사용합니다.
