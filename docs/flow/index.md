# 코드 흐름/모듈 해설 인덱스

이 문서는 **라인 단위가 아닌 모듈 단위**로 흐름과 책임을 설명합니다.  
현업 사용자는 실행 흐름과 설정을 빠르게 확인할 수 있고, 개발자는 모듈 간 연결을 이해할 수 있습니다.

---

## 빠른 길 안내

- 실행 흐름이 궁금하면 → `docs/flow/overview.md`
- 제약/마스킹 흐름을 보려면 → `docs/flow/masking.md`
- 학습 동작을 보려면 → `docs/flow/ppo_train.md`
- 평가 동작을 보려면 → `docs/flow/ppo_eval.md`
- 스케줄링 경로 차이를 보려면 → `docs/flow/scheduling.md`
- 엑셀 변환 오류가 나면 → `docs/flow/data_converter.md`

---

## 문서 목록

1) 전체 실행 흐름
- `docs/flow/overview.md`

2) 환경/제약/마스킹
- `docs/flow/environment.md`
- `docs/flow/masking.md`

3) PPO 학습/평가
- `docs/flow/ppo_train.md`
- `docs/flow/ppo_eval.md`

4) 스케줄링 경로
- `docs/flow/scheduling.md`

5) 데이터/변환
- `docs/flow/data_converter.md`

---

## 문서 업데이트 기준

- 코드 구조가 바뀌면 해당 문서를 먼저 갱신합니다.
- 실행 로직 변경은 `overview.md`와 `ppo_*` 문서를 우선 갱신합니다.
- 제약/완화가 바뀌면 `masking.md`와 `environment.md`를 갱신합니다.

