# enhanced_environment/masking 핸드북

액션 마스킹 핵심 로직을 담당합니다.

---

## 1) 전체 흐름

1. 후보 블록 분류 (emergency / urgent / normal)
2. 작업장 헤드 + 윈도우 필터
3. 기본 제약 검사
4. relax_order 순서대로 완화
5. 최후 선택 (최소 위반)

---

## 2) 핵심 파일

- `core.py`  
  ConstraintChecker 메인
- `capacity.py`  
  심수/블록 수 용량 제약
- `routing.py`  
  C-Seam, 곡판, 고심수, 작업장 순서
- `ps_mixing.py`  
  P/S 및 라인 혼합
- `saw_time.py`  
  P6 시간/오후 착수
- `workshop_window.py`  
  작업장 헤드 + 윈도우 후보 구성

---

## 3) 설정 연동

- `constraints.relax_order_start_date`
- `constraints.relax_order_assembly`
- `constraints.strict_rules`

→ runtime_config를 통해 동적으로 읽힘

---

## 4) 디버그

환경변수로 상세 로그 제어
- PBS_FORCE_DEBUG=1
- PBS_DEBUG_VERBOSE=1
- PBS_RL_STEP_LOG=1

---

## Line-by-line 해설

- `docs/line_by_line/enhanced_environment.masking.core.py.md`
