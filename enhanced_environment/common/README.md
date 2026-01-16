# enhanced_environment/common 핸드북

이 폴더는 엑셀 변환, 시간 처리, 위반 집계 등 **공통 유틸**이 모여 있습니다.

---

## 1) 핵심 역할
- 엑셀 입력을 EnhancedBlock으로 변환
- 날짜/시간 계산 함수 제공
- 위반 중복 제거 및 집계

---

## 2) 주요 파일과 사용법

### data_converter.py
- `excel_to_blocks(excel_path, sheet_name)`
- `excel_to_blocks_with_metadata(excel_path, sheet_name)`

예시
```python
blocks = DataConverter.excel_to_blocks(
    "environment/판넬 블록 데이터셋_250618_SNU.xlsx",
    "Sheet1"
)
```

### time_utils.py
- 날짜 계산, 범위 생성
- 캘린더 처리에 사용

### violation_utils.py
- 위반 중복 제거
- severity 기준 필터링

---

## 3) 트러블슈팅

- 엑셀 파일이 안 열림
  - `config.yaml` 경로 확인
  - 시트명이 정확한지 확인

- 변환 중 에러
  - 입력 엑셀 컬럼이 누락되었는지 확인

