# enhanced_environment/constraints/managers 핸드북

제약 상태를 누적 관리하는 매니저 모음입니다.

---

## 1) 파일별 역할
- `capacity_manager.py`  
  일일 심수/블록 수 용량 관리
- `calendar_manager.py`  
  휴무/반일/점심/혹서기 캘린더
- `ps_manager.py`  
  P/S 쌍 관리
- `bay_manager.py`  
  베이 패턴/연속 제한 상태

---

## 2) 사용 위치
- `masking/core.py`에서 제약 체크
- `pbs_env/step_logic.py`에서 상태 업데이트

