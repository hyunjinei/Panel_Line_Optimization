# PPO 핸드북 (상세)

PPO 학습과 평가 전체 파이프라인을 설명합니다. train/eval 폴더와 모델 구조의 연결을 정리합니다.

---

## 흐름(요약)
```
train
  └─ eval
       └─ 결과 요약
```

## 핵심 개념
- self_label과 ppo 모드는 runner에서 분기됩니다.
- feature_mode는 모델 입력 차원을 결정합니다.

## 파일별 상세
이 폴더에는 파이썬 파일이 없습니다.

## 주요 클래스/함수 (요약)
(자동 표 참조)

## 운영 팁
- 모델 경로와 feature_mode가 다르면 로드 에러가 납니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

<!-- /AUTO-GENERATED -->
