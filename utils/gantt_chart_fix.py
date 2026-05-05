#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
향상된 공정별 간트차트 생성기 - 더 명확한 시각화
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from datetime import datetime, timedelta
import numpy as np
import glob
import os
from collections import defaultdict
from typing import Any, Dict, Optional, Tuple

# 한글 폰트 설정
plt.rcParams['font.family'] = 'Malgun Gothic'  # Windows
plt.rcParams['axes.unicode_minus'] = False

# 🆕 결과 폴더 설정 (최상단으로 이동)
# 🆕 결과 폴더 설정 (최상단으로 이동)
# 상대경로 기본값: PPO/eval 하위 결과 폴더
RESULT_BASE_FOLDER = "PPO/eval"

# ==== [AGENT-ADD BEGIN: summary-only gantt-like chart] ====
# [AGENT-ADD] utils/gantt_chart_enhanced.py는 수정하지 않고, 발표/요약용 차트를 별도 경로로 생성하기 위해
#            gantt_chart_fix.py에 "요약 메트릭 기반(총 소요시간/위반건수)" 차트 기능을 추가합니다.
#            (RL은 실제 결과 CSV에서 읽고, 나머지는 사용자 지정 값으로 대체 가능)

# 요약값(사용자 제공) 기본값: 필요 시 실행 전에 숫자만 바꿔서 사용 가능
# - RL_모델은 기본적으로 CSV에서 자동 계산되며, 여기 값을 직접 넣으면 그 값을 우선 적용할 수 있음
# - None이면 "CSV 자동 계산값"을 유지 (요청: RL도 설정 가능 + None이면 CSV 읽기)
DEFAULT_MANUAL_METRICS: Dict[str, Dict[str, Optional[float]]] = {
    # key는 create_comparison_chart() / 기존 코드와 동일하게 유지
    "실적데이터": {"makespan_hours": 104.8, "violations": 57},
    "실적데이터_착수일_최적화": {"makespan_hours": 106.2, "violations": 20},
    "SPT": {"makespan_hours": 96.2, "violations": 16},
    "LPT": {"makespan_hours": 92.4, "violations": 6},
    "Seam_min": {"makespan_hours": 93.9, "violations": 8},
    # [AGENT-EDIT] RL 값도 수동으로 설정 가능(필요 시). None이면 CSV 자동 계산값 사용.
    # "RL_모델": {"makespan_hours": None, "violations": None},
    "RL": {"makespan_hours": 89.3, "violations": 6},
}


def _safe_read_csv(csv_path: str) -> Optional[pd.DataFrame]:
    """CSV를 안전하게 로드(utf-8-sig 우선)"""
    if not csv_path or not os.path.exists(csv_path):
        return None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return pd.read_csv(csv_path, encoding=enc)
        except Exception:
            continue
    return None


def _extract_makespan_hours_and_violations_from_results(results_csv_path: str) -> Optional[Tuple[float, int]]:
    """
    rl_best_results.csv 같은 *_results.csv에서
    - makespan_hours (float)
    - violations (int; ERROR/WARNING 집계값이 columns에 있으면 사용)
    을 추출한다.
    """
    df = _safe_read_csv(results_csv_path)
    if df is None or df.empty:
        return None

    makespan_hours: Optional[float] = None
    if "makespan_hours" in df.columns:
        makespan_hours = float(pd.to_numeric(df["makespan_hours"], errors="coerce").max())
    elif "makespan_minutes" in df.columns:
        makespan_hours = float(pd.to_numeric(df["makespan_minutes"], errors="coerce").max()) / 60.0

    # violations 컬럼이 있으면 그 합을 우선 사용 (프로젝트에서 ERROR/WARNING만 카운트하도록 통일한 상태)
    violations_count = 0
    if "violations" in df.columns:
        violations_count = int(pd.to_numeric(df["violations"], errors="coerce").fillna(0).sum())
    elif "violation_severity" in df.columns:
        # 문자열 리스트인 경우도 있으니 파싱해서 ERROR/WARNING만 카운트
        import ast

        for _, row in df.iterrows():
            severities = row.get("violation_severity", [])
            if isinstance(severities, str):
                try:
                    severities = ast.literal_eval(severities)
                except Exception:
                    severities = []
            for sev in severities or []:
                if sev in ("ERROR", "WARNING"):
                    violations_count += 1

    if makespan_hours is None or np.isnan(makespan_hours):
        return None
    return makespan_hours, violations_count


