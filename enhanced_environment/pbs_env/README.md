# enhanced_environment/pbs_env 핸드북

환경 코어 구현입니다.

## 핵심 파일
- `core.py`: EnhancedPanelBlockShop (reset/step)
- `step_logic.py`: step 처리 로직
- `observation.py`: 관측/피처 구성

## 실무 포인트
- step API는 유지되어야 함 (RL 안정성)
- 휴리스틱도 env 상태를 통해 검사 가능

