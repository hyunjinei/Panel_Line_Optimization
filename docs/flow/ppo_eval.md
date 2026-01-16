# PPO 평가 흐름

이 문서는 **포괄 평가 실행 흐름**을 설명합니다.

---

## 1) 실행 흐름

```
main.py → PPO/eval/runner.py
  ├─ 데이터 준비
  ├─ 휴리스틱 평가 (SPT/LPT/SEAM)
  └─ RL 평가 (샘플링)
       └─ best 갱신 시 상세 CSV 재생성
```

---

## 2) 평가 모드 (MODE 1 / MODE 2)

- MODE 1: 생성 데이터 기반 비교
- MODE 2: 엑셀 기반 비교

평가 모드 선택은 `config.yaml`의 `evaluation.mode`에서 설정됩니다.

---

## 3) RL 평가 세부

- 모델 로드
- 샘플링 N회 수행
- 가장 좋은 makespan 선택
- best 갱신 시
  - 기존 상세 파일 삭제
  - 상세 CSV 재생성

---

## 4) 출력 파일

- `*_evaluation_results.csv`
- `rl_best_results.csv`
- `detailed_assembly_*`

---

## 5) 자주 발생하는 이슈

- 모델 로드 실패 → feature_mode/embedding_dim 불일치
- 상세 CSV 누락 → best 갱신 시점 확인
- LPT/엑셀 미포함 → eval.methods 설정 확인