def _extract_total_duration_hours_from_process_csvs(result_folder: str, process_prefix: str) -> Optional[float]:
    """
    detailed_{prefix}_schedule_processes_YYYYMMDD.csv 들을 합쳐서
    전체 기간 start~end 기반 makespan(hours)을 계산한다.
    (evaluation 콘솔 출력과 동일한 방식)
    """
    if not result_folder or not process_prefix:
        return None

    pattern = os.path.join(result_folder, f"detailed_{process_prefix}_schedule_processes_*.csv")
    paths = sorted(glob.glob(pattern))
    if not paths:
        return None

    frames = []
    for p in paths:
        df = _safe_read_csv(p)
        if df is None or df.empty:
            continue
        # 프로젝트 내 process CSV는 start_datetime/end_datetime 컬럼이 표준
        for col in ("start_datetime", "end_datetime", "start_time", "end_time"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        frames.append(df)

    if not frames:
        return None

    merged = pd.concat(frames, ignore_index=True)
    if "start_datetime" in merged.columns and "end_datetime" in merged.columns:
        start = merged["start_datetime"].min()
        end = merged["end_datetime"].max()
    elif "start_time" in merged.columns and "end_time" in merged.columns:
        start = merged["start_time"].min()
        end = merged["end_time"].max()
    else:
        return None

    if pd.isna(start) or pd.isna(end):
        return None
    return float((end - start).total_seconds() / 3600.0)


def create_summary_gantt_like_chart(
    result_folder: str,
    manual_metrics: Optional[Dict[str, Dict[str, float]]] = None,
    rl_results_csv: str = "rl_best_results.csv",
    save_name: str = "요약_메트릭_간트차트.png",
    prefer_manual_metrics: bool = False,
) -> Optional[str]:
    """
    발표용 요약 차트:
    - 방법별 총 소요시간(가로 막대; 간트처럼 0에서 시작)
    - 막대 끝에 위반 건수 함께 표기

    RL은 result_folder/rl_best_results.csv에서 자동 읽기.
    나머지는 manual_metrics(또는 DEFAULT_MANUAL_METRICS)를 사용.
    """
    if not result_folder:
        return None
    os.makedirs(result_folder, exist_ok=True)

    # ---- [AGENT-EDIT] makespan 계산 기준 통일 ----
    # *_evaluation_results.csv의 makespan_hours는 "일자별(08:00~)" 값이라,
    # 콘솔/평가에서 쓰는 전체 makespan(예: 89.3h)을 재현하려면
    # detailed_*_schedule_processes_YYYYMMDD.csv의 start/end로 계산해야 함.
    # 수동 메트릭(발표용) 병합: 기본값 + JSON 입력값
    # - prefer_manual_metrics=False(기본): 자동 추출이 우선, 자동 추출이 실패한 경우에만 수동값을 fallback으로 사용
    # - prefer_manual_metrics=True: 수동값이 자동값을 덮어쓴다 (요청: RL 제외하고 수동값 사용 + RL은 CSV 기준)
    merged_manual_metrics: Dict[str, Dict[str, Optional[float]]] = {}
    for k, v in DEFAULT_MANUAL_METRICS.items():
        merged_manual_metrics[k] = dict(v)
    if manual_metrics:
        for k, v in manual_metrics.items():
            merged_manual_metrics[k] = dict(v)

    # 방법별 파일 매핑 (process_prefix / 결과 CSV)
    method_sources: Dict[str, Dict[str, str]] = {
        "실적데이터": {"process_prefix": "excel", "result_csv": "excel_evaluation_results.csv"},
        "실적데이터_착수일_최적화": {"process_prefix": "actionmasking", "result_csv": "actionmasking_evaluation_results.csv"},
        "SPT": {"process_prefix": "spt", "result_csv": "spt_evaluation_results.csv"},
        "LPT": {"process_prefix": "lpt", "result_csv": "lpt_evaluation_results.csv"},
        "Seam_min": {"process_prefix": "seam_min", "result_csv": "seam_min_evaluation_results.csv"},
        "RL": {"process_prefix": "rl", "result_csv": rl_results_csv},
    }

    # 자동 추출(가능한 것만)
    metrics: Dict[str, Dict[str, float]] = {}
    for method_name, src in method_sources.items():
        # 1) makespan: process CSV 기반
        process_prefix = src.get("process_prefix", "")
        dur = _extract_total_duration_hours_from_process_csvs(result_folder, process_prefix)
        if dur is not None:
            metrics.setdefault(method_name, {})
            metrics[method_name]["makespan_hours"] = float(dur)

        # 2) violations: 결과 CSV 기반 (sum(violations))
        res_csv = src.get("result_csv", "")
        res_path = os.path.join(result_folder, res_csv) if res_csv else ""
        df_res = _safe_read_csv(res_path) if res_path else None
        if df_res is not None and not df_res.empty:
            if "violations" in df_res.columns:
                v_sum = int(pd.to_numeric(df_res["violations"], errors="coerce").fillna(0).sum())
                metrics.setdefault(method_name, {})
                metrics[method_name]["violations"] = float(v_sum)
            else:
                # fallback (ERROR/WARNING 파싱)
                parsed = _extract_makespan_hours_and_violations_from_results(res_path)
                if parsed is not None:
                    _, v_cnt = parsed
                    metrics.setdefault(method_name, {})
                    metrics[method_name]["violations"] = float(v_cnt)

    # 수동값 병합
    if prefer_manual_metrics:
        # 수동값이 자동값을 덮어쓴다 (요청: RL 제외 수동/ RL은 CSV)
        for method_name, vals in merged_manual_metrics.items():
            metrics.setdefault(method_name, {})
            for k, v in vals.items():
                # [AGENT-EDIT] None이면 자동값 유지 (CSV 자동 계산)
                if v is None:
                    continue
                metrics[method_name][k] = float(v)
    else:
        # 자동값이 우선, 없는 경우만 수동값 사용
        for method_name, vals in merged_manual_metrics.items():
            metrics.setdefault(method_name, {})
            for k, v in vals.items():
                if v is None:
                    continue
                metrics[method_name].setdefault(k, float(v))

    # 표시 순서(요청 순서)
    method_order = [
        "실적데이터",
        "실적데이터_착수일_최적화",
        "SPT",
        "LPT",
        "Seam_min",
        "RL",
    ]
    methods = [m for m in method_order if m in metrics]
    if not methods:
        print("❌ 요약 차트 생성 실패: 표시할 방법론이 없습니다.")
        return None

    # 색상은 기존 비교차트와 동일하게 유지
    method_colors = {
        "실적데이터": "#8E8E93",
        "실적데이터_착수일_최적화": "#34C759",
        "SPT": "#007AFF",
        "LPT": "#AF52DE",
        "Seam_min": "#FF9500",
        "RL": "#FF3B30",
    }
    colors = [method_colors.get(m, "#999999") for m in methods]

    makespans = [float(metrics[m]["makespan_hours"]) for m in methods]
    violations = [int(float(metrics[m].get("violations", 0))) for m in methods]

    # (요청) PPT 예시처럼 "세로 막대"로 표현 (통합 1장: 시간+위반 동시 표기)
    # [AGENT-EDIT] 크기 비율을 기존 create_comparison_chart()의 1_총소요시간비교.png(fig=(12,8))와 동일하게 맞춤
    fig, ax = plt.subplots(figsize=(12, 8))
    x = np.arange(len(methods))
    bars = ax.bar(x, makespans, color=colors, alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=0, fontsize=11)
    ax.set_ylabel("Makespan(h)", fontsize=12)
    ax.set_title("총 소요 시간 비교", fontsize=16, fontweight="bold")
    ax.grid(True, axis="y", alpha=0.3, linestyle=":")

    # 막대 위에 텍스트: "xx.xh"
    for bar, ms in zip(bars, makespans):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(0.8, ms * 0.01),
            f"{ms:.1f}h",
            va="bottom",
            ha="center",
            fontsize=11,
            fontweight="bold",
        )

    # 보조 텍스트(위반 건수)는 축 내부 하단에 작게 표기 (너무 복잡하면 별도 차트를 사용)
    for idx, v in enumerate(violations):
        ax.text(
            idx,
            max(makespans) * 0.02,
            f"{v}건",
            va="bottom",
            ha="center",
            fontsize=9,
            color="#333333",
        )

    save_path = os.path.join(result_folder, save_name)
    fig.savefig(save_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"📊 요약 메트릭 간트차트 저장: {save_path}")

    # [AGENT-ADD] 요청 사항: 총 소요 시간/위반 건수 각각 단독 차트도 함께 생성
    # 1) 총 소요 시간만 (세로 막대)
    # [AGENT-EDIT] 1_총소요시간비교.png와 동일 비율
    fig_t, ax_t = plt.subplots(figsize=(12, 8))
    bars_t = ax_t.bar(x, makespans, color=colors, alpha=0.85)
    ax_t.set_xticks(x)
    ax_t.set_xticklabels(methods, rotation=0, fontsize=11)
    ax_t.set_ylabel("Makespan(h)", fontsize=12)
    ax_t.set_title("총 소요 시간 비교", fontsize=16, fontweight="bold")
    ax_t.grid(True, axis="y", alpha=0.3, linestyle=":")
    for bar, ms in zip(bars_t, makespans):
        ax_t.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(0.8, ms * 0.01),
            f"{ms:.1f}h",
            va="bottom",
            ha="center",
            fontsize=11,
            fontweight="bold",
        )
    save_path_t = os.path.join(result_folder, "요약_총소요시간.png")
    fig_t.savefig(save_path_t, dpi=300, bbox_inches="tight")
    plt.close(fig_t)
    print(f"📊 요약 총 소요 시간 차트 저장: {save_path_t}")

    # 2) 제약조건 위반 건수만 (세로 막대)
    # [AGENT-EDIT] 1_총소요시간비교.png와 동일 비율
    fig_v, ax_v = plt.subplots(figsize=(12, 8))
    bars_v = ax_v.bar(x, violations, color=colors, alpha=0.85)
    ax_v.set_xticks(x)
    ax_v.set_xticklabels(methods, rotation=0, fontsize=11)
    ax_v.set_ylabel("Violations", fontsize=12)
    ax_v.set_title("제약조건 위반 건수 비교", fontsize=16, fontweight="bold")
    ax_v.grid(True, axis="y", alpha=0.3, linestyle=":")
    for bar, v in zip(bars_v, violations):
        ax_v.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(0.3, v * 0.02),
            f"{int(v)}건",
            va="bottom",
            ha="center",
            fontsize=11,
            fontweight="bold",
        )
    save_path_v = os.path.join(result_folder, "요약_제약위반건수.png")
    fig_v.savefig(save_path_v, dpi=300, bbox_inches="tight")
    plt.close(fig_v)
    print(f"📊 요약 제약조건 위반 차트 저장: {save_path_v}")

    return save_path
