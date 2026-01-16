# 스케줄링 경로

이 문서는 **휴리스틱/실적재현/조립착수일 경로**를 설명합니다.

---

## 1) 경로 구분

| 경로 | 설명 |
|---|---|
| scheduling/assembly_start | 조립착수일 기반 (RL 포함) |
| scheduling/start_date | 착수일 기반 휴리스틱 |
| scheduling/performance_replay | 엑셀 실적 재현 |

---

## 2) 흐름

### 조립착수일 (RL 포함)
```
rl_assembly_scheduler.py
  └─ env + masking → schedule
```

### 착수일 휴리스틱
```
action_sequence_착수일기준휴리스틱.py
  └─ 착수일 기준 정렬
```

### 실적 재현
```
excel_실적데이터_순번기반시퀀싱.py
  └─ 엑셀 순번 재현
```

---

## 3) 출력 파일

- 공통 결과 CSV: `*_evaluation_results.csv`
- 상세 CSV: `detailed_assembly_*`

---

## 4) 실행 시 주의점

- 조립착수일 경로는 마스킹/완화를 적극 사용
- 착수일 경로는 실적 재현 성격이 강함
- 실적 재현은 입력 엑셀 순서를 유지

