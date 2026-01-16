# enhanced_environment 핸드북 (상세)

PBS 환경 구현 전체를 담는 최상위 모듈입니다. 환경, 마스킹, 제약, 베이 로직이 이 아래에 있습니다.

---

## 흐름(요약)
```
엑셀/생성 데이터
  └─ DataConverter
       └─ EnhancedPanelBlockShop.reset
            └─ 마스킹/베이 배정
                 └─ 상태/관측/보상 반환
```

## 핵심 개념
- 환경은 reset/step 인터페이스를 유지합니다.
- 마스킹은 제약 후보를 줄이고 완화 순서를 적용합니다.
- 베이 배정과 makespan 계산은 별도 모듈로 분리됩니다.

## 파일별 상세
이 폴더에는 파이썬 파일이 없습니다.

## 주요 클래스/함수 (요약)
(자동 표 참조)

## 운영 팁
- 환경과 마스킹 설정은 config.yaml과 runtime_config.py 모두 확인합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

<!-- /AUTO-GENERATED -->