# ==== [AGENT-ADD END] ====

# 🔧 동적으로 최신 결과 폴더 찾기 함수
def find_latest_result_folder():
    """최신 결과 폴더 자동 탐색 (PPO/eval 우선, 없으면 PPO)"""
    candidates = []
    for base_dir in ("PPO/eval", "PPO"):
        if not os.path.exists(base_dir):
            continue
        # YYYYMMDD_* 형태의 결과 폴더 수집
        pattern = os.path.join(base_dir, "????????_*")
        for folder in glob.glob(pattern):
            if os.path.isdir(folder):
                candidates.append(folder)

    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)

def create_gantt_folders(data_by_method=None, result_base_folder=None):
    """간트차트 저장을 위한 폴더 구조 생성"""
    # 🆕 결과 폴더 자동 감지 또는 사용자 지정 사용
    if result_base_folder is None:
        result_base_folder = find_latest_result_folder()
        if result_base_folder is None:
            result_base_folder = RESULT_BASE_FOLDER
    
    # 🆕 CSV에서 가장 빠른 날짜 추출
    earliest_date = None
    if data_by_method:
        all_dates = []
        for method_data in data_by_method.values():
            for date_str in method_data.keys():
                try:
                    # YYYYMMDD 형식의 날짜 파싱
                    date_obj = datetime.strptime(date_str, '%Y%m%d')
                    all_dates.append(date_obj)
                except:
                    pass
        
        if all_dates:
            earliest_date = min(all_dates)
    
    # 🆕 폴더명 생성 (gantt_charts_20250609_실행날짜_time 형식)
    current_time = datetime.now().strftime('%m%d_%H_%M')
    if earliest_date:
        earliest_date_str = earliest_date.strftime('%Y%m%d')
        gantt_folder_name = f"gantt_charts_{earliest_date_str}_{current_time}"
    else:
        gantt_folder_name = f"gantt_charts_{current_time}"
    
    # 🔧 결과 폴더 하위에 간트차트 폴더 생성
    base_folder = os.path.join(result_base_folder, gantt_folder_name)
    
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)
        print(f"간트차트 폴더 생성: {base_folder}")
    
    return base_folder

def load_process_csv_files(pattern='detailed_*_schedule_processes_*.csv', search_dir=None):
    """공정별 상세 스케줄링 CSV 파일들을 로드"""
    # 🔧 결과 폴더에서 파일 검색 (동적으로 최신 폴더 찾기)
    if search_dir is None:
        search_dir = find_latest_result_folder()
        if search_dir is None:
            search_dir = RESULT_BASE_FOLDER
    
    if search_dir and os.path.exists(search_dir):
        pattern = os.path.join(search_dir, pattern)
    else:
        # Fallback: PPO 폴더에서 검색
        pattern = os.path.join('PPO', pattern)
    
    files = glob.glob(pattern)
    data_by_method = defaultdict(dict)
    
    for file in files:
        try:
            df = pd.read_csv(file, encoding='utf-8-sig')
            
            # 파일명에서 방식과 날짜 추출
            filename = os.path.basename(file)
            
            # PPO 폴더의 파일명 패턴에 맞게 수정
            if 'spt' in filename:
                method = 'SPT'
            elif 'random' in filename:
                method = 'Random'
            elif 'lpt' in filename:
                method = 'LPT'
            elif 'seam_min' in filename:
                method = 'Seam_min'                                
            elif 'rl' in filename:
                method = 'RL'
            elif 'actionmasking' in filename:
                method = '실적데이터_착수일_최적화'
            elif 'excel' in filename:
                method = '실적데이터'
            elif 'assembly_decoding' in filename:
                method = 'Assembly_Decoding'
            else:
                method = 'Unknown'
            
            # 날짜 추출 (YYYYMMDD)
            date_str = filename.split('_')[-1].replace('.csv', '')
            
            # 시간 컬럼을 datetime으로 변환
            df['start_datetime'] = pd.to_datetime(df['start_time'])
            df['end_datetime'] = pd.to_datetime(df['end_time'])
            
            data_by_method[method][date_str] = df
            print(f"로드: {filename} ({len(df)}개 공정)")
            
        except Exception as e:
            print(f"파일 로드 실패 {file}: {e}")
    
    return data_by_method

def create_machine_mapping():
    """기계 순서 매핑 생성"""
    machines = [
        # 공통 공정 (단일 라인)
        '판계',
        '전면SAW', 
        'TurnOver',
        '후면SAW',
        'NC',
        # 베이별 분기 공정
        '베이A 론지취부',
        '베이A 론지용접', 
        '베이A 수정',
        '베이B 론지취부',
        '베이B 론지용접',
        '베이B 수정'
    ]
    
    return {machine: i for i, machine in enumerate(machines)}

def assign_machine_line(df):
    """각 공정을 적절한 기계 라인에 할당"""
    df = df.copy()
    
    # 공통 공정은 단일 라인으로 처리 (순차적)
    common_processes = ['판계', '전면SAW', 'TurnOver', '후면SAW', 'NC']
    
    for process in common_processes:
        process_df = df[df['process_name'] == process].copy()
        if len(process_df) > 0:
            # 공통 공정은 모두 같은 이름으로 통일 (라인 분할 없음)
            df.loc[df['process_name'] == process, 'machine_line'] = process
    
    # 베이별 공정은 직접 매핑
    bay_mapping = {
        '베이35A 론지취부': '베이A 론지취부',
        '베이35A 론지용접': '베이A 론지용접', 
        '베이35A 수정': '베이A 수정',
        '베이36B 론지취부': '베이B 론지취부',
        '베이36B 론지용접': '베이B 론지용접',
        '베이36B 수정': '베이B 수정'
    }
    
    for original, mapped in bay_mapping.items():
        df.loc[df['process_name'] == original, 'machine_line'] = mapped
    
    return df

