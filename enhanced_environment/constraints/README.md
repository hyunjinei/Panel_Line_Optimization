# enhanced_environment/constraints 핸드북

제약 설정과 제약 상태 매니저를 정의합니다.

---

## 1) 핵심 파일

### config.py
- ConstraintConfig 정의
- 완화 허용 여부, 제약 파라미터 보관

### presets.py
- 기본 제약 프리셋 제공

---

## 2) managers/

- 상태 누적 관리
- 예: 일일 용량, P/S 쌍, 라인그룹, 캘린더

---

## 3) 실무 팁

- strict_rules에 넣은 제약은 완화 불가
- relax_order_start_date / relax_order_assembly로 완화 순서 제어

