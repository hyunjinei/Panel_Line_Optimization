# enhanced_environment 핸드북

이 폴더는 **PBS 환경의 핵심 로직**(제약, 상태, 마스킹, 베이 배정)을 담고 있습니다.  
강화학습/휴리스틱/실적재현 모두 동일한 환경 규칙을 사용합니다.

---

## 1) 언제 쓰는가

- RL 학습과 평가에서 **제약 판정**과 **환경 상태 업데이트**가 필요할 때
- 휴리스틱 스케줄링에서 **액션 마스킹**을 적용할 때
- 실적 재현에서 **동일한 제약 집계**를 재사용할 때

---

## 2) 핵심 구조 요약

```
common/        공통 유틸(엑셀 변환, 시간 처리, 위반 집계)
constraints/   제약 설정 + 상태 매니저
masking/       액션 마스킹(후보 필터링, 완화 순서)
pbs_env/       환경 코어(reset/step/관측/보상)
bay/           베이 배정, 검증, 메이크스팬
models/        데이터 구조(블록, 상태, 위반 등)
```

---

## 3) 실행 흐름 (핵심)

1. `main.py` → `runtime_config.py` 설정 반영
2. PPO/eval 또는 scheduling 경로에서 환경 생성
3. `pbs_env/core.py`가 환경 상태 관리
4. `masking/core.py`가 후보 필터링/완화
5. `bay/assigner`가 베이 배정
6. `bay/validator`가 사후 검증

---

## 4) 입력/출력

### 입력
- 엑셀 데이터: `config.yaml`의 `data.excel_path`
- 제약 설정: `config.yaml`의 `constraints` 섹션

### 출력
- 스케줄 결과 CSV
- 위반 집계 및 로그

---

## 5) 설정 포인트 (실무에서 꼭 보는 것)

- `constraints.relax_order_start_date`
- `constraints.relax_order_assembly`
- `constraints.strict_rules`
- `calendar.*` (휴무, 점심, 반일)

이 값들은 **runtime_config**를 통해 동적으로 읽힙니다.

---

## 6) 트러블슈팅

- `NameError` 유형 → 분할 과정에서 import 누락 가능
- 제약 완화가 반영되지 않음 → `runtime_config` 적용 여부 확인
- 액션 후보가 0 → `masking/core.py` 완화 순서 확인

---

## 7) 파일별 상세 설명 (핵심만)

### common/
- `data_converter.py`  
  엑셀 데이터를 EnhancedBlock 리스트로 변환
- `time_utils.py`  
  날짜/시간 계산 유틸
- `violation_utils.py`  
  위반 중복 제거 및 집계

### constraints/
- `config.py`  
  ConstraintConfig 정의
- `presets.py`  
  기본 제약 프리셋
- `managers/*`  
  제약 누적 상태 관리

### masking/
- `core.py`  
  ConstraintChecker 핵심 로직
- `capacity.py`  
  심수/블록 용량 제한
- `routing.py`  
  C-Seam, 곡판, 고심수, 작업장 순서

### pbs_env/
- `core.py`  
  EnhancedPanelBlockShop 환경
- `step_logic.py`  
  step 처리 로직

### bay/
- `assigner/core.py`  
  베이 배정
- `validator/*`  
  사후 검증
- `makespan/core.py`  
  메이크스팬 계산

### models/
- `block.py`  
  EnhancedBlock
- `state.py`  
  EnvironmentState
- `violation.py`  
  ConstraintViolation