def get_enhanced_color_and_style(row):
    """
    향상된 색상 및 스타일 반환
    
    Returns:
        (color, hatch, edgecolor, alpha, linewidth)
    """
    block_id = row['block_id']
    assigned_bay = row.get('assigned_bay', '')
    process_name = row['process_name']
    
    # 블록별로 매우 다양한 색상 팔레트 (50개 색상)
    vibrant_colors = [
        '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FECA57', '#FF9FF3', '#54A0FF', 
        '#5F27CD', '#00D2D3', '#FF9F43', '#EE5A24', '#0984E3', '#6C5CE7', '#A29BFE',
        '#FD79A8', '#E17055', '#00B894', '#00A085', '#FDCB6E', '#E84393', '#74B9FF',
        '#81ECEC', '#A0E7E5', '#B2F5EA', '#C6F6D5', '#EDEBFF', '#F8BBD9', '#FFD93D',
        '#6BCF7F', '#4D96FF', '#9775FA', '#FFA8A8', '#74C0FC', '#8CE99A', '#FFD43B',
        '#DA77F2', '#51CF66', '#FF8787', '#69DB7C', '#339AF0', '#91A7FF', '#FFA94D',
        '#8C7CF0', '#FF6B9D', '#4DABF7', '#69DB7C', '#FFD43B', '#DA77F2', '#51CF66',
        '#FF8787', '#69DB7C', '#339AF0', '#91A7FF', '#FFA94D', '#8C7CF0', '#FF6B9D'
    ]
    
    # 블록 ID에 따른 색상 선택 (더 많은 색상 활용)
    color_idx = (block_id - 1) % len(vibrant_colors)
    color = vibrant_colors[color_idx]
    
    # P/S/C 타입에 따른 해칭 패턴
    block_name = row.get('block_name', '')
    if 'P' in block_name:
        hatch = '///'  # Port: 대각선
        edgecolor = 'darkred'
        linewidth = 2
    elif 'S' in block_name:
        hatch = '\\\\\\'  # Starboard: 역대각선
        edgecolor = 'darkblue'
        linewidth = 2
    elif 'C' in block_name:
        hatch = '++++'  # Center: 십자
        edgecolor = 'darkgreen'
        linewidth = 2
    else:
        hatch = None
        edgecolor = 'black'
        linewidth = 1
    
    # 공정별 투명도 조절
    if any(proc in process_name for proc in ['론지취부', '론지용접', '수정']):
        alpha = 0.9  # 베이 공정: 진하게
    else:
        alpha = 0.8  # 공통 공정: 약간 연하게
    
    return color, hatch, edgecolor, alpha, linewidth

def create_enhanced_gantt_chart(combined_df, method_name, chart_title, save_path=None):
    """향상된 간트차트 생성"""
    if combined_df.empty:
        print(f"{method_name}에 데이터가 없습니다.")
        return
    
    # 기계 매핑
    machine_mapping = create_machine_mapping()
    
    # 시간 범위 계산
    min_time = combined_df['start_datetime'].min()
    max_time = combined_df['end_datetime'].max()
    
    # 그래프 설정
    fig, ax = plt.subplots(figsize=(24, 14))
    
    # 각 공정을 간트바로 그리기
    for _, row in combined_df.iterrows():
        machine = row['machine_line']
        if machine not in machine_mapping:
            continue
            
        y_pos = machine_mapping[machine]
        start_time = row['start_datetime']
        end_time = row['end_datetime']
        
        # 향상된 스타일 적용
        color, hatch, edgecolor, alpha, linewidth = get_enhanced_color_and_style(row)
        
        # matplotlib dates를 위한 올바른 시간 계산
        start_num = mdates.date2num(start_time)
        end_num = mdates.date2num(end_time)
        width = end_num - start_num
        
        # 간트바 그리기
        ax.barh(y_pos, width, left=start_num, 
                height=0.7, color=color, alpha=alpha, 
                edgecolor=edgecolor, linewidth=linewidth, hatch=hatch)
        
        # 블록 정보 라벨 (조건부)
        duration_hours = (end_time - start_time).total_seconds() / 3600
        if width > 0.02:  # 충분한 폭이 있는 경우만
            block_name = row.get('block_name', f"BLK_{row['block_id']}")
            # "BLK_" 제거하고 간단히 표시
            label = block_name.replace('BLK_', '') if 'BLK_' in block_name else str(row['block_id'])
            
            ax.text(start_num + width/2, y_pos, 
                    label, ha='center', va='center', 
                    fontsize=8, fontweight='bold', color='white',
                    bbox=dict(boxstyle="round,pad=0.1", facecolor='black', alpha=0.7))
    
    # Y축 설정 (기계 이름)
    machine_names = list(machine_mapping.keys())
    ax.set_yticks(range(len(machine_names)))
    ax.set_yticklabels(machine_names, fontsize=10)
    ax.invert_yaxis()
    
    # X축 설정 (시간)
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))
    ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:%M'))
    
    # 아침 8시 시작선 (파란색 점선)
    dates = pd.date_range(min_time.date(), max_time.date() + timedelta(days=1), freq='D')
    for date in dates:
        start_8am = datetime.combine(date.date(), datetime.min.time().replace(hour=8))
        if start_8am >= min_time and start_8am <= max_time:
            ax.axvline(x=mdates.date2num(start_8am), color='blue', linestyle=':', 
                      alpha=0.8, linewidth=3)
            ax.text(mdates.date2num(start_8am), len(machine_names), 
                    '08:00 시작', rotation=90, ha='right', va='bottom', 
                    fontsize=9, color='blue', fontweight='bold',
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="lightblue", alpha=0.8))
    
    # 날짜 경계선 표시 (자정)
    for date in dates:
        ax.axvline(x=mdates.date2num(date), color='red', linestyle='--', alpha=0.8, linewidth=2)
        ax.text(mdates.date2num(date + timedelta(hours=12)), -0.5, 
                date.strftime('%Y-%m-%d'), rotation=0, ha='center', va='top', 
                fontsize=11, color='red', fontweight='bold',
                bbox=dict(boxstyle="round,pad=0.3", facecolor="yellow", alpha=0.8))
    
    # 공정 영역 구분 세로선
    # 공통 공정과 베이 공정 사이 구분선
    common_processes_count = 5  # 판계, 전면SAW, TurnOver, 후면SAW, NC
    
    # 공통 공정 영역 끝 (베이A 시작 전)
    ax.axhline(y=common_processes_count - 0.5, color='purple', linestyle='-', 
               alpha=0.7, linewidth=3)
    ax.text(mdates.date2num(min_time), common_processes_count - 0.3, 
            '공통 공정 영역', ha='left', va='bottom', 
            fontsize=12, color='purple', fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor="lavender", alpha=0.9))
    
    # 베이A와 베이B 사이 구분선
    bay_a_count = 3  # 베이A 론지취부, 론지용접, 수정
    bay_divider_pos = common_processes_count + bay_a_count - 0.5
    
    ax.axhline(y=bay_divider_pos, color='orange', linestyle='-', 
               alpha=0.7, linewidth=3)
    ax.text(mdates.date2num(min_time), bay_divider_pos - 0.3, 
            '베이A 영역', ha='left', va='bottom', 
            fontsize=12, color='darkorange', fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor="peachpuff", alpha=0.9))
    
    # 베이B 영역 라벨
    ax.text(mdates.date2num(min_time), len(machine_names) - 0.5, 
            '베이B 영역', ha='left', va='top', 
            fontsize=12, color='teal', fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor="lightcyan", alpha=0.9))
    
    # 그래프 스타일 설정
    ax.grid(True, alpha=0.3, linestyle=':')
    ax.set_xlabel('시간 (일/시)', fontsize=14, fontweight='bold')
    ax.set_ylabel('기계/공정', fontsize=14, fontweight='bold')
    ax.set_title(chart_title, fontsize=18, fontweight='bold')
    
    # X축 라벨 회전
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    # 향상된 범례 생성
    legend_elements = []
    
    # P/S/C 타입별 범례
    legend_elements.extend([
        mpatches.Patch(color='gray', hatch='///', label='Port (P) 블록'),
        mpatches.Patch(color='gray', hatch='\\\\\\', label='Starboard (S) 블록'),
        mpatches.Patch(color='gray', hatch='++++', label='Center (C) 블록')
    ])
    
    # 구분선 범례
    legend_elements.extend([
        mpatches.Patch(color='purple', label='공통 공정 영역'),
        mpatches.Patch(color='orange', label='베이A 영역'),
        mpatches.Patch(color='teal', label='베이B 영역'),
        mpatches.Patch(color='blue', label='08:00 시작 시간'),
        mpatches.Patch(color='red', label='일자 경계')
    ])
    
    ax.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(1.15, 1), 
              ncol=1, fontsize=10, title='블록 타입 & 영역 구분', title_fontsize=12)
    
    plt.tight_layout()
    
    # 저장
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"📊 간트차트 저장: {save_path}")
    
    # 자동 표시 제거 (사용자 요청)
    # plt.show()  # 제거됨
    plt.close()  # 메모리 절약을 위해 닫기

