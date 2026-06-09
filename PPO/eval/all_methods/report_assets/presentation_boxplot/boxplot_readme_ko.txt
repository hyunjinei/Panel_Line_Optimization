# [AGENT-ADD] presentation_boxplot CSV 설명

이 폴더는 Number of Blocks 기준 박스플롯을 그리기 위한 발표용 CSV 폴더입니다.
mask off 결과는 제외되어 있으며, 각 값은 best 결과 기준입니다.
이상치 값은 CSV에서 삭제하지 않았습니다. 박스플롯에서 점 표시 여부는 그래프 옵션으로 조절합니다.

방법론별 wide CSV:
- makespan_boxplot_SPT.csv
- makespan_boxplot_MSF.csv
- makespan_boxplot_LPT.csv
- makespan_boxplot_GA.csv
- makespan_boxplot_Proposed.csv
- violation_boxplot_SPT.csv
- violation_boxplot_MSF.csv
- violation_boxplot_LPT.csv
- violation_boxplot_GA.csv
- violation_boxplot_Proposed.csv

각 방법론별 wide CSV 구조:
- 행: Distribution Group 6개
- 열: 20, 30, 40, ..., 200 블록
- 각 셀: 해당 분포 그룹과 블록 수에서의 makespan 또는 violation 값

분포 그룹 이름:
- General
- Paired Block
- Subassembly
- Mixed Relation
- Heavy Workload
- High Utilization

전체 long-format CSV:
- makespan_boxplot_all_methods_long.csv
- violation_boxplot_all_methods_long.csv

long-format CSV는 Python, R, Origin, Tableau 같은 도구에서 한 번에 다시 그릴 때 사용합니다.
