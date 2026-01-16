# PPO/train 핸드북

학습 엔트리 포인트와 롤아웃 로직입니다.

## 핵심 파일
- `runner.py`: 학습 진입점
- `assembly_rollout.py`: PPO/self_label 루프
- `data_utils.py`: 학습 데이터 준비

## 설정 연동
- config.yaml의 train 섹션 → runner.py에서 적용

---

## Line-by-line 해설

- `docs/line_by_line/PPO.train.runner.py.md`