def calculate_method_stats(data_by_method):
    """방법론별 통계 계산"""
    stats = {}
    
    for method_name, dates_data in data_by_method.items():
        # 모든 날짜의 데이터 합치기
        all_data = []
        for date, df in dates_data.items():
            all_data.append(df)
        
        if not all_data:
            continue
            
        combined_df = pd.concat(all_data, ignore_index=True)
        
        # 통계 계산
        start_time = combined_df['start_datetime'].min()
        end_time = combined_df['end_datetime'].max()
        total_duration = (end_time - start_time).total_seconds() / 3600  # 시간 단위
        
        unique_blocks = combined_df['block_id'].unique()
        block_count = len(unique_blocks)
        
        # 베이별 통계
        bay_a_blocks = combined_df[combined_df['assigned_bay'] == '35A']['block_id'].unique()
        bay_b_blocks = combined_df[combined_df['assigned_bay'] == '36B']['block_id'].unique()

        # 베이별 론지 합계 (가능한 경우)
        bay_a_longi_total = 0
        bay_b_longi_total = 0
        total_longi_count = 0
        if 'longi_count' in combined_df.columns:
            longi_df = combined_df[['block_id', 'assigned_bay', 'longi_count']].dropna(subset=['longi_count']).copy()
            if not longi_df.empty:
                longi_df['assigned_bay'] = longi_df['assigned_bay'].astype(str).str.upper()
                longi_df = longi_df.sort_values(['block_id', 'assigned_bay']).drop_duplicates(subset='block_id', keep='last')
                longi_group = longi_df.groupby('assigned_bay')['longi_count'].sum()
                bay_a_longi_total = int(longi_group.get('35A', 0))
                bay_b_longi_total = int(longi_group.get('36B', 0))
                total_longi_count = int(longi_group.sum())

        # 제약조건 위반 정보 추출 (결과 CSV에서)
        violations_count = 0
        try:
            # 🔧 결과 폴더에서 CSV 파일 찾기
            result_base_folder = find_latest_result_folder()
            if result_base_folder is None:
                result_base_folder = RESULT_BASE_FOLDER
            
            # 해당 방법론의 결과 CSV 파일에서 위반 정보 추출
            result_files = []
            if method_name == 'SPT':
                result_files = ['spt_evaluation_results.csv']
            elif method_name == 'LPT':
                result_files = ['lpt_evaluation_results.csv']
            elif method_name == 'SIM_Min_Select_휴리스틱':
                result_files = ['seam_min_evaluation_results.csv']
            elif method_name == 'Random_휴리스틱':
                result_files = ['random_best_results.csv']
            elif method_name == 'RL_모델':
                result_files = ['rl_best_results.csv']
            elif method_name == 'Excel_순번':
                result_files = ['excel_evaluation_results.csv']
            elif method_name == 'Action_Masking':
                result_files = ['actionmasking_evaluation_results.csv']
            
            for result_file in result_files:
                # 결과 폴더에서 파일 찾기
                full_path = os.path.join(result_base_folder, result_file)
                if os.path.exists(full_path):
                    result_df = pd.read_csv(full_path, encoding='utf-8-sig')
                    
                    # violations 컬럼이 0이면 violation_details에서 실제 위반 계산
                    if 'longi_count' in result_df.columns and 'assigned_bay' in result_df.columns:
                        assigned_series = result_df['assigned_bay'].fillna('').astype(str).str.upper()
                        longi_series = pd.to_numeric(result_df['longi_count'], errors='coerce').fillna(0)
                        bay_a_mask = assigned_series.str.contains('35A', na=False) | assigned_series.eq('A') | assigned_series.str.contains('BAY_35A', na=False)
                        bay_b_mask = assigned_series.str.contains('36B', na=False) | assigned_series.eq('B') | assigned_series.str.contains('BAY_36B', na=False)
                        bay_a_longi_total = int(longi_series[bay_a_mask].sum())
                        bay_b_longi_total = int(longi_series[bay_b_mask].sum())
                        total_longi_count = int(longi_series.sum())

                    if 'violations' in result_df.columns:
                        violations_from_column = result_df['violations'].sum()
                        
                        # violations 컬럼이 0이면 violation_details에서 계산
                        if violations_from_column == 0 and 'violation_details' in result_df.columns:
                            for _, row in result_df.iterrows():
                                violation_details = row.get('violation_details', [])
                                violation_severity = row.get('violation_severity', [])
                                
                                # 문자열로 저장된 리스트를 파싱
                                if isinstance(violation_details, str):
                                    try:
                                        import ast
                                        violation_details = ast.literal_eval(violation_details)
                                    except:
                                        violation_details = []
                                
                                if isinstance(violation_severity, str):
                                    try:
                                        import ast
                                        violation_severity = ast.literal_eval(violation_severity)
                                    except:
                                        violation_severity = []
                                
                                # ERROR/WARNING 위반만 카운트 (INFO 제외)
                                for i, severity in enumerate(violation_severity):
                                    if severity in ['ERROR', 'WARNING']:
                                        violations_count += 1
                        else:
                            violations_count = violations_from_column
                            
                    elif 'total_violations' in result_df.columns:
                        violations_count += result_df['total_violations'].sum()
                    break
                # Fallback: 현재 디렉토리에서 찾기
                elif os.path.exists(result_file):
                    result_df = pd.read_csv(result_file, encoding='utf-8-sig')
                    
                    # violations 컬럼이 0이면 violation_details에서 실제 위반 계산
                    if ('longi_count' in result_df.columns and 'assigned_bay' in result_df.columns
                        and total_longi_count == 0):
                        assigned_series = result_df['assigned_bay'].fillna('').astype(str).str.upper()
                        longi_series = pd.to_numeric(result_df['longi_count'], errors='coerce').fillna(0)
                        bay_a_mask = assigned_series.str.contains('35A', na=False) | assigned_series.eq('A') | assigned_series.str.contains('BAY_35A', na=False)
                        bay_b_mask = assigned_series.str.contains('36B', na=False) | assigned_series.eq('B') | assigned_series.str.contains('BAY_36B', na=False)
                        bay_a_longi_total = int(longi_series[bay_a_mask].sum())
                        bay_b_longi_total = int(longi_series[bay_b_mask].sum())
                        total_longi_count = int(longi_series.sum())

                    if 'violations' in result_df.columns:
                        violations_from_column = result_df['violations'].sum()
                        
                        # violations 컬럼이 0이면 violation_details에서 계산
                        if violations_from_column == 0 and 'violation_details' in result_df.columns:
                            for _, row in result_df.iterrows():
                                violation_details = row.get('violation_details', [])
                                violation_severity = row.get('violation_severity', [])
                                
                                # 문자열로 저장된 리스트를 파싱
                                if isinstance(violation_details, str):
                                    try:
                                        import ast
                                        violation_details = ast.literal_eval(violation_details)
                                    except:
                                        violation_details = []
                                
                                if isinstance(violation_severity, str):
                                    try:
                                        import ast
                                        violation_severity = ast.literal_eval(violation_severity)
                                    except:
                                        violation_severity = []
                                
                                # ERROR/WARNING 위반만 카운트 (INFO 제외)
                                for i, severity in enumerate(violation_severity):
                                    if severity in ['ERROR', 'WARNING']:
                                        violations_count += 1
                        else:
                            violations_count = violations_from_column
                            
                    elif 'total_violations' in result_df.columns:
                        violations_count += result_df['total_violations'].sum()
                    break
        except Exception as e:
            print(f"   {method_name} 위반 정보 추출 실패: {e}")
            violations_count = 0
        
        stats[method_name] = {
            'start_time': start_time,
            'end_time': end_time,
            'total_duration_hours': total_duration,
            'total_blocks': block_count,
            'bay_a_blocks': len(bay_a_blocks),
            'bay_b_blocks': len(bay_b_blocks),
            'bay_a_longi_total': bay_a_longi_total,
            'bay_b_longi_total': bay_b_longi_total,
            'total_longi_count': total_longi_count,
            'blocks_per_day': block_count / len(dates_data),
            'dates_count': len(dates_data),
            'violations_count': violations_count  # 제약조건 위반 수 추가
        }
    
    return stats

