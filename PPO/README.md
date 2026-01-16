# PPO 핸드북

이 폴더는 학습(train)과 평가(eval)를 담당합니다.

---

## 1) 구조
- `train/`: 학습 엔트리
- `eval/`: 평가 엔트리
- `models/`: 네트워크 정의
- `common/`: 공통 유틸

---

## 2) 실행

학습
```bash
python main.py train --config config.yaml --yes
```

평가
```bash
python main.py eval --config config.yaml --yes
```

---

## 3) 결과
- 모델: `PPO/result/models/...`
- 로그: `PPO/result/log/...`

