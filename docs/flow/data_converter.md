# 데이터 변환 흐름

이 문서는 **엑셀 → 블록 변환 흐름**을 설명합니다.

---

## 1) 핵심 경로

```
config.yaml → data.excel_path
  └─ enhanced_environment/common/data_converter.py
       ├─ excel_to_blocks
       └─ excel_to_blocks_with_metadata
```

---

## 2) 입력/출력

| 입력 | 출력 |
|---|---|
| 엑셀 파일 | EnhancedBlock 리스트 |
| 시트명 | 메타데이터 |

---

## 3) 시트 선택 규칙

- `config.yaml`에 시트명이 지정되면 그 값을 우선 사용
- 없으면 자동 후보(기본 시트명)에서 선택

---

## 4) 자주 발생하는 문제

- 경로가 `PPO/environment`로 잘못 잡힘
- 시트명 불일치
- 컬럼 누락

---

## 5) 실무 체크

- 상대 경로 사용 권장
- 시트명이 실제 엑셀과 일치하는지 확인