def create_comparison_chart(stats, save_folder):
    """방법론 비교 차트 생성"""
    if not stats:
        print("비교할 통계 데이터가 없습니다.")
        return
    
    # 🆕 방법론 순서 정의 (사용자 요청에 따라)
    method_order = []
    available_methods = list(stats.keys())
    
    # 우선순위 순서 정의
    if '실적데이터' in available_methods:
        method_order.append('실적데이터')
    if '실적데이터_착수일_최적화' in available_methods:
        method_order.append('실적데이터_착수일_최적화')
    if 'SPT' in available_methods:
        method_order.append('SPT')
    if 'LPT' in available_methods:
        method_order.append('LPT')
    if 'Seam_min' in available_methods:
        method_order.append('Seam_min')
    if 'Random' in available_methods:
        method_order.append('Random')
    if 'RL' in available_methods:
        method_order.append('RL')

    # 정의되지 않은 방법들 추가
    for method in available_methods:
        if method not in method_order:
            method_order.append(method)
    
    methods = method_order
    
    # 알고리즘별 일관된 색상 설정 (모든 차트에서 동일하게 사용)
    method_colors = {
        '실적데이터': '#8E8E93',      # 회색
        '실적데이터_착수일_최적화': '#34C759',  # 초록색
        'SPT': '#007AFF',     # 파란색
        'LPT': '#AF52DE',     # 보라색
        'Seam_min': '#FF9500',  # 주황색
        'Random': '#5AC8FA',  # 하늘색
        'RL': '#FF3B30',         # 빨간색 (가장 눈에 띄게)
    }
    # 현재 방법론들의 색상 리스트 생성
    colors = [method_colors.get(method, '#999999') for method in methods]
    
    # 비교 차트 (2x3 레이아웃으로 확장 - 제약조건 위반 차트 추가)
    fig, ((ax1, ax2, ax3), (ax4, ax5, ax6)) = plt.subplots(2, 3, figsize=(30, 16))
    
    # 1. 총 소요 시간 비교
    durations = [stats[method]['total_duration_hours'] for method in methods]
    
    bars1 = ax1.bar(methods, durations, color=colors, alpha=0.8)
    ax1.set_title('총 소요 시간 비교', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Makespan(h))', fontsize=12)
    
    # 막대 위에 값 표시
    for bar, duration in zip(bars1, durations):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f'{duration:.1f}h', ha='center', va='bottom', fontweight='bold')
    
    # 2. 처리된 블록 수 비교
    block_counts = [stats[method]['total_blocks'] for method in methods]
    bars2 = ax2.bar(methods, block_counts, color=colors, alpha=0.8)
    ax2.set_title('처리된 총 블록 수 비교', fontsize=14, fontweight='bold')
    ax2.set_ylabel('블록 수', fontsize=12)
    
    for bar, count in zip(bars2, block_counts):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'{count}개', ha='center', va='bottom', fontweight='bold')
    
    # 3. 베이별 블록 분배
    bay_a_counts = [stats[method]['bay_a_blocks'] for method in methods]
    bay_b_counts = [stats[method]['bay_b_blocks'] for method in methods]
    
    x = np.arange(len(methods))
    width = 0.35
    
    bars3a = ax3.bar(x - width/2, bay_a_counts, width, label='베이A (35A)', color='#FF8E53', alpha=0.8)
    bars3b = ax3.bar(x + width/2, bay_b_counts, width, label='베이B (36B)', color='#4ECDC4', alpha=0.8)
    
    ax3.set_title('베이별 블록 분배', fontsize=14, fontweight='bold')
    ax3.set_ylabel('블록 수', fontsize=12)
    ax3.set_xticks(x)
    ax3.set_xticklabels(methods)
    ax3.legend()
    
    # 값 표시
    for bars in [bars3a, bars3b]:
        for bar in bars:
            height = bar.get_height()
            ax3.text(bar.get_x() + bar.get_width()/2, height + 0.3,
                    f'{int(height)}', ha='center', va='bottom', fontweight='bold')
    
    # 4. 일별 처리율
    daily_rates = [stats[method]['blocks_per_day'] for method in methods]
    bars4 = ax4.bar(methods, daily_rates, color=colors, alpha=0.8)
    ax4.set_title('일평균 블록 처리율', fontsize=14, fontweight='bold')
    ax4.set_ylabel('블록/일', fontsize=12)
    
    for bar, rate in zip(bars4, daily_rates):
        ax4.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{rate:.1f}', ha='center', va='bottom', fontweight='bold')
    
    # 🆕 5. 제약조건 위반 비교 (알고리즘별 색상 적용)
    violations = [stats[method]['violations_count'] for method in methods]
    bars5 = ax5.bar(methods, violations, color=colors, alpha=0.8)
    ax5.set_title('제약조건 위반 건수', fontsize=14, fontweight='bold')
    ax5.set_ylabel('Violations', fontsize=12)
    
    for bar, violation in zip(bars5, violations):
        ax5.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'{int(violation)}건', ha='center', va='bottom', fontweight='bold')
    
    # 🆕 6. 성능 종합 점수 (알고리즘별 색상 적용)
    performance_scores = []
    for method in methods:
        duration = stats[method]['total_duration_hours']
        violation = stats[method]['violations_count']
        # 성능 점수: 낮을수록 좋음 (Makespan + 위반 페널티)
        penalty = violation * 5  # 위반 1건당 5시간 페널티
        score = duration + penalty
        performance_scores.append(score)
    
    bars6 = ax6.bar(methods, performance_scores, color=colors, alpha=0.8)
    ax6.set_title('종합 성능 점수 (낮을수록 좋음)', fontsize=14, fontweight='bold')
    ax6.set_ylabel('점수 (시간 + 위반페널티)', fontsize=12)
    
    for bar, score in zip(bars6, performance_scores):
        ax6.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f'{score:.1f}', ha='center', va='bottom', fontweight='bold')
    
    plt.tight_layout()
    
    # 통합 차트 저장
    comparison_path = os.path.join(save_folder, "방법론_비교_차트.png")
    plt.savefig(comparison_path, dpi=300, bbox_inches='tight')
    print(f"📊 통합 비교 차트 저장: {comparison_path}")
    
    # 개별 차트들을 각각 저장
    individual_charts = [
        (ax1, "1_총소요시간비교.png", "총 소요 시간 비교"),
        (ax2, "2_처리블록수비교.png", "처리된 총 블록 수 비교"),
        (ax3, "3_베이별블록분배.png", "베이별 블록 분배"),
        (ax4, "4_일평균처리율.png", "일평균 블록 처리율"),
        (ax5, "5_제약조건위반건수.png", "제약조건 위반 건수"),
        (ax6, "6_종합성능점수.png", "종합 성능 점수 (낮을수록 좋음)")
    ]
    
    for ax, filename, title in individual_charts:
        # 개별 차트를 위한 새 figure 생성
        individual_fig, individual_ax = plt.subplots(figsize=(12, 8))
        
        # 원본 차트의 데이터와 스타일 복사
        if ax == ax1:  # 총 소요 시간
            individual_ax.bar(methods, durations, color=colors, alpha=0.8)
            individual_ax.set_ylabel('Makespan(h)', fontsize=12)
            for i, (method, duration) in enumerate(zip(methods, durations)):
                individual_ax.text(i, duration + 1, f'{duration:.1f}h', 
                                 ha='center', va='bottom', fontweight='bold')
        elif ax == ax2:  # 처리된 블록 수
            block_counts = [stats[method]['total_blocks'] for method in methods]
            individual_ax.bar(methods, block_counts, color=colors, alpha=0.8)
            individual_ax.set_ylabel('블록 수', fontsize=12)
            for i, (method, count) in enumerate(zip(methods, block_counts)):
                individual_ax.text(i, count + 0.5, f'{count}개', 
                                 ha='center', va='bottom', fontweight='bold')
        elif ax == ax3:  # 베이별 분배
            bay_a_counts = [stats[method]['bay_a_blocks'] for method in methods]
            bay_b_counts = [stats[method]['bay_b_blocks'] for method in methods]
            x = np.arange(len(methods))
            width = 0.35
            individual_ax.bar(x - width/2, bay_a_counts, width, label='베이A (35A)', color='#FF8E53', alpha=0.8)
            individual_ax.bar(x + width/2, bay_b_counts, width, label='베이B (36B)', color='#4ECDC4', alpha=0.8)
            individual_ax.set_ylabel('블록 수', fontsize=12)
            individual_ax.set_xticks(x)
            individual_ax.set_xticklabels(methods)
            individual_ax.legend()
            for i, (a_count, b_count) in enumerate(zip(bay_a_counts, bay_b_counts)):
                individual_ax.text(i - width/2, a_count + 0.3, f'{int(a_count)}', 
                                 ha='center', va='bottom', fontweight='bold')
                individual_ax.text(i + width/2, b_count + 0.3, f'{int(b_count)}', 
                                 ha='center', va='bottom', fontweight='bold')
        elif ax == ax4:  # 일평균 처리율
            daily_rates = [stats[method]['blocks_per_day'] for method in methods]
            individual_ax.bar(methods, daily_rates, color=colors, alpha=0.8)
            individual_ax.set_ylabel('블록/일', fontsize=12)
            for i, (method, rate) in enumerate(zip(methods, daily_rates)):
                individual_ax.text(i, rate + 0.1, f'{rate:.1f}', 
                                 ha='center', va='bottom', fontweight='bold')
        elif ax == ax5:  # 제약조건 위반
            individual_ax.bar(methods, violations, color=colors, alpha=0.8)
            individual_ax.set_ylabel('Violations', fontsize=12)
            for i, (method, violation) in enumerate(zip(methods, violations)):
                individual_ax.text(i, violation + 0.5, f'{int(violation)}건', 
                                 ha='center', va='bottom', fontweight='bold')
        elif ax == ax6:  # 종합 성능 점수
            individual_ax.bar(methods, performance_scores, color=colors, alpha=0.8)
            individual_ax.set_ylabel('점수 (시간 + 위반페널티)', fontsize=12)
            for i, (method, score) in enumerate(zip(methods, performance_scores)):
                individual_ax.text(i, score + 1, f'{score:.1f}', 
                                 ha='center', va='bottom', fontweight='bold')
        
        individual_ax.set_title(title, fontsize=16, fontweight='bold')
        individual_ax.grid(True, alpha=0.3, linestyle=':')
        
        # 개별 차트 저장
        individual_path = os.path.join(save_folder, filename)
        individual_fig.savefig(individual_path, dpi=300, bbox_inches='tight')
        print(f"📊 개별 차트 저장: {filename}")
        
        # 메모리 절약을 위해 닫기
        plt.close(individual_fig)

    # 베이별 론지 합계 비교 (별도 차트)
    if any(stats[method].get('total_longi_count', 0) > 0 for method in methods):
        bay_a_longi_totals = [stats[method].get('bay_a_longi_total', 0) for method in methods]
        bay_b_longi_totals = [stats[method].get('bay_b_longi_total', 0) for method in methods]
        idx = np.arange(len(methods))
        width = 0.35

        longi_fig, longi_ax = plt.subplots(figsize=(12, 8))
        longi_ax.bar(idx - width/2, bay_a_longi_totals, width, label='베이A (35A)', color='#FF6F61', alpha=0.85)
        longi_ax.bar(idx + width/2, bay_b_longi_totals, width, label='베이B (36B)', color='#1E90FF', alpha=0.85)
        longi_ax.set_xticks(idx)
        longi_ax.set_xticklabels(methods)
        longi_ax.set_ylabel('론지 수 (ea)', fontsize=12)
        longi_ax.set_title('베이별 론지 합계', fontsize=16, fontweight='bold')
        longi_ax.grid(True, alpha=0.2, linestyle=':')
        longi_ax.legend()

        for i, (a_total, b_total) in enumerate(zip(bay_a_longi_totals, bay_b_longi_totals)):
            longi_ax.text(i - width/2, a_total + max(a_total * 0.02, 1), f'{int(a_total)}',
                          ha='center', va='bottom', fontweight='bold')
            longi_ax.text(i + width/2, b_total + max(b_total * 0.02, 1), f'{int(b_total)}',
                          ha='center', va='bottom', fontweight='bold')

        longi_chart_path = os.path.join(save_folder, "7_베이별론지합계.png")
        longi_fig.savefig(longi_chart_path, dpi=300, bbox_inches='tight')
        plt.close(longi_fig)
        print(f"📊 베이별 론지 합계 차트 저장: {longi_chart_path}")

    # 자동 표시 제거 (사용자 요청)
    # plt.show()  # 제거됨
    plt.close(fig)  # 메모리 절약을 위해 닫기

    # 통계 테이블 저장
    stats_df = pd.DataFrame(stats).T
    
    # datetime 컬럼들을 문자열로 변환
    for method in stats_df.index:
        stats_df.loc[method, 'start_time'] = stats[method]['start_time'].strftime('%Y-%m-%d %H:%M')
        stats_df.loc[method, 'end_time'] = stats[method]['end_time'].strftime('%Y-%m-%d %H:%M')
    
    stats_df['total_duration_hours'] = stats_df['total_duration_hours'].astype(float).round(1)
    stats_df['blocks_per_day'] = stats_df['blocks_per_day'].astype(float).round(1)
    stats_df['violations_count'] = stats_df['violations_count'].astype(int)
    int_columns = ['bay_a_blocks', 'bay_b_blocks', 'bay_a_longi_total', 'bay_b_longi_total', 'total_longi_count']
    for col in int_columns:
        if col in stats_df.columns:
            stats_df[col] = stats_df[col].fillna(0).astype(int)
    
    stats_path = os.path.join(save_folder, "방법론_비교_통계.csv")
    stats_df.to_csv(stats_path, encoding='utf-8-sig')
    print(f" 통계 테이블 저장: {stats_path}")

