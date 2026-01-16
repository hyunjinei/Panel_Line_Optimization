# PPO 학습 흐름

이 문서는 **PPO/self_label 학습 흐름**과 업데이트 조건을 설명합니다.  
학습의 기준은 **makespan 최소화 + 위반 최소화**입니다.

---

## 1) 실행 흐름

```
main.py → PPO/train/runner.py
  └─ 학습 파라미터 로드
  └─ 데이터 생성/로드
  └─ AssemblyPPORollout 실행
       ├─ PPO 모드
       └─ self_label 모드
```

---

## 2) PPO 모드 상세

1. actor 시퀀스 생성
2. baseline (LPT) 시퀀스 생성
3. score 계산
4. advantage 계산
5. update 조건 확인

---

## 3) self_label 모드 상세

- K개 샘플 중 최고 시퀀스를 선택
- baseline과 비교하여 업데이트 여부 결정
- 결과는 학습 CSV에 기록됨

---

## 4) score / advantage

- score = makespan + (위반수 * 가중치) + (론지 부하 가중치)
- advantage = baseline_score - actor_score
- update 조건: actor_score <= baseline_score

---

## 5) 학습 파라미터 연결

| 설정 | 위치 | 설명 |
|---|---|---|
| optimizer | train.cli_args | Adam/AdamW/ranger_adabelief |
| learning rate | train.cli_args | 학습률 |
| entropy coeff | train.cli_args | 탐색 유지 |
| feature_mode | train.cli_args | 피처 모드 |
| use_env_state | train.cli_args | 환경 피처 사용 여부 |

---

## 6) 학습 로그

- `PPO/result/log/ppo/*_ppo_train.csv`
- 로그에는 score, advantage, 업데이트 여부가 기록됨

---

## 7) 자주 발생하는 학습 문제

1. 후보가 1개만 남음 → 학습 신호 약함
2. strict_rules가 너무 강함 → 업데이트 불가
3. logit 분포가 너무 결정적 → entropy 감소

