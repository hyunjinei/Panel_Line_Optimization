# 액션 마스킹 흐름

이 문서는 **후보 필터링과 완화 흐름**을 단계별로 설명합니다.  
가장 중요한 목표는 **제약을 지키는 후보를 먼저 고르고**, 없으면 **완화 단계로 확대**하는 것입니다.

---

## 1) 큰 흐름

```
remaining blocks
  └─ layer 분류 (emergency / urgent / normal)
        └─ workshop heads + window 필터
             └─ 기본 제약 검사
                  └─ relax_order 순서대로 완화
                       └─ 최후 선택(최소 위반)
```

---

## 2) 후보 레이어 의미

- emergency: 매우 급박하거나 강제 우선 처리 대상
- urgent: 긴급도 높은 후보
- normal: 일반 후보

레이어 기준은 코드에 정의된 규칙을 따르며,  
후보 레이어가 달라지면 **작업장 헤드 목록**이 달라질 수 있습니다.

---

## 3) 작업장 헤드 + 윈도우

- 각 작업장 기준으로 **가장 빠른 착수 후보**를 헤드로 선택
- 현재 날짜 기준으로 window(3일,4일,5일...)를 확장
- window 안에 포함된 헤드만 1차 후보가 됨

예시 로그
```
[Masking] NORMAL: window=3 candidates=0
[Masking] NORMAL: window=4 candidates=0
[Masking] NORMAL: window=5 candidates=4
```

---

## 4) 완화 순서

### start_date 경로
- `constraints.relax_order_start_date` 사용

### assembly 경로
- `constraints.relax_order_assembly` 사용

### strict_rules
- `constraints.strict_rules`에 포함된 제약은 완화 불가

---

## 5) 완화 키 매핑

| 키 | 의미 |
|---|---|
| 라인고정간격 | LINE_GROUP_CONSTRAINT |
| 라인혼합 | P5#11, P5#12 |
| 용량 | P5#8, P5#9, P5#10, P5#16 |
| P6 | P6#1, P6#2, P6#3 |
| P6#4 | P6#4 |
| P5#15 | P5#15 |
| C-Seam | ROUTING_C_SEAM_SPACING |
| 곡판 | ROUTING_CURVED_SPACING |
| 고심수 | ROUTING_HIGH_SEAM_SPACING |
| 3Bay완화 | CONSECUTIVE_3BAY |

---

## 6) 최후 선택 단계

- 모든 제약/완화를 거쳐도 후보가 없으면
  `최소 위반 점수` 기준으로 선택
- 위반 점수는 `masking/scoring.py`에서 계산

---

## 7) 로그 해석 예시

```
[Masking] layer_counts: emergency=0 urgent=0 normal=56
[Masking] normal_workshop_heads=44
[Masking] NORMAL: window=3 candidates=6
[Masking] NORMAL: base_pass=6 stage=작업장헤드-3d-NON_P6
```

해석
- emergency/urgent 없음 → normal 레이어만 사용
- workshop head 후보가 44개
- 3일 window 안의 후보 6개
- 현재 단계는 기본 검사 통과(base_pass)

---

## 8) 디버그

환경변수로 상세 로그 제어
- PBS_FORCE_DEBUG=1
- PBS_DEBUG_VERBOSE=1
- PBS_RL_STEP_LOG=1