def generate_enhanced_gantt_charts(search_dir=None):
    """향상된 간트차트 생성"""
    print("🎯 향상된 공정별 간트차트 생성기 시작")
    
    # 🔧 결과 폴더 자동 감지
    if search_dir is None:
        search_dir = find_latest_result_folder()
        if search_dir is None:
            search_dir = RESULT_BASE_FOLDER
    
    print(f" 검색 디렉토리: {search_dir}")
    print("=" * 60)
    
    # CSV 파일들 로드 (결과 폴더에서)
    data_by_method = load_process_csv_files(search_dir=search_dir)
    
    # 🆕 폴더 생성 (데이터 기반으로 날짜 추출, 결과 폴더 하위에)
    base_folder = create_gantt_folders(data_by_method, search_dir)
    
    if not data_by_method:
        print(" CSV 파일을 찾을 수 없습니다.")
        return
    
    print(f"\n 발견된 방식들: {list(data_by_method.keys())}")
    
    # 각 방법론별로 간트차트 생성
    for method_name in data_by_method.keys():
        print(f"\n {method_name} 간트차트 생성 중...")
        
        # 방법론별 폴더 생성
        method_folder = os.path.join(base_folder, method_name)
        if not os.path.exists(method_folder):
            os.makedirs(method_folder)
            print(f" 폴더 생성: {method_folder}")
        
        # 전체 기간 간트차트
        all_data = []
        for date, df in data_by_method[method_name].items():
            df_with_line = assign_machine_line(df)
            all_data.append(df_with_line)
        
        if all_data:
            combined_df = pd.concat(all_data, ignore_index=True)
            
            # 전체 간트차트
            all_chart_title = f'{method_name.replace("_", " ")} 방식 - 전체 기간 간트차트'
            all_save_path = os.path.join(method_folder, "전체_기간_간트차트.png")
            create_enhanced_gantt_chart(combined_df, method_name, all_chart_title, all_save_path)
            
            # 일별 간트차트
            for date, df in data_by_method[method_name].items():
                df_with_line = assign_machine_line(df)
                
                daily_chart_title = f'{method_name.replace("_", " ")} 방식 - {date} 일별 간트차트'
                daily_save_path = os.path.join(method_folder, f"일별_{date}_간트차트.png")
                create_enhanced_gantt_chart(df_with_line, method_name, daily_chart_title, daily_save_path)
    
    # 🏆 방법론 비교 자료 생성
    print(f"\n🏆 방법론 비교 자료 생성 중...")
    stats = calculate_method_stats(data_by_method)
    create_comparison_chart(stats, base_folder)

if __name__ == "__main__":
    # [AGENT-EDIT] 기존 스크립트 동작은 유지하되, 발표용 "요약 메트릭 간트차트"만 생성하는 옵션을 추가합니다.
    import argparse
    import json

    parser = argparse.ArgumentParser(description="PBS 간트차트/요약차트 생성기 (fix 버전)")
    parser.add_argument(
        "--result_dir",
        type=str,
        default=None,
        help="결과 폴더 경로(예: PPO/eval/20250609_0129_15_29_seed42). 비우면 최신 결과 폴더 자동 탐색.",
    )
    parser.add_argument(
        "--summary_only",
        action="store_true",
        help="상세 공정 간트차트 대신, (총 소요시간/위반건수) 요약 차트만 생성",
    )
    parser.add_argument(
        "--manual_metrics_json",
        type=str,
        default=None,
        help="요약 차트에 사용할 수동 메트릭 JSON 파일 경로. (없으면 DEFAULT_MANUAL_METRICS 사용)",
    )
    parser.add_argument(
        "--prefer_manual_metrics",
        action="store_true",
        help="요약 차트에서 수동 메트릭을 자동 추출값보다 우선 적용 (값이 None이면 CSV 자동값 유지)",
    )
    parser.add_argument(
        "--rl_results_csv",
        type=str,
        default="rl_best_results.csv",
        help="RL 결과 CSV 파일명(기본: rl_best_results.csv)",
    )
    parser.add_argument(
        "--summary_save_name",
        type=str,
        default="요약_메트릭_간트차트.png",
        help="요약 차트 저장 파일명",
    )
    args = parser.parse_args()

    # 결과 폴더 결정
    target_dir = args.result_dir or find_latest_result_folder() or RESULT_BASE_FOLDER

    manual_metrics: Optional[Dict[str, Dict[str, float]]] = None
    if args.manual_metrics_json and os.path.exists(args.manual_metrics_json):
        try:
            with open(args.manual_metrics_json, "r", encoding="utf-8") as f:
                manual_metrics = json.load(f)
        except Exception as e:
            print(f"⚠️ manual_metrics_json 로드 실패: {e}")

    if args.summary_only:
        # JSON을 넘겼으면(수동값 의도가 명확) 우선 적용을 기본으로 켠다.
        prefer_manual = args.prefer_manual_metrics or (manual_metrics is not None)
        create_summary_gantt_like_chart(
            result_folder=target_dir,
            manual_metrics=manual_metrics,
            rl_results_csv=args.rl_results_csv,
            save_name=args.summary_save_name,
            prefer_manual_metrics=prefer_manual,
        )
    else:
        # 최신 결과 폴더의 CSV 파일들로 간트차트 생성
        generate_enhanced_gantt_charts(search_dir=target_dir)

        print("\n 모든 간트차트 및 비교 자료 생성 완료!")
        print(" 생성된 구조:")
        print("   PPO/eval/<결과폴더>/gantt_charts_<날짜>/")
        print("   ├── 실적데이터_착수일_최적화/")
        print("   ├── 실적데이터/")
        print("   ├── SPT/")
        print("   ├── LPT/")
        print("   ├── Seam_min/")
        print("   ├── Random/")
        print("   ├── RL/")
        print("   ├── 방법론_비교_차트.png")
        print("   ├── (옵션)7_베이별론지합계.png")
        print("   └── 방법론_비교_통계.csv")
