from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# [AGENT-ADD] Streamlit may put apps/ on sys.path instead of the repository root.
# Add the root before importing local packages such as llm_interface and utils.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from llm_interface.analysis_report import explain_analysis_bundle, load_analysis_bundle, save_analysis_report
from utils.gantt_chart_enhanced import assign_machine_line, create_enhanced_gantt_chart, create_machine_mapping


# [AGENT-ADD] Operational dashboard-style GUI on top of the existing interactive wrapper.
# It does not replace the scheduler. It orchestrates request composition,
# rerun execution, comparison display, and grounded explanation rendering.

RESULTS_ROOT = REPO_ROOT / "results" / "fresh_runs"
ENV_DIR = REPO_ROOT / "environment"
PPO_DIR = REPO_ROOT / "PPO"
RESCHEDULE_SCRIPT = REPO_ROOT / "experiments" / "interactive_llm_reschedule.py"
RECOMMEND_SCRIPT = REPO_ROOT / "experiments" / "interactive_llm_recommendation.py"

TOGGLE_KEEP = "기본값 유지"
TOGGLE_RUNTIME_ON_AUDIT_ON = "Runtime ON / Audit ON"
TOGGLE_RUNTIME_OFF_AUDIT_ON = "Runtime OFF / Audit ON"
TOGGLE_OPTIONS = [TOGGLE_KEEP, TOGGLE_RUNTIME_ON_AUDIT_ON, TOGGLE_RUNTIME_OFF_AUDIT_ON]

CONSTRAINT_TOGGLES = [
    {"key": "c_seam", "label": "C/Seam 간격", "phrase": "C/Seam 간격"},
    {"key": "curved_spacing", "label": "곡판 간격", "phrase": "곡판간격"},
    {"key": "high_seam", "label": "고심수 간격", "phrase": "고심수간격"},
    {"key": "workshop_order", "label": "작업장 순서", "phrase": "작업장순서"},
    {"key": "line_group", "label": "라인 그룹", "phrase": "라인그룹"},
    {"key": "material_ready", "label": "자재 미입고", "phrase": "자재미입고"},
    {"key": "weekday_capacity", "label": "평일 용량", "phrase": "평일용량"},
    {"key": "weekend_capacity", "label": "주말 용량", "phrase": "주말용량"},
    {"key": "hot_capacity", "label": "혹서기 용량", "phrase": "혹서기용량"},
]

BIAS_OPTIONS = {
    "기본값 유지": None,
    "끄기": "후보축소마스킹 꺼줘",
    "1만": "후보축소마스킹 1만 켜줘",
    "1+2": "후보축소마스킹 1 2만 켜줘",
    "1+3": "후보축소마스킹 1 3만 켜줘",
    "1+2+3": "후보축소마스킹 1 2 3만 켜줘",
}

METHOD_ORDER = ["rl", "lpt", "spt"]
DEFAULT_VISIBLE_METHODS = ["rl"]

DEMO_PROMPTS = {
    "긴급 4번째 투입": "11번 블록은 네 번째로 해줘",
    "선후행 변경": "11번은 4번보다 먼저 오게 해줘",
    "베이 강제": "11번 블록은 35A로 배정해줘",
    "일일 상한": "11일날에는 15개 블록만 해야해. 최대.",
    "C/Seam 완화": "C/Seam 간격은 runtime만 끄고 audit는 유지해줘",
}

SAMPLE_CASE_DIRS = {
    "rl": RESULTS_ROOT / "interactive_trace_bundle_rl_pos4_frozen_20260417",
    "lpt": RESULTS_ROOT / "interactive_trace_bundle_lpt_pos4_frozen_20260417",
}

LLM_PARSER_OPTIONS = {
    "규칙 기반": "deterministic",
    "Groq": "groq",
    "Gemini": "gemini",
    "Ollama 로컬": "ollama",
    "OpenAI": "openai",
    "OpenAI 호환 직접 지정": "openai_compatible",
}


@st.cache_data(show_spinner=False)
def discover_excel_files() -> List[str]:
    if not ENV_DIR.exists():
        return []
    return sorted(str(path.relative_to(REPO_ROOT)) for path in ENV_DIR.glob("*.xlsx"))


@st.cache_data(show_spinner=False)
def discover_rl_models() -> List[str]:
    candidates = sorted(
        PPO_DIR.rglob("*.pth"),
        key=lambda path: (0 if "best" in path.name.lower() else 1, -path.stat().st_mtime),
    )
    return [str(path.relative_to(REPO_ROOT)) for path in candidates]


@st.cache_data(show_spinner=False)
def infer_planning_anchor_date(excel_path: str, sheet_name: str | None) -> Optional[date]:
    """[AGENT-ADD] Infer the date used as date_offset=0 from the selected Excel."""
    if not excel_path:
        return None
    source_path = Path(excel_path)
    if not source_path.is_absolute():
        source_path = REPO_ROOT / source_path
    if not source_path.exists():
        return None
    try:
        df = pd.read_excel(source_path, sheet_name=sheet_name or 0, usecols=["조립 착수일"])
    except Exception:
        return None
    if "조립 착수일" not in df.columns:
        return None
    parsed = pd.to_datetime(df["조립 착수일"].astype(str), format="%Y%m%d", errors="coerce").dropna()
    if parsed.empty:
        return None
    return parsed.min().date()


@st.cache_data(show_spinner=False)
def load_csv(path: str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    csv_path = Path(path)
    if not csv_path.exists():
        return pd.DataFrame()
    return pd.read_csv(csv_path, encoding="utf-8-sig")



def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .status-card {
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 14px;
            padding: 16px 18px;
            margin-bottom: 12px;
            color: #e2e8f0;
        }
        .status-card h4 {
            margin: 0 0 10px 0;
            font-size: 1.05rem;
            color: #f8fafc;
        }
        .status-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px 12px;
        }
        .status-metric {
            background: #111827;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 10px 12px;
        }
        .status-metric-label {
            font-size: 0.75rem;
            color: #94a3b8;
            margin-bottom: 4px;
        }
        .status-metric-value {
            font-size: 1rem;
            font-weight: 700;
            color: #f8fafc;
        }
        .block-chip {
            display: inline-block;
            padding: 4px 10px;
            margin: 0 6px 6px 0;
            border-radius: 999px;
            border: 1px solid #cbd5e1;
            background: #f8fafc;
            color: #0f172a;
            font-size: 0.82rem;
        }
        .method-board {
            border: 1px solid #dbe4ee;
            border-radius: 14px;
            padding: 14px 14px 10px 14px;
            min-height: 720px;
            background: #ffffff;
        }
        .method-board h4 {
            margin: 0 0 8px 0;
        }
        .small-note {
            color: #64748b;
            font-size: 0.82rem;
        }
        .panel-title {
            margin-top: 0.2rem;
            margin-bottom: 0.4rem;
        }
        .lane-box {
            border: 1px solid #cbd5e1;
            border-radius: 12px;
            padding: 10px;
            margin: 8px 0 10px 0;
            background: #f8fafc;
        }
        .lane-title {
            font-size: 0.82rem;
            font-weight: 700;
            color: #334155;
            margin-bottom: 8px;
        }
        .lane-track {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        .lane-block {
            display: inline-block;
            min-width: 86px;
            padding: 8px 8px;
            border-radius: 10px;
            border: 1px solid #94a3b8;
            background: #e2e8f0;
            color: #0f172a;
            font-size: 0.74rem;
            line-height: 1.25;
        }
        .lane-block.method-lpt { background: #dbeafe; border-color: #60a5fa; }
        .lane-block.method-spt { background: #dcfce7; border-color: #4ade80; }
        .lane-block.method-rl { background: #ffedd5; border-color: #fb923c; }
        .lane-block.delayed { background: #fee2e2; border-color: #ef4444; }
        .lane-block.forced { box-shadow: inset 0 0 0 2px #7c3aed; }
        .demo-button-note {
            color: #64748b;
            font-size: 0.78rem;
            margin-top: 4px;
        }
        .top-sequence-title {
            font-size: 1.05rem;
            font-weight: 800;
            color: #0f172a;
            margin: 14px 0 10px 0;
            padding-top: 6px;
            border-top: 1px solid #e2e8f0;
        }
        .top-sequence-card {
            border: 1px solid #dbe4ee;
            border-radius: 16px;
            background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
            padding: 14px 16px;
            min-height: 168px;
            box-shadow: 0 8px 22px rgba(15, 23, 42, 0.05);
        }
        .top-sequence-method {
            font-size: 1.15rem;
            font-weight: 850;
            color: #0f172a;
            margin-bottom: 8px;
        }
        .top-sequence-kpis {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 7px;
            margin-bottom: 10px;
        }
        .top-kpi-box {
            border-radius: 10px;
            background: #eef2ff;
            padding: 8px 8px;
            border: 1px solid #c7d2fe;
        }
        .top-kpi-label {
            font-size: 0.68rem;
            color: #64748b;
            margin-bottom: 2px;
        }
        .top-kpi-value {
            font-size: 0.95rem;
            color: #111827;
            font-weight: 800;
        }
        .sequence-line {
            font-size: 0.84rem;
            color: #334155;
            line-height: 1.55;
            word-break: keep-all;
        }
        /* [AGENT-ADD] Current-block banner */
        .current-card-title {
            font-size: 1.0rem;
            font-weight: 800;
            color: #0f172a;
            margin: 4px 0 8px 0;
        }
        .current-card-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 10px;
            margin-bottom: 14px;
        }
        .current-card {
            border: 2px solid #cbd5e1;
            border-radius: 14px;
            background: #ffffff;
            padding: 10px 12px 12px 12px;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
        }
        .current-card-method {
            display: inline-block;
            color: #ffffff;
            font-weight: 800;
            font-size: 0.78rem;
            padding: 3px 10px;
            border-radius: 999px;
            margin-bottom: 8px;
            letter-spacing: 0.06em;
        }
        .current-card-block {
            font-size: 0.9rem;
            color: #0f172a;
            margin-bottom: 4px;
        }
        .current-card-stage {
            font-size: 0.92rem;
            color: #1e293b;
            margin-bottom: 4px;
        }
        .current-card-meta {
            font-size: 0.76rem;
            color: #64748b;
        }
        .current-card-empty {
            font-size: 0.82rem;
            color: #94a3b8;
        }
        /* [AGENT-ADD] Pipeline flow diagram */
        .flow-panel {
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 14px 14px 16px 14px;
            margin: 6px 0 14px 0;
            background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
            box-shadow: 0 8px 22px rgba(15, 23, 42, 0.05);
        }
        .flow-panel-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        .flow-panel-title {
            font-size: 1.05rem;
            font-weight: 800;
            color: #0f172a;
        }
        .flow-panel-legend {
            display: flex;
            gap: 12px;
            font-size: 0.78rem;
            color: #475569;
        }
        .flow-legend-item {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-weight: 700;
        }
        .flow-legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }
        .flow-common-row {
            display: flex;
            align-items: stretch;
            flex-wrap: nowrap;
            overflow-x: auto;
            gap: 2px;
            padding-bottom: 4px;
        }
        .flow-split {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 10px 0;
        }
        .flow-split-line {
            flex: 1;
            height: 2px;
            background: linear-gradient(90deg, #e2e8f0, #94a3b8, #e2e8f0);
            border-radius: 2px;
        }
        .flow-split-label {
            font-size: 0.78rem;
            font-weight: 800;
            color: #475569;
            white-space: nowrap;
            padding: 2px 10px;
            background: #f1f5f9;
            border-radius: 999px;
        }
        .flow-bay-wrap {
            display: grid;
            grid-template-columns: 1fr;
            gap: 8px;
        }
        .flow-bay-row {
            display: flex;
            align-items: stretch;
            gap: 10px;
        }
        .flow-bay-tag {
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 82px;
            padding: 0 10px;
            border-radius: 12px;
            font-weight: 800;
            font-size: 0.82rem;
            color: #ffffff;
        }
        .flow-bay-tag-A { background: #0ea5e9; }
        .flow-bay-tag-B { background: #a855f7; }
        .flow-bay-chips {
            display: flex;
            flex: 1;
            gap: 2px;
            overflow-x: auto;
        }
        .flow-chip {
            flex: 1;
            min-width: 104px;
            border: 1px solid #e2e8f0;
            background: #ffffff;
            border-radius: 12px;
            padding: 8px 8px 6px 8px;
            text-align: center;
        }
        .flow-chip.flow-bay-common { background: #eff6ff; border-color: #bfdbfe; }
        .flow-chip.flow-bay-A { background: #ecfeff; border-color: #a5f3fc; }
        .flow-chip.flow-bay-B { background: #faf5ff; border-color: #e9d5ff; }
        .flow-chip.flow-chip-active {
            border-color: #0f172a;
            box-shadow: 0 4px 14px rgba(15, 23, 42, 0.18);
            transform: translateY(-1px);
        }
        .flow-chip-name {
            font-weight: 800;
            font-size: 0.84rem;
            color: #0f172a;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .flow-chip-badges {
            display: flex;
            justify-content: center;
            gap: 3px;
            margin-top: 4px;
            min-height: 18px;
            flex-wrap: wrap;
        }
        .flow-badge {
            color: #ffffff;
            font-size: 0.64rem;
            font-weight: 800;
            padding: 1px 6px;
            border-radius: 999px;
            letter-spacing: 0.04em;
        }
        .flow-arrow {
            display: flex;
            align-items: center;
            justify-content: center;
            color: #94a3b8;
            font-weight: 700;
            font-size: 1.1rem;
            padding: 0 2px;
        }

        /* [AGENT-EDIT] Shop-map visualization for professor demo screenshots. */
        .shop-map {
            position: relative;
            height: 268px;
            border: 1px solid #dbe4ee;
            border-radius: 18px;
            background:
                radial-gradient(circle at 8% 18%, rgba(14, 165, 233, 0.12), transparent 25%),
                linear-gradient(180deg, #ffffff 0%, #f8fafc 58%, #eef2f7 100%);
            overflow: hidden;
            margin: 6px 0 12px 0;
            box-shadow: 0 10px 26px rgba(15, 23, 42, 0.06);
        }
        .shop-map-title {
            position: absolute;
            left: 18px;
            top: 15px;
            font-size: 1.05rem;
            font-weight: 900;
            color: #0f172a;
        }
        .shop-map-subtitle {
            position: absolute;
            left: 18px;
            top: 40px;
            font-size: 0.78rem;
            font-weight: 700;
            color: #64748b;
        }
        .shop-conveyor-main {
            position: absolute;
            left: 5.5%;
            right: 34%;
            top: 142px;
            height: 16px;
            border-radius: 999px;
            background: repeating-linear-gradient(90deg, #64748b 0 12px, #cbd5e1 12px 24px);
            box-shadow: 0 7px 0 #334155;
        }
        .shop-conveyor-branch {
            position: absolute;
            left: 61%;
            width: 28%;
            height: 10px;
            border-radius: 999px;
            background: linear-gradient(90deg, #94a3b8, #cbd5e1);
            box-shadow: 0 5px 0 rgba(51, 65, 85, 0.35);
        }
        .shop-conveyor-branch.a { top: 104px; }
        .shop-conveyor-branch.b { top: 195px; }
        .shop-split-post {
            position: absolute;
            left: 60.5%;
            top: 102px;
            width: 2px;
            height: 104px;
            background: #94a3b8;
        }
        .shop-yard {
            position: absolute;
            right: 3.6%;
            top: 82px;
            width: 8.5%;
            height: 124px;
            background: linear-gradient(135deg, #334155 0%, #475569 100%);
            transform: skewY(-9deg);
            border-radius: 4px;
            box-shadow: 0 10px 18px rgba(15, 23, 42, 0.16);
        }
        .shop-yard::before, .shop-yard::after {
            content: "";
            position: absolute;
            right: 12px;
            width: 54px;
            height: 22px;
            background: #e5e7eb;
            box-shadow: 0 5px 0 #9ca3af;
        }
        .shop-yard::before { top: -20px; }
        .shop-yard::after { bottom: 14px; right: 34px; }
        .shop-station {
            position: absolute;
            width: 118px;
            height: 58px;
            border: 1px solid #bfdbfe;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.84);
            box-shadow: 0 6px 12px rgba(15, 23, 42, 0.06);
            text-align: center;
            z-index: 2;
        }
        .shop-station-frame {
            position: absolute;
            left: 14px;
            right: 14px;
            top: 7px;
            height: 22px;
            border-left: 6px solid #2563eb;
            border-right: 6px solid #2563eb;
            border-top: 6px solid #3b82f6;
            transform: skewY(-5deg);
            opacity: 0.86;
        }
        .shop-station-name {
            position: absolute;
            left: 5px;
            right: 5px;
            bottom: 7px;
            font-size: 0.72rem;
            line-height: 1.1;
            font-weight: 900;
            color: #0f172a;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .shop-station-common { background: #eff6ff; }
        .shop-station-A { background: #ecfeff; border-color: #67e8f9; }
        .shop-station-B { background: #faf5ff; border-color: #d8b4fe; }
        .shop-station-active {
            border-color: #ea580c;
            background: #fff7ed;
            box-shadow: 0 8px 18px rgba(234, 88, 12, 0.22);
        }
        .shop-lane-label {
            position: absolute;
            left: 61.5%;
            width: 56px;
            height: 28px;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            font-size: 0.72rem;
            font-weight: 900;
            z-index: 3;
        }
        .shop-lane-label.a { top: 69px; background: #0ea5e9; }
        .shop-lane-label.b { top: 160px; background: #a855f7; }
        .shop-moving-block {
            position: absolute;
            min-width: 80px;
            padding: 7px 10px;
            border-radius: 12px;
            color: #ffffff;
            font-size: 0.74rem;
            font-weight: 950;
            text-align: center;
            box-shadow: 0 10px 18px rgba(15, 23, 42, 0.22);
            border: 2px solid rgba(255, 255, 255, 0.84);
            z-index: 5;
        }
        .shop-moving-block small {
            display: block;
            margin-top: 1px;
            font-size: 0.62rem;
            font-weight: 800;
            color: rgba(255, 255, 255, 0.88);
        }
        .shop-map-note {
            position: absolute;
            left: 18px;
            bottom: 14px;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 700;
        }
        /* [AGENT-ADD] Gemini-reference dashboard layout */
        .status-strip {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 6px 14px;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            background: #ffffff;
            margin: 2px 0 10px 0;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
        }
        .status-strip-title {
            font-size: 1.05rem;
            font-weight: 900;
            color: #0f172a;
            letter-spacing: 0.08em;
        }
        .status-strip-meta {
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
            align-items: center;
        }
        .status-strip-kpi {
            font-size: 0.78rem;
            color: #475569;
            background: #f1f5f9;
            padding: 4px 10px;
            border-radius: 999px;
        }
        .chatroom-title {
            font-size: 0.92rem;
            font-weight: 800;
            color: #0f172a;
            margin: 4px 0 8px 0;
        }
        .chatroom-item {
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 8px 10px;
            margin-bottom: 6px;
            background: #ffffff;
        }
        .chatroom-item-active {
            background: #e0f2fe;
            border-color: #38bdf8;
        }
        .chatroom-item-title {
            font-size: 0.82rem;
            font-weight: 700;
            color: #0f172a;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .chatroom-item-time {
            font-size: 0.7rem;
            color: #64748b;
            margin-top: 2px;
        }
        .chatroom-hint {
            font-size: 0.7rem;
            color: #94a3b8;
            margin-top: 6px;
        }
        .chat-panel-title {
            font-size: 0.96rem;
            font-weight: 800;
            color: #0f172a;
            margin: 4px 0 6px 0;
        }
        .chat-empty {
            padding: 20px 10px;
            color: #64748b;
            font-size: 0.88rem;
            text-align: center;
        }
        .seq-list-title {
            font-size: 0.98rem;
            font-weight: 800;
            color: #0f172a;
            margin: 4px 0 8px 0;
        }
        .seq-card {
            border: 2px solid #cbd5e1;
            border-radius: 14px;
            padding: 10px 12px;
            background: #ffffff;
            margin-bottom: 6px;
        }
        .seq-card-active {
            background: #f0f9ff;
            box-shadow: 0 6px 16px rgba(14, 165, 233, 0.14);
        }
        .seq-card-head {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }
        .seq-card-badge {
            color: #ffffff;
            font-weight: 800;
            font-size: 0.7rem;
            padding: 2px 8px;
            border-radius: 999px;
            letter-spacing: 0.05em;
        }
        .seq-card-title {
            font-size: 0.86rem;
            font-weight: 800;
            color: #0f172a;
        }
        .seq-card-sub {
            font-size: 0.72rem;
            color: #64748b;
            margin-bottom: 6px;
        }
        .seq-card-kpis {
            display: flex;
            gap: 10px;
            margin-bottom: 6px;
        }
        .seq-card-kpi {
            font-size: 0.78rem;
            color: #1e293b;
            background: #f8fafc;
            border-radius: 8px;
            padding: 3px 8px;
            border: 1px solid #e2e8f0;
        }
        .seq-card-head-seq {
            font-size: 0.72rem;
            color: #334155;
            background: #f1f5f9;
            padding: 5px 8px;
            border-radius: 8px;
            word-break: keep-all;
        }
        .analysis-card {
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            padding: 12px 14px 10px 14px;
            background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
            margin: 10px 0 10px 0;
        }
        .analysis-title {
            font-size: 0.96rem;
            font-weight: 800;
            color: #0f172a;
            margin-bottom: 8px;
        }
        .analysis-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
        }
        .analysis-cell {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 8px 10px;
        }
        .analysis-cell-label {
            font-size: 0.72rem;
            color: #64748b;
            margin-bottom: 2px;
        }
        .analysis-cell-value {
            font-size: 1.05rem;
            font-weight: 800;
            color: #0f172a;
        }
        .analysis-cell-sub {
            font-size: 0.72rem;
            color: #0ea5e9;
            font-weight: 700;
            margin-top: 2px;
        }
        /* [AGENT-ADD] ChatGPT homepage polish */
        .main .block-container {
            padding-top: 1.2rem !important;
            padding-bottom: 2rem !important;
            max-width: 100% !important;
        }
        [data-testid="stAppViewContainer"] {
            background: #f7f7f8;
        }
        [data-testid="stHeader"] {
            background: transparent;
            height: 0;
        }
        .stAppDeployButton, footer, #MainMenu {
            visibility: hidden;
        }
        /* Chatroom list */
        .chatroom-title {
            font-size: 0.78rem;
            font-weight: 700;
            color: #6b7280;
            margin: 2px 0 10px 0;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        .chatroom-item {
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 9px 12px;
            margin-bottom: 2px;
            background: transparent;
            transition: background 0.15s ease;
            cursor: pointer;
        }
        .chatroom-item:hover {
            background: #ececf1;
        }
        .chatroom-item-active {
            background: #e8e8ee;
            border-color: transparent;
        }
        .chatroom-item-title {
            font-size: 0.84rem;
            font-weight: 500;
            color: #202123;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .chatroom-item-time {
            font-size: 0.68rem;
            color: #8e8ea0;
            margin-top: 1px;
        }
        .chatroom-hint {
            font-size: 0.68rem;
            color: #8e8ea0;
            margin-top: 10px;
            padding: 6px 4px 0 4px;
            border-top: 1px solid #ececf1;
        }
        /* 새 대화 button — ChatGPT 스타일 */
        .stButton button[kind="secondary"]:has(div:contains("새 대화")) {
            background: #ffffff !important;
        }
        /* Chat panel */
        .chat-panel-title {
            font-size: 0.78rem;
            font-weight: 700;
            color: #6b7280;
            margin: 2px 0 8px 0;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        .chat-empty {
            padding: 40px 18px;
            color: #8e8ea0;
            font-size: 0.95rem;
            text-align: center;
            font-weight: 500;
        }
        /* Streamlit chat messages */
        [data-testid="stChatMessage"] {
            background: transparent !important;
            padding: 8px 4px !important;
            border: none !important;
            border-radius: 0 !important;
        }
        [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarUser"] + div,
        [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarAssistant"] + div {
            background: transparent;
        }
        /* Textarea / input polish */
        [data-testid="stTextArea"] textarea {
            border-radius: 14px !important;
            border: 1px solid #d9d9e3 !important;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04) !important;
            padding: 12px 14px !important;
            font-size: 0.92rem !important;
            background: #ffffff !important;
        }
        [data-testid="stTextArea"] textarea:focus {
            border-color: #10a37f !important;
            box-shadow: 0 0 0 3px rgba(16, 163, 127, 0.15) !important;
        }
        /* Form submit button (전송) */
        [data-testid="stFormSubmitButton"] button {
            background: #10a37f !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 10px !important;
            font-weight: 700 !important;
        }
        [data-testid="stFormSubmitButton"] button:hover {
            background: #0e8f6f !important;
        }
        /* Status strip */
        .status-strip {
            background: #ffffff !important;
            border: 1px solid #ececf1 !important;
            border-radius: 12px !important;
            padding: 10px 16px !important;
            margin-bottom: 10px !important;
            box-shadow: none !important;
        }
        .status-strip-title {
            color: #202123 !important;
        }
        .status-strip-kpi {
            background: #f7f7f8 !important;
            border: 1px solid #ececf1;
            color: #343541 !important;
            font-weight: 600;
        }
        /* Expander minimal style */
        [data-testid="stExpander"] {
            border: 1px solid #ececf1 !important;
            border-radius: 10px !important;
            background: #ffffff !important;
        }
        [data-testid="stExpander"] summary {
            font-weight: 600 !important;
            color: #343541 !important;
        }
        /* Containers with border softer */
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: #ececf1 !important;
            border-radius: 14px !important;
            background: #ffffff !important;
        }
        /* Chat action buttons (적용/비교) */
        .chat-action-row .stButton button {
            background: #ffffff !important;
            border: 1px solid #d9d9e3 !important;
            color: #343541 !important;
            font-weight: 600 !important;
            border-radius: 10px !important;
        }
        .chat-action-row .stButton button:hover {
            background: #f7f7f8 !important;
        }
        /* Sequence list title */
        .seq-list-title {
            font-size: 0.85rem !important;
            font-weight: 700 !important;
            color: #6b7280 !important;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin: 14px 0 10px 0 !important;
        }
        /* Profile footer in left column */
        .chatroom-footer {
            margin-top: 14px;
            padding: 10px 8px;
            border-top: 1px solid #ececf1;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .chatroom-footer-avatar {
            width: 30px;
            height: 30px;
            border-radius: 50%;
            background: linear-gradient(135deg, #10a37f 0%, #0d8a6c 100%);
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 0.82rem;
        }
        .chatroom-footer-name {
            font-size: 0.82rem;
            color: #343541;
            font-weight: 600;
        }
        .chatroom-footer-sub {
            font-size: 0.68rem;
            color: #8e8ea0;
        }
        /* [AGENT-ADD] Balance columns + enlarge gantt/graph */
        [data-testid="column"] [data-testid="stVerticalBlockBorderWrapper"] {
            min-height: 78vh;
        }
        [data-testid="column"] [data-testid="stVerticalBlockBorderWrapper"] > div {
            height: 100%;
        }
        .stPlotlyChart, [data-testid="stPyplotChart"] {
            min-height: 360px;
        }
        .seq-card-time {
            font-size: 0.7rem;
            color: #8e8ea0;
            margin-left: auto;
        }
        .rl-note {
            margin: 14px 0 4px 0;
            padding: 10px 12px;
            font-size: 0.8rem;
            border: 1px dashed #c7d2fe;
            border-radius: 10px;
            background: #eef2ff;
            color: #1e293b;
            line-height: 1.55;
        }
        .rl-note-tag {
            background: #ffffff;
            border: 1px solid #c7d2fe;
            padding: 1px 6px;
            border-radius: 6px;
            font-weight: 700;
            color: #4338ca;
            margin: 0 2px;
        }
        /* 채팅 입력/대화 열: 메시지가 많아질 때 자연스럽게 아래로 쌓임 */
        .chat-scroll {
            max-height: 52vh;
            overflow-y: auto;
            padding-right: 4px;
        }
        /* [AGENT-ADD] Redesigned layout: left rail + tabbed main */
        .rail-brand {
            font-size: 1.1rem;
            font-weight: 900;
            color: #10a37f;
            margin: 4px 0 14px 0;
            line-height: 1.15;
        }
        .rail-brand span {
            font-size: 0.7rem;
            font-weight: 600;
            color: #8e8ea0;
            letter-spacing: 0.06em;
        }
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: 4px;
            border-bottom: 1px solid #ececf1;
            padding: 0 4px;
        }
        [data-testid="stTabs"] [data-baseweb="tab"] {
            padding: 8px 18px !important;
            font-weight: 700 !important;
            color: #6b7280 !important;
            background: transparent !important;
            border: none !important;
            border-radius: 8px 8px 0 0 !important;
        }
        [data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {
            color: #10a37f !important;
            background: #ffffff !important;
            border-bottom: 2px solid #10a37f !important;
        }
        [data-testid="stTabs"] [data-baseweb="tab-panel"] {
            padding: 16px 6px 6px 6px !important;
        }
        .seq-list-title {
            font-size: 0.82rem !important;
            font-weight: 700 !important;
            color: #6b7280 !important;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin: 14px 0 8px 0 !important;
        }
        .analysis-card {
            margin-top: 0 !important;
        }
        /* 간트 & 추이 그래프 확대 */
        .stPlotlyChart {
            min-height: 420px !important;
        }
        [data-testid="stPyplotChart"] img {
            width: 100% !important;
            height: auto !important;
        }
        /* [AGENT-ADD] 단순화된 공정흐름의 블록 chip: method 색 없이 깔끔한 단색 */
        .flow-block-neutral {
            min-width: 58px !important;
            padding: 5px 8px !important;
            background: #0f172a !important;
            color: #ffffff !important;
            font-size: 0.72rem !important;
            border: 1.5px solid #ffffff !important;
        }
        .flow-block-neutral small {
            color: #94a3b8 !important;
            font-weight: 700;
        }
        /* [AGENT-ADD] 조립 공정 단계 chip-flow: 공통 → 분기 → 35A/36B */
        .pflow-wrap {
            border: 1px solid #ececf1;
            border-radius: 14px;
            background: #ffffff;
            padding: 14px 18px 16px 18px;
            margin: 6px 0 14px 0;
            box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
        }
        .pflow-title {
            font-size: 1rem;
            font-weight: 800;
            color: #111827;
            margin-bottom: 3px;
        }
        .pflow-sub {
            font-size: 0.78rem;
            color: #6b7280;
            margin-bottom: 14px;
        }
        .pflow-row {
            display: flex;
            align-items: stretch;
            gap: 8px;
            flex-wrap: nowrap;
            overflow-x: auto;
        }
        .pflow-common {
            display: flex;
            align-items: stretch;
            gap: 4px;
            flex-shrink: 0;
        }
        .pflow-split {
            position: relative;
            width: 28px;
            min-width: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .pflow-split::before {
            content: "";
            position: absolute;
            left: 0;
            top: 50%;
            width: 14px;
            height: 2px;
            background: #cbd5e1;
        }
        .pflow-split-up {
            position: absolute;
            left: 14px;
            top: 26%;
            width: 14px;
            height: 2px;
            background: #cbd5e1;
            transform-origin: left center;
            transform: rotate(-18deg);
        }
        .pflow-split-down {
            position: absolute;
            left: 14px;
            bottom: 26%;
            width: 14px;
            height: 2px;
            background: #cbd5e1;
            transform-origin: left center;
            transform: rotate(18deg);
        }
        .pflow-branches {
            display: flex;
            flex-direction: column;
            gap: 8px;
            flex: 1;
            min-width: 0;
        }
        .pflow-branch {
            display: flex;
            align-items: stretch;
            gap: 8px;
        }
        .pflow-branch-tag {
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 54px;
            padding: 0 10px;
            border-radius: 10px;
            font-weight: 900;
            font-size: 0.82rem;
            color: #ffffff;
            letter-spacing: 0.04em;
            flex-shrink: 0;
        }
        .pflow-branch-tag-a { background: #0ea5e9; }
        .pflow-branch-tag-b { background: #a855f7; }
        .pflow-branch-chain {
            display: flex;
            align-items: stretch;
            gap: 4px;
            flex: 1;
            min-width: 0;
        }
        .pflow-chip {
            flex: 1;
            min-width: 100px;
            border: 1px solid #e5e7eb;
            background: #f9fafb;
            border-radius: 10px;
            padding: 7px 8px 6px 8px;
            display: flex;
            flex-direction: column;
            transition: border-color 0.15s ease, background 0.15s ease;
        }
        .pflow-chip-active {
            border-color: #10a37f;
            background: #ecfdf5;
            box-shadow: 0 2px 8px rgba(16, 163, 127, 0.12);
        }
        .pflow-chip-name {
            font-size: 0.76rem;
            font-weight: 800;
            color: #111827;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            padding-bottom: 4px;
            border-bottom: 1px dashed #e5e7eb;
            margin-bottom: 4px;
        }
        .pflow-chip-body {
            display: flex;
            flex-direction: column;
            gap: 2px;
            min-height: 34px;
            justify-content: center;
        }
        .pflow-chip-empty {
            color: #cbd5e1;
            text-align: center;
            font-size: 0.9rem;
            font-weight: 800;
            letter-spacing: 0.1em;
        }
        .pflow-chip-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 4px;
            padding: 2px 4px;
            border-radius: 6px;
            background: #ffffff;
            border: 1px solid #d1fae5;
        }
        .pflow-chip-bid {
            font-size: 0.72rem;
            font-weight: 800;
            color: #065f46;
        }
        .pflow-chip-elapsed {
            font-size: 0.68rem;
            font-weight: 700;
            color: #6b7280;
        }
        .pflow-arrow {
            display: flex;
            align-items: center;
            justify-content: center;
            color: #94a3b8;
            font-weight: 700;
            font-size: 1rem;
            padding: 0 1px;
            flex-shrink: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )



def _toggle_fragment(label_phrase: str, state: str) -> str | None:
    if state == TOGGLE_KEEP:
        return None
    if state == TOGGLE_RUNTIME_ON_AUDIT_ON:
        return f"{label_phrase}은 runtime만 켜고 audit는 유지해줘"
    if state == TOGGLE_RUNTIME_OFF_AUDIT_ON:
        return f"{label_phrase}은 runtime만 끄고 audit는 유지해줘"
    return None



def _compose_request_text(chat_text: str, controls: Dict[str, Any]) -> str:
    fragments: List[str] = []
    chat_text = (chat_text or "").strip()
    if chat_text:
        fragments.append(chat_text)

    for item in CONSTRAINT_TOGGLES:
        fragment = _toggle_fragment(item["phrase"], controls["constraint_states"].get(item["key"], TOGGLE_KEEP))
        if fragment:
            fragments.append(fragment)

    bias_fragment = BIAS_OPTIONS.get(controls.get("bias_mode"))
    if bias_fragment:
        fragments.append(bias_fragment)

    if controls.get("enable_block_cap"):
        fragments.append(
            f"{int(controls['block_cap_day'])}일날에는 {int(controls['block_cap_value'])}개 블록만 해야해. 최대."
        )

    if controls.get("enable_seam_cap"):
        fragments.append(
            f"{int(controls['seam_cap_day'])}일에는 {int(controls['seam_cap_value'])}심까지만 해야해. 최대."
        )

    return "\n".join(fragment for fragment in fragments if fragment.strip())



def _split_date_items(raw_text: str) -> List[str]:
    """[AGENT-ADD] Parse comma/newline separated dates or ranges for calendar controls."""
    text = (raw_text or "").replace(";", ",").replace("\n", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _build_runtime_config_from_controls(controls: Dict[str, Any]) -> Dict[str, Any]:
    """[AGENT-ADD] Convert GUI calendar controls into runtime_config calendar overrides."""
    calendar: Dict[str, Any] = {}

    closed_dates = _split_date_items(controls.get("closed_dates_text", ""))
    if closed_dates:
        calendar["enable_holidays_off"] = True
        calendar["holidays_off"] = closed_dates

    work_dates = _split_date_items(controls.get("work_dates_text", ""))
    if work_dates:
        calendar["enable_work_dates"] = True
        calendar["work_dates"] = work_dates

    morning_only_dates = _split_date_items(controls.get("morning_only_dates_text", ""))
    if morning_only_dates:
        calendar["enable_afternoon_shutdown"] = True
        calendar["afternoon_shutdown_dates"] = morning_only_dates
        calendar["afternoon_shutdown"] = controls.get("afternoon_shutdown", "15:00-08:00")

    afternoon_only_dates = _split_date_items(controls.get("afternoon_only_dates_text", ""))
    if afternoon_only_dates:
        calendar["enable_morning_shutdown"] = True
        calendar["morning_shutdown_dates"] = afternoon_only_dates
        calendar["morning_shutdown"] = controls.get("morning_shutdown", "08:00-12:00")

    if controls.get("enable_lunch_break"):
        calendar["enable_lunch_break"] = True
        calendar["lunch_break"] = controls.get("lunch_break", "12:00-13:00")

    return {"calendar": calendar} if calendar else {}


def _runtime_config_json_from_controls(controls: Dict[str, Any]) -> Optional[str]:
    runtime_config = _build_runtime_config_from_controls(controls)
    if not runtime_config:
        return None
    return json.dumps(runtime_config, ensure_ascii=False)


def _llm_subprocess_env_from_controls(controls: Dict[str, Any]) -> Dict[str, str]:
    """[AGENT-ADD] Pass LLM secrets through environment, not command arguments."""
    env: Dict[str, str] = {}
    parser_mode = str(controls.get("parser_mode") or "deterministic")
    provider = str(controls.get("llm_provider") or "")
    model = str(controls.get("llm_model") or "").strip()
    base_url = str(controls.get("llm_base_url") or "").strip()
    api_key = str(controls.get("llm_api_key") or "").strip()

    if parser_mode in {"deterministic", "rule", "rules"}:
        return env
    if provider:
        env["PBS_LLM_PROVIDER"] = provider
    if model:
        env["PBS_LLM_MODEL"] = model
        if provider == "groq":
            env["GROQ_MODEL"] = model
        elif provider == "gemini":
            env["GEMINI_MODEL"] = model
        elif provider == "ollama":
            env["OLLAMA_MODEL"] = model
        elif provider == "openai":
            env["OPENAI_MODEL"] = model
    if base_url:
        env["PBS_LLM_BASE_URL"] = base_url
    if api_key:
        if provider == "groq":
            env["GROQ_API_KEY"] = api_key
        elif provider == "gemini":
            env["GEMINI_API_KEY"] = api_key
        elif provider == "openai":
            env["OPENAI_API_KEY"] = api_key
        else:
            env["PBS_LLM_API_KEY_ENV"] = "PBS_LLM_API_KEY"
            env["PBS_LLM_API_KEY"] = api_key
    return env


def _run_subprocess(cmd: List[str], extra_env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )



def _load_existing_case(method: str, case_dir: Path) -> Dict[str, Any]:
    summary_path = case_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"샘플 summary가 없습니다: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    bundle = load_analysis_bundle(summary["analysis_bundle_json"])
    report_path = case_dir / "analysis_report.md"
    if not report_path.exists():
        save_analysis_report(bundle, report_path)
    return {
        "method": method,
        "summary": summary,
        "bundle": bundle,
        "report_path": str(report_path),
        "report_text": explain_analysis_bundle(bundle),
        "recommendation": None,
        "stdout": "",
        "stderr": "",
    }



def _load_sample_results(methods: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    selected_methods = set(methods or DEFAULT_VISIBLE_METHODS)
    for method, case_dir in SAMPLE_CASE_DIRS.items():
        if method not in selected_methods:
            continue
        if case_dir.exists():
            results[method] = _load_existing_case(method, case_dir)
    return results


def _run_reschedule_case(method: str, request_text: str, controls: Dict[str, Any], case_root: Path) -> Dict[str, Any]:
    case_dir = case_root / method
    case_dir.mkdir(parents=True, exist_ok=True)
    llm_env = _llm_subprocess_env_from_controls(controls)

    cmd = [
        sys.executable,
        str(RESCHEDULE_SCRIPT),
        "--excel-path", str(REPO_ROOT / controls["excel_path"]),
        "--sheet-name", controls["sheet_name"],
        "--method", method,
        "--request", request_text,
        "--output-dir", str(case_dir),
        "--date-offset", str(int(controls["date_offset"])),
        "--max-days", str(int(controls["max_days"])),
        "--save-detailed-process",
    ]
    parser_mode = str(controls.get("parser_mode") or "deterministic")
    if parser_mode != "deterministic":
        cmd.extend(["--parser", parser_mode])
        if controls.get("llm_provider"):
            cmd.extend(["--llm-provider", str(controls["llm_provider"])])
        if controls.get("llm_model"):
            cmd.extend(["--llm-model", str(controls["llm_model"])])
        if controls.get("llm_base_url"):
            cmd.extend(["--llm-base-url", str(controls["llm_base_url"])])
    runtime_config_json = _runtime_config_json_from_controls(controls)
    if runtime_config_json:
        cmd.extend(["--runtime-config-json", runtime_config_json])
    if controls.get("freeze_existing_prefix"):
        cmd.append("--freeze-existing-prefix")
    if method == "rl":
        rl_model_path = controls.get("rl_model_path", "").strip()
        if not rl_model_path:
            raise ValueError("RL을 선택했지만 모델 경로가 비어 있습니다.")
        cmd.extend([
            "--rl-model-path",
            str(REPO_ROOT / rl_model_path) if not Path(rl_model_path).is_absolute() else rl_model_path,
        ])

    completed = _run_subprocess(cmd, extra_env=llm_env)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"{method} rerun failed")

    summary_path = case_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    bundle = load_analysis_bundle(summary["analysis_bundle_json"])
    report_path = case_dir / "analysis_report.md"
    save_analysis_report(bundle, report_path)

    recommendation = None
    if controls.get("run_recommendation"):
        recommend_dir = case_root / f"{method}_recommendation"
        rec_cmd = [
            sys.executable,
            str(RECOMMEND_SCRIPT),
            "--excel-path", str(REPO_ROOT / controls["excel_path"]),
            "--sheet-name", controls["sheet_name"],
            "--method", method,
            "--request", request_text,
            "--output-dir", str(recommend_dir),
            "--date-offset", str(int(controls["date_offset"])),
            "--max-days", str(int(controls["max_days"])),
        ]
        if parser_mode != "deterministic":
            rec_cmd.extend(["--parser", parser_mode])
            if controls.get("llm_provider"):
                rec_cmd.extend(["--llm-provider", str(controls["llm_provider"])])
            if controls.get("llm_model"):
                rec_cmd.extend(["--llm-model", str(controls["llm_model"])])
            if controls.get("llm_base_url"):
                rec_cmd.extend(["--llm-base-url", str(controls["llm_base_url"])])
        if runtime_config_json:
            rec_cmd.extend(["--runtime-config-json", runtime_config_json])
        if controls.get("freeze_existing_prefix"):
            rec_cmd.append("--freeze-existing-prefix")
        if method == "rl":
            rl_model_path = controls.get("rl_model_path", "").strip()
            rec_cmd.extend([
                "--rl-model-path",
                str(REPO_ROOT / rl_model_path) if not Path(rl_model_path).is_absolute() else rl_model_path,
            ])
        rec_completed = _run_subprocess(rec_cmd, extra_env=llm_env)
        if rec_completed.returncode == 0:
            rec_path = recommend_dir / "recommendation_summary.json"
            recommendation = json.loads(rec_path.read_text(encoding="utf-8"))
        else:
            recommendation = {
                "error": rec_completed.stderr.strip() or rec_completed.stdout.strip() or "recommendation failed"
            }

    return {
        "method": method,
        "summary": summary,
        "bundle": bundle,
        "report_path": str(report_path),
        "report_text": explain_analysis_bundle(bundle),
        "recommendation": recommendation,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }



def _comparison_rows(results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for method, payload in results.items():
        bundle = payload["bundle"]
        before = bundle["before"]
        after = bundle["after"]
        delta = bundle["delta"]
        rows.append({
            "method": method.upper(),
            "before_makespan_h": round(float(before["makespan_hours"]), 2),
            "after_makespan_h": round(float(after["makespan_hours"]), 2),
            "delta_makespan_h": round(float(delta["makespan_hours"]), 2),
            "before_primary": int(before["primary_violations"]),
            "after_primary": int(after["primary_violations"]),
            "delta_primary": int(delta["primary_violations"]),
            "before_raw": int(before["raw_violations"] or 0),
            "after_raw": int(after["raw_violations"] or 0),
            "delta_raw": int(delta["raw_violations"] or 0),
            "sequence_head_after": " -> ".join(str(v) for v in after.get("sequence_head", [])[:8]),
        })
    return rows



def _assistant_summary(request_text: str, results: Dict[str, Dict[str, Any]]) -> str:
    if not results:
        return "실행 결과가 없습니다."
    lines = [f"요청을 실행했습니다. `{request_text}`"]
    for row in _comparison_rows(results):
        lines.append(
            f"- {row['method']}: makespan {row['before_makespan_h']}h -> {row['after_makespan_h']}h, "
            f"primary {row['before_primary']} -> {row['after_primary']}, raw {row['before_raw']} -> {row['after_raw']}"
        )
    return "\n".join(lines)



def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        return int(float(value))
    except Exception:
        return default


CONSTRAINT_DISPLAY_NAMES = {
    "ROUTING_C_SEAM_SPACING": "C/Seam 간격",
    "ROUTING_CURVED_SPACING": "곡판 간격",
    "ROUTING_HIGH_SEAM_SPACING": "고심수 간격",
    "ROUTING_WORKSHOP_ORDER": "작업장 조립착수일 순서",
    "LINE_GROUP_CONSTRAINT": "라인 그룹 연속 제한",
    "P5#8": "평일 심수 용량",
    "P5#9": "72심 초과 시 17블록 조건",
    "P5#10": "주말 심수 용량",
    "P5#13": "자재 미입고 금지",
    "P5#15": "명절 전날 제약",
    "P5#16": "혹서기 용량",
    "P5#17": "별판 통합 처리",
    "P7#1": "론지 A/B 부하 균형",
    "P7#2": "폭 21m 초과 블록 B베이",
    "P7#3": "작은 P/S 쌍 동일 베이",
    "P7#4": "날짜 조건 P/S 동일 베이",
    "P7#7": "베이 연속 배치 제한",
    "P7#8": "주판 Only 연속 제한",
    "P7#10": "론지 30개 이상 A베이 우선",
    "P7#12": "LT강재 A베이 우선",
}


def _parse_listish(value: Any) -> List[Any]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if not text or text.lower() == "nan" or text == "[]":
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return [text]


def _constraint_label(constraint_id: Any) -> str:
    cid = str(constraint_id or "").strip()
    if not cid:
        return "-"
    label = CONSTRAINT_DISPLAY_NAMES.get(cid)
    return f"{cid} ({label})" if label else cid


def _humanize_selection_reason(value: Any) -> str:
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    replacements = {
        "[EMERGENCY] 기본 통과": "리드타임 초과 후보 - runtime 후보검사 통과",
        "[URGENT] 기본 통과": "리드타임 임박 후보 - runtime 후보검사 통과",
        "[NORMAL] 기본 통과": "일반 후보 - runtime 후보검사 통과",
        "interactive prefix override": "사용자 요청 위치 강제 적용",
        "FORCED_PREFIX_OVERRIDE": "사용자 요청 위치 강제 적용",
        "FORCED_PREFIX_AVAILABLE": "사용자 요청 prefix 후보",
    }
    for raw, display in replacements.items():
        text = text.replace(raw, display)
    return text


def _humanize_trace_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    display_df = df.copy()
    for col in ["selection_reason", "selected_inclusion_reason", "before_reason", "after_reason"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(_humanize_selection_reason)
    return display_df


def _primary_violation_rows(payload: Dict[str, Any]) -> pd.DataFrame:
    summary = payload.get("summary", {})
    after_csv = summary.get("after_csv")
    if not after_csv or not Path(after_csv).exists():
        return pd.DataFrame()
    df = load_csv(after_csv)
    if df.empty or "violations_primary_count" not in df.columns:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        primary_count = _safe_int(row.get("violations_primary_count"), 0)
        if primary_count <= 0:
            continue
        constraint_ids = [_constraint_label(cid) for cid in _parse_listish(row.get("constraint_ids"))]
        details = [str(item) for item in _parse_listish(row.get("violation_details"))]
        rows.append({
            "seq": _safe_int(row.get("am_sequence")),
            "block_id": _safe_int(row.get("block_id")),
            "block_name": row.get("block_name", ""),
            "bay": row.get("assigned_bay", ""),
            "panel_start_time": row.get("panel_start_time", ""),
            "final_end_time": row.get("final_end_time", ""),
            "primary_count": primary_count,
            "constraint": ", ".join(constraint_ids) if constraint_ids else "-",
            "detail": ", ".join(details) if details else "-",
        })
    return pd.DataFrame(rows)


def _primary_breakdown_df(payload: Dict[str, Any]) -> pd.DataFrame:
    violation_rows = _primary_violation_rows(payload)
    if violation_rows.empty:
        return pd.DataFrame(columns=["constraint", "count"])
    counts: Dict[str, int] = {}
    for _, row in violation_rows.iterrows():
        for cid in str(row.get("constraint", "")).split(","):
            key = cid.strip()
            if key and key != "-":
                counts[key] = counts.get(key, 0) + _safe_int(row.get("primary_count"), 1)
    return pd.DataFrame([{"constraint": key, "count": value} for key, value in counts.items()])



def _date_key(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if "." in text:
        text = text.split(".")[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits or text



def _date_label(value: Any) -> str:
    digits = _date_key(value)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return digits or "-"



def _datetime_label(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    return str(value)



def _extract_sequence(df: pd.DataFrame) -> List[int]:
    if df.empty or "am_sequence" not in df.columns or "block_id" not in df.columns:
        return []
    ordered = df.sort_values("am_sequence")
    return [_safe_int(v) for v in ordered["block_id"].tolist() if pd.notna(v)]



def _build_block_lookup(df: pd.DataFrame) -> Dict[int, Dict[str, Any]]:
    lookup: Dict[int, Dict[str, Any]] = {}
    if df.empty or "block_id" not in df.columns:
        return lookup
    ordered = df.sort_values("am_sequence") if "am_sequence" in df.columns else df
    for _, row in ordered.iterrows():
        block_id = row.get("block_id")
        if pd.isna(block_id):
            continue
        key = _safe_int(block_id)
        if key in lookup:
            continue
        lookup[key] = {
            "name": str(row.get("block_name", "")).strip() or f"BLK_{key}",
            "bay": str(row.get("assigned_bay", "")).strip() or "-",
            "date": _date_key(row.get("date")),
            "seam": _safe_int(row.get("seam_count")),
        }
    return lookup



def _format_block_tokens(block_ids: List[int], lookup: Dict[int, Dict[str, Any]], limit: int = 8) -> List[str]:
    tokens: List[str] = []
    for block_id in block_ids[:limit]:
        meta = lookup.get(_safe_int(block_id), {})
        name = meta.get("name", f"BLK_{block_id}")
        bay = meta.get("bay", "-")
        tokens.append(f"{block_id} · {name} ({bay})")
    return tokens



def _count_delayed_blocks(df: pd.DataFrame) -> int:
    if df.empty or "date" not in df.columns or "block_assembly_date" not in df.columns:
        return 0
    delayed = 0
    for _, row in df.iterrows():
        scheduled = _date_key(row.get("date"))
        target = _date_key(row.get("block_assembly_date"))
        if scheduled and target and scheduled > target:
            delayed += 1
    return delayed



def _pick_trace_row(trace_df: pd.DataFrame, step: int) -> Dict[str, Any]:
    if trace_df.empty or "step" not in trace_df.columns:
        return {}
    match = trace_df.loc[trace_df["step"] == step]
    if not match.empty:
        return match.iloc[0].to_dict()
    if step > 0 and step <= len(trace_df):
        return trace_df.iloc[step - 1].to_dict()
    return trace_df.iloc[0].to_dict()



def _derive_reference_state(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = payload["summary"]
    bundle = payload["bundle"]

    before_df = load_csv(summary.get("before_csv"))
    after_df = load_csv(summary.get("after_csv"))
    before_trace_df = load_csv(summary.get("before_decision_trace_csv"))
    after_trace_df = load_csv(summary.get("after_decision_trace_csv"))

    before_sequence = _extract_sequence(before_df)
    after_sequence = _extract_sequence(after_df)
    before_lookup = _build_block_lookup(before_df)
    after_lookup = _build_block_lookup(after_df)

    forced_steps = bundle.get("trace", {}).get("forced_steps_after", [])
    forced_step_values = sorted(_safe_int(item.get("step"), 0) for item in forced_steps if item.get("step") is not None)
    first_forced_step = forced_step_values[0] if forced_step_values else 0
    committed_prefix_len = max(first_forced_step - 1, 0) if first_forced_step else 0
    editable_start_step = committed_prefix_len + 1 if before_sequence else 1
    committed_blocks = before_sequence[:committed_prefix_len]

    reference_row = _pick_trace_row(before_trace_df, editable_start_step) or _pick_trace_row(after_trace_df, editable_start_step)
    reference_date_key = _date_key(reference_row.get("current_date"))
    if not reference_date_key and not before_df.empty and "date" in before_df.columns:
        reference_date_key = _date_key(before_df.iloc[0]["date"])

    today_before_df = before_df.loc[before_df["date"].map(_date_key) == reference_date_key] if reference_date_key and not before_df.empty and "date" in before_df.columns else pd.DataFrame()
    today_after_df = after_df.loc[after_df["date"].map(_date_key) == reference_date_key] if reference_date_key and not after_df.empty and "date" in after_df.columns else pd.DataFrame()

    before_stats = summary.get("before_stats", {})
    total_expected = _safe_int(before_stats.get("total_blocks_expected"), len(before_sequence))
    total_processed = _safe_int(before_stats.get("total_blocks_processed"), len(before_sequence))

    next_before_ids = before_sequence[committed_prefix_len:committed_prefix_len + 5]
    next_after_ids = after_sequence[committed_prefix_len:committed_prefix_len + 5]

    return {
        "reference_datetime": _datetime_label(reference_row.get("current_datetime")),
        "reference_date": _date_label(reference_date_key),
        "reference_date_key": reference_date_key,
        "committed_prefix_len": committed_prefix_len,
        "committed_blocks": _format_block_tokens(committed_blocks, before_lookup, limit=12),
        "editable_start_step": editable_start_step,
        "next_before_blocks": _format_block_tokens(next_before_ids, before_lookup),
        "next_after_blocks": _format_block_tokens(next_after_ids, after_lookup),
        "today_before_blocks": int(len(today_before_df)),
        "today_after_blocks": int(len(today_after_df)),
        "today_before_seam": int(today_before_df["seam_count"].sum()) if not today_before_df.empty and "seam_count" in today_before_df.columns else 0,
        "today_after_seam": int(today_after_df["seam_count"].sum()) if not today_after_df.empty and "seam_count" in today_after_df.columns else 0,
        "delayed_before": _count_delayed_blocks(before_df),
        "delayed_after": _count_delayed_blocks(after_df),
        "total_expected": total_expected,
        "total_processed": total_processed,
        "remaining_after_prefix": max(total_expected - committed_prefix_len, 0),
    }



def _render_chip_list(items: List[str], empty_text: str = "없음") -> None:
    if not items:
        st.markdown(f"<div class='small-note'>{empty_text}</div>", unsafe_allow_html=True)
        return
    html = "".join(f"<span class='block-chip'>{item}</span>" for item in items)
    st.markdown(html, unsafe_allow_html=True)


def _method_css_class(method: str) -> str:
    return f"method-{method.lower()}"



def _lane_block_tokens(payload: Dict[str, Any], bay: str, limit: int = 6) -> List[Dict[str, Any]]:
    summary = payload["summary"]
    state = _derive_reference_state(payload)
    after_df = load_csv(summary.get("after_csv"))
    if after_df.empty:
        return []
    reference_key = state.get("reference_date_key", "")
    if reference_key and "date" in after_df.columns:
        scoped_df = after_df.loc[after_df["date"].map(_date_key) == reference_key].copy()
        if scoped_df.empty:
            scoped_df = after_df.copy()
    else:
        scoped_df = after_df.copy()
    scoped_df = scoped_df.sort_values("am_sequence")
    if "assigned_bay" in scoped_df.columns:
        scoped_df = scoped_df.loc[scoped_df["assigned_bay"].astype(str).str.strip() == bay]
    committed_ids = set()
    base_df = load_csv(summary.get("before_csv"))
    if not base_df.empty:
        committed_ids = set(_extract_sequence(base_df)[: state.get("committed_prefix_len", 0)])
    rows: List[Dict[str, Any]] = []
    for _, row in scoped_df.head(limit).iterrows():
        block_id = _safe_int(row.get("block_id"))
        delayed = False
        scheduled = _date_key(row.get("date"))
        target = _date_key(row.get("block_assembly_date"))
        if scheduled and target and scheduled > target:
            delayed = True
        rows.append({
            "block_id": block_id,
            "name": str(row.get("block_name", f"BLK_{block_id}")),
            "seam": _safe_int(row.get("seam_count")),
            "bay": bay,
            "delayed": delayed,
            "forced": block_id in committed_ids,
        })
    return rows



def _render_lane_box(method: str, bay: str, blocks: List[Dict[str, Any]]) -> None:
    if not blocks:
        html = f"<div class='lane-box'><div class='lane-title'>{bay}</div><div class='small-note'>배치 블록 없음</div></div>"
        st.markdown(html, unsafe_allow_html=True)
        return
    items = []
    for block in blocks:
        classes = ["lane-block", _method_css_class(method)]
        if block.get("delayed"):
            classes.append("delayed")
        if block.get("forced"):
            classes.append("forced")
        items.append(
            f"<div class='{' '.join(classes)}'>"
            f"<div><b>{block['block_id']}</b></div>"
            f"<div>{block['name']}</div>"
            f"<div>심수 {block['seam']}</div>"
            f"</div>"
        )
    html = (
        f"<div class='lane-box'>"
        f"<div class='lane-title'>{bay}</div>"
        f"<div class='lane-track'>{''.join(items)}</div>"
        f"</div>"
    )
    st.markdown(html, unsafe_allow_html=True)



PROCESS_STAGE_NAMES = {
    1: "판계",
    2: "전면SAW",
    3: "TurnOver",
    4: "후면SAW",
    5: "NC",
    6: "론지취부",
    7: "론지용접",
    8: "수정",
}

PROCESS_CSV_PREFIX = "detailed_assembly_decoding_schedule_processes_"



def _process_csv_paths(summary: Dict[str, Any], phase: str = "after") -> List[str]:
    """Return detailed per-process CSVs produced by the scheduler, if available."""
    explicit = summary.get(f"{phase}_process_csvs") or []
    paths = [str(path) for path in explicit if path and Path(path).exists()]
    if paths:
        return sorted(paths)

    case_dir = Path(summary.get(f"{phase}_csv", "")).parent
    if not case_dir.exists():
        return []
    patterns = [
        f"{phase}_{PROCESS_CSV_PREFIX}*.csv",
        f"{PROCESS_CSV_PREFIX}*.csv",
    ]
    found: List[str] = []
    for pattern in patterns:
        found.extend(str(path) for path in case_dir.glob(pattern) if path.exists())
    return sorted(set(found))


def _timeline_from_process_csvs(paths: List[str], reference_key: str, limit: int) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for path in paths:
        df = load_csv(path)
        if df.empty:
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame()

    process_df = pd.concat(frames, ignore_index=True)
    if reference_key and "date" in process_df.columns:
        scoped = process_df.loc[process_df["date"].map(_date_key) == reference_key].copy()
        if not scoped.empty:
            process_df = scoped

    process_df["start"] = pd.to_datetime(process_df.get("start_time"), errors="coerce")
    process_df["end"] = pd.to_datetime(process_df.get("end_time"), errors="coerce")
    process_df = process_df.dropna(subset=["start", "end"]).copy()
    if process_df.empty:
        return pd.DataFrame()

    process_df = assign_machine_line(process_df)
    if "sequence" in process_df.columns:
        sequence_values = pd.to_numeric(process_df["sequence"], errors="coerce")
        keep_sequences = sorted(sequence_values.dropna().unique().tolist())[:limit]
        process_df = process_df.loc[sequence_values.isin(keep_sequences)].copy()
    elif "block_id" in process_df.columns:
        keep_blocks = process_df["block_id"].drop_duplicates().head(limit).tolist()
        process_df = process_df.loc[process_df["block_id"].isin(keep_blocks)].copy()

    process_df["block_id"] = process_df["block_id"].map(lambda value: _safe_int(value))
    process_df["block_name"] = process_df.get("block_name", "").astype(str)
    process_df["assigned_bay"] = process_df.get("assigned_bay", "").fillna("").astype(str).replace("", "-").replace("nan", "-")
    process_df["duration_min"] = pd.to_numeric(process_df.get("duration_min", 0), errors="coerce").fillna(0).round(1)
    process_df["bar_label"] = process_df.apply(lambda row: f"{_safe_int(row.get('block_id'))}", axis=1)
    process_df["block_label"] = process_df.apply(
        lambda row: f"{_safe_int(row.get('block_id'))} · {row.get('block_name', '')}",
        axis=1,
    )
    process_df["source"] = "scheduler_detail"
    return process_df[[
        "block_id", "block_name", "block_label", "bar_label", "process_name", "machine_line",
        "assigned_bay", "start", "end", "duration_min", "source"
    ]]


def _timeline_from_block_csv(payload: Dict[str, Any], limit: int = 7) -> pd.DataFrame:
    """Fallback for older sample bundles that do not include detailed process CSVs."""
    summary = payload["summary"]
    state = _derive_reference_state(payload)
    after_df = load_csv(summary.get("after_csv"))
    if after_df.empty:
        return pd.DataFrame()

    reference_key = state.get("reference_date_key", "")
    scoped_df = after_df.copy()
    if reference_key and "date" in scoped_df.columns:
        date_df = scoped_df.loc[scoped_df["date"].map(_date_key) == reference_key].copy()
        if not date_df.empty:
            scoped_df = date_df

    if "am_sequence" in scoped_df.columns:
        scoped_df = scoped_df.sort_values("am_sequence")

    rows: List[Dict[str, Any]] = []
    for _, row in scoped_df.head(limit).iterrows():
        block_id = _safe_int(row.get("block_id"))
        block_name = str(row.get("block_name", f"BLK_{block_id}"))
        bay = str(row.get("assigned_bay", "-")).strip() or "-"
        panel_start = pd.to_datetime(row.get("panel_start_time") or row.get("start_time"), errors="coerce")
        branch_start = pd.to_datetime(row.get("machine2_start_time"), errors="coerce")
        if pd.isna(panel_start):
            continue
        if pd.isna(branch_start):
            branch_start = panel_start

        common_cursor = panel_start
        branch_cursor = branch_start
        for idx in range(1, 9):
            minutes = float(row.get(f"process_{idx}_time_min", 0) or 0)
            if minutes <= 0:
                continue
            if idx <= 5:
                start = common_cursor
                end = start + pd.to_timedelta(minutes, unit="m")
                common_cursor = end
                process_name = PROCESS_STAGE_NAMES[idx]
                machine_line = process_name
                assigned_bay = ""
            else:
                start = branch_cursor
                end = start + pd.to_timedelta(minutes, unit="m")
                branch_cursor = end
                process_name = f"베이{bay} {PROCESS_STAGE_NAMES[idx]}"
                machine_line = f"베이A {PROCESS_STAGE_NAMES[idx]}" if bay == "35A" else f"베이B {PROCESS_STAGE_NAMES[idx]}"
                assigned_bay = bay
            rows.append({
                "block_id": block_id,
                "block_name": block_name,
                "block_label": f"{block_id} · {block_name}",
                "bar_label": str(block_id),
                "process_name": process_name,
                "machine_line": machine_line,
                "assigned_bay": assigned_bay,
                "start": start,
                "end": end,
                "duration_min": round(minutes, 1),
                "source": "block_csv_fallback",
            })
    return pd.DataFrame(rows)


def _process_timeline_df(payload: Dict[str, Any], limit: int = 7) -> pd.DataFrame:
    summary = payload["summary"]
    state = _derive_reference_state(payload)
    process_paths = _process_csv_paths(summary, phase="after")
    timeline = _timeline_from_process_csvs(process_paths, state.get("reference_date_key", ""), limit)
    if not timeline.empty:
        return timeline
    return _timeline_from_block_csv(payload, limit=limit)


def _render_process_timeline(payload: Dict[str, Any], method: str, height: int = 260, key_suffix: str = "board") -> None:
    # [AGENT-EDIT] Added key_suffix so multiple chart calls don't clash on Streamlit element IDs.
    timeline = _process_timeline_df(payload)
    if timeline.empty:
        st.caption("공정 타임라인을 만들 데이터가 없습니다.")
        return

    machine_order = list(create_machine_mapping().keys())
    # [AGENT-ADD] Inject zero-duration placeholder rows so every machine line appears on the y-axis.
    anchor_start = timeline["start"].min()
    placeholder_rows = []
    for machine_name in machine_order:
        if machine_name not in set(timeline["machine_line"].unique()):
            placeholder_rows.append({
                "block_id": -1,
                "block_name": "",
                "block_label": "__placeholder__",
                "bar_label": "",
                "process_name": machine_name,
                "machine_line": machine_name,
                "assigned_bay": "",
                "start": anchor_start,
                "end": anchor_start,
                "duration_min": 0.0,
                "source": timeline["source"].iloc[0] if "source" in timeline.columns else "",
            })
    if placeholder_rows:
        timeline = pd.concat([timeline, pd.DataFrame(placeholder_rows)], ignore_index=True)

    source = str(timeline.loc[timeline["block_label"] != "__placeholder__", "source"].iloc[0]) if (timeline["block_label"] != "__placeholder__").any() else ""
    fig = px.timeline(
        timeline,
        x_start="start",
        x_end="end",
        y="machine_line",
        color="block_label",
        text="bar_label",
        hover_data={
            "block_id": True,
            "block_name": True,
            "process_name": True,
            "assigned_bay": True,
            "duration_min": True,
            "block_label": False,
            "bar_label": False,
            "machine_line": False,
        },
        category_orders={"machine_line": machine_order},
    )
    # [AGENT-EDIT] Hide placeholder traces from legend and bars while keeping the y-axis rows.
    for trace in fig.data:
        if getattr(trace, "name", "") == "__placeholder__":
            trace.showlegend = False
            trace.opacity = 0.0
            trace.hoverinfo = "skip"
    fig.update_yaxes(
        autorange="reversed",
        title=None,
        categoryorder="array",
        categoryarray=machine_order,
        tickfont=dict(size=11),
        automargin=True,
    )
    fig.update_xaxes(title=None, showgrid=True)
    fig.update_traces(textposition="inside", insidetextanchor="middle", textfont_size=10)
    fig.update_layout(
        height=height,
        margin=dict(l=120, r=6, t=8, b=8),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        font=dict(size=10),
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        key=f"process_timeline_{method}_{key_suffix}",
    )
    if source == "block_csv_fallback":
        st.caption("상세 공정 CSV가 없는 저장 샘플이라 블록 CSV의 공정시간으로 보조 재구성했습니다. 새 실행 결과는 기존 Gantt 상세 CSV 기준으로 표시됩니다.")
    else:
        st.caption("기존 Gantt 상세 공정 CSV 기준입니다.")


# [AGENT-ADD] Pipeline stage layout shared by the flow diagram and current-block banner.
FLOW_COMMON_STAGES = ["판계", "전면SAW", "TurnOver", "후면SAW", "NC"]
FLOW_BAY_A_STAGES = ["베이A 론지취부", "베이A 론지용접", "베이A 수정"]
FLOW_BAY_B_STAGES = ["베이B 론지취부", "베이B 론지용접", "베이B 수정"]
FLOW_ALL_STAGES = FLOW_COMMON_STAGES + FLOW_BAY_A_STAGES + FLOW_BAY_B_STAGES

METHOD_COLORS = {
    "lpt": "#2563eb",
    "spt": "#16a34a",
    "rl": "#ea580c",
}


def _current_block_status(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the active or next process at the decision reference time."""
    timeline = _process_timeline_df(payload, limit=60)
    if timeline.empty:
        return None

    timeline = timeline.loc[timeline.get("block_label", "") != "__placeholder__"].copy()
    if timeline.empty:
        return None

    state = _derive_reference_state(payload)
    reference_time = pd.to_datetime(state.get("reference_datetime"), errors="coerce")
    if pd.isna(reference_time):
        reference_time = timeline["start"].min()

    active = timeline.loc[(timeline["start"] <= reference_time) & (timeline["end"] > reference_time)].copy()
    status_label = "진행중"
    if active.empty:
        active = timeline.loc[timeline["start"] >= reference_time].copy()
        status_label = "다음 예정"
    if active.empty:
        active = timeline.copy()
        status_label = "최종 완료"

    row = active.sort_values(["start", "end"]).iloc[0]
    block_id = _safe_int(row.get("block_id"))
    block_name = str(row.get("block_name", "")).strip() or f"BLK_{block_id}"
    stage_name = str(row.get("process_name", "")).strip() or "-"
    machine_line = str(row.get("machine_line", "")).strip() or stage_name
    assigned_bay_raw = row.get("assigned_bay", "")
    assigned_bay = "-" if pd.isna(assigned_bay_raw) else str(assigned_bay_raw).strip()
    if not assigned_bay or assigned_bay.lower() == "nan":
        assigned_bay = "-"
    start_time = pd.to_datetime(row.get("start"), errors="coerce")
    end_time = pd.to_datetime(row.get("end"), errors="coerce")
    start_label = "-" if pd.isna(start_time) else start_time.strftime("%m-%d %H:%M")
    end_label = "-" if pd.isna(end_time) else end_time.strftime("%m-%d %H:%M")
    ref_label = "-" if pd.isna(reference_time) else reference_time.strftime("%m-%d %H:%M")
    return {
        "block_id": block_id,
        "block_name": block_name,
        "stage": stage_name,
        "machine_line": machine_line,
        "assigned_bay": assigned_bay,
        "start_label": start_label,
        "end_label": end_label,
        "reference_label": ref_label,
        "status_label": status_label,
    }


def _render_current_block_banner(results: Dict[str, Dict[str, Any]]) -> None:
    """Top-of-page banner: which block is each method currently working on, and where."""
    if not results:
        return
    ordered_methods = [method for method in METHOD_ORDER if method in results]
    if not ordered_methods:
        ordered_methods = list(results.keys())
    cards: List[str] = []
    for method in ordered_methods:
        status = _current_block_status(results[method])
        color = METHOD_COLORS.get(method, "#475569")
        if status is None:
            body = "<div class='current-card-empty'>공정 진행 데이터 없음</div>"
        else:
            body = (
                f"<div class='current-card-block'>블록 <b>{status['block_id']}</b> · {status['block_name']}</div>"
                f"<div class='current-card-stage'>{status['status_label']} <b>{status['machine_line']}</b></div>"
                f"<div class='current-card-meta'>기준 {status['reference_label']} · {status['start_label']}~{status['end_label']} · 베이 {status['assigned_bay']}</div>"
            )
        cards.append(
            f"<div class='current-card' style='border-color:{color};'>"
            f"<div class='current-card-method' style='background:{color};'>{method.upper()}</div>"
            f"{body}"
            "</div>"
        )
    st.markdown(
        "<div class='current-card-title'>현재 진행중 블록 & 공정 위치</div>"
        f"<div class='current-card-row'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


def _stage_bay(stage: str) -> str:
    if stage in FLOW_BAY_A_STAGES:
        return "A"
    if stage in FLOW_BAY_B_STAGES:
        return "B"
    return "common"


def _blocks_at_stages(results: Dict[str, Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """기준 시점에 각 stage에 머무는 블록들과 경과(작업한) 시간을 반환.
    [AGENT-EDIT] method 라벨 제거 — 첫 번째 이용 가능한 method의 timeline을 단일 기준으로 사용.
    """
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in FLOW_ALL_STAGES}
    if not results:
        return out
    ordered = [m for m in METHOD_ORDER if m in results] or list(results.keys())
    if not ordered:
        return out
    ref_method = ordered[0]
    payload = results[ref_method]
    timeline = _process_timeline_df(payload, limit=120)
    if timeline.empty:
        return out
    timeline = timeline.loc[timeline.get("block_label", "") != "__placeholder__"].copy()
    if timeline.empty:
        return out
    state = _derive_reference_state(payload)
    reference_time = pd.to_datetime(state.get("reference_datetime"), errors="coerce")
    if pd.isna(reference_time):
        reference_time = timeline["start"].min()
    active = timeline.loc[(timeline["start"] <= reference_time) & (timeline["end"] > reference_time)].copy()
    for _, row in active.iterrows():
        stage = str(row.get("machine_line", "")).strip()
        if stage not in out:
            continue
        start_time = pd.to_datetime(row.get("start"), errors="coerce")
        elapsed_h = 0.0
        if pd.notna(start_time):
            elapsed_h = max(0.0, (reference_time - start_time).total_seconds() / 3600.0)
        block_id = _safe_int(row.get("block_id"))
        out[stage].append({
            "block_id": block_id,
            "block_name": str(row.get("block_name", "")).strip() or f"BLK_{block_id}",
            "elapsed_h": elapsed_h,
            "start_label": "-" if pd.isna(start_time) else start_time.strftime("%m-%d %H:%M"),
        })
    return out


def _render_pipeline_flow(results: Dict[str, Dict[str, Any]]) -> None:
    """[AGENT-EDIT] 조립 공정 단계 스타일 - 깨끗한 체인 다이어그램.
    - 공장 캔버스(벨트/야드 그림) 제거.
    - 한 행: 공통 5개 체인 → 분기점 → 35A / 36B 각각의 3개 체인.
    - 각 칩에 현재 머무는 블록 id + 경과 시간(h). method 라벨 없음.
    """
    if not results:
        return
    blocks_at = _blocks_at_stages(results)

    def _chip_html(stage: str) -> str:
        blocks_here = blocks_at.get(stage, [])
        active_class = "pflow-chip-active" if blocks_here else ""
        if blocks_here:
            rows = "".join(
                f"<div class='pflow-chip-row'>"
                f"<span class='pflow-chip-bid'>#{b['block_id']}</span>"
                f"<span class='pflow-chip-elapsed'>{b['elapsed_h']:.1f}h</span>"
                f"</div>"
                for b in blocks_here[:3]
            )
            body = f"<div class='pflow-chip-body'>{rows}</div>"
        else:
            body = "<div class='pflow-chip-body pflow-chip-empty'>—</div>"
        return (
            f"<div class='pflow-chip {active_class}'>"
            f"<div class='pflow-chip-name'>{stage}</div>"
            f"{body}"
            "</div>"
        )

    arrow = "<div class='pflow-arrow'>→</div>"
    common_html = arrow.join(_chip_html(s) for s in FLOW_COMMON_STAGES)
    bay_a_html = arrow.join(_chip_html(s) for s in FLOW_BAY_A_STAGES)
    bay_b_html = arrow.join(_chip_html(s) for s in FLOW_BAY_B_STAGES)

    html = f"""
    <div class='pflow-wrap'>
      <div class='pflow-title'>조립 공정 단계 · 현재 블록 위치</div>
      <div class='pflow-sub'>공통 공정 후 35A / 36B 로 분기. 각 칩은 스테이션이며 해당 칩 내의 <b>#ID · 경과h</b> 는 지금 작업 중인 블록입니다.</div>
      <div class='pflow-row'>
        <div class='pflow-common'>{common_html}</div>
        <div class='pflow-split'>
          <div class='pflow-split-up'></div>
          <div class='pflow-split-down'></div>
        </div>
        <div class='pflow-branches'>
          <div class='pflow-branch pflow-branch-a'>
            <div class='pflow-branch-tag pflow-branch-tag-a'>35A</div>
            <div class='pflow-branch-chain'>{bay_a_html}</div>
          </div>
          <div class='pflow-branch pflow-branch-b'>
            <div class='pflow-branch-tag pflow-branch-tag-b'>36B</div>
            <div class='pflow-branch-chain'>{bay_b_html}</div>
          </div>
        </div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _reference_payload(results: Dict[str, Dict[str, Any]]) -> tuple[str, Dict[str, Any]]:
    for method in METHOD_ORDER:
        if method in results:
            return method, results[method]
    method = next(iter(results))
    return method, results[method]


def _render_metric_html(label: str, value: str) -> str:
    return (
        "<div class='status-metric'>"
        f"<div class='status-metric-label'>{label}</div>"
        f"<div class='status-metric-value'>{value}</div>"
        "</div>"
    )



def _sequence_head_text(sequence: List[Any], limit: int = 12) -> str:
    values = [str(_safe_int(value)) for value in sequence[:limit]]
    if len(sequence) > limit:
        values.append("...")
    return " -> ".join(values) if values else "-"



def _render_top_sequence_summary(results: Dict[str, Dict[str, Any]]) -> None:
    if not results:
        return
    title = "RL Baseline vs 요청 반영 결과" if list(results.keys()) == ["rl"] else "Baseline vs 요청 반영 결과"
    st.markdown(f"<div class='top-sequence-title'>{title}</div>", unsafe_allow_html=True)
    ordered_methods = [method for method in METHOD_ORDER if method in results]
    if not ordered_methods:
        ordered_methods = list(results.keys())
    cols = st.columns(len(ordered_methods))
    for col, method in zip(cols, ordered_methods):
        payload = results[method]
        bundle = payload["bundle"]
        before = bundle["before"]
        after = bundle["after"]
        delta = bundle["delta"]
        before_makespan = float(before.get("makespan_hours", 0.0))
        after_makespan = float(after.get("makespan_hours", 0.0))
        after_primary = _safe_int(after.get("primary_violations"), 0)
        after_raw = _safe_int(after.get("raw_violations"), 0)
        before_sequence_text = _sequence_head_text(before.get("sequence_head", []), limit=8)
        after_sequence_text = _sequence_head_text(after.get("sequence_head", []), limit=8)
        delta_ms = float(delta.get("makespan_hours", 0.0))
        delta_primary = _safe_int(delta.get("primary_violations"), 0)
        delta_raw = _safe_int(delta.get("raw_violations"), 0)
        html = f"""
        <div class='top-sequence-card'>
            <div class='top-sequence-method'>{method.upper()}</div>
            <div class='top-sequence-kpis'>
                <div class='top-kpi-box'><div class='top-kpi-label'>Before</div><div class='top-kpi-value'>{before_makespan:.2f}h</div></div>
                <div class='top-kpi-box'><div class='top-kpi-label'>After</div><div class='top-kpi-value'>{after_makespan:.2f}h</div></div>
                <div class='top-kpi-box'><div class='top-kpi-label'>After P/R</div><div class='top-kpi-value'>{after_primary}/{after_raw}</div></div>
            </div>
            <div class='small-note'>delta: makespan {delta_ms:+.2f}h / primary {delta_primary:+d} / raw {delta_raw:+d}</div>
            <div class='sequence-line'><b>Before</b><br>{before_sequence_text}</div>
            <div class='sequence-line'><b>After</b><br>{after_sequence_text}</div>
        </div>
        """
        with col:
            st.markdown(html, unsafe_allow_html=True)


def _render_status_panel(results: Dict[str, Dict[str, Any]]) -> None:
    ref_method, ref_payload = _reference_payload(results)
    state = _derive_reference_state(ref_payload)

    metrics_html = "".join([
        _render_metric_html("기준 방법", ref_method.upper()),
        _render_metric_html("기준 시점", state["reference_datetime"]),
        _render_metric_html("이미 확정된 블록", f"{state['committed_prefix_len']}개"),
        _render_metric_html("재조정 시작", f"{state['editable_start_step']}번째 step"),
        _render_metric_html("오늘 블록 수", f"{state['today_before_blocks']} → {state['today_after_blocks']}"),
        _render_metric_html("오늘 심수", f"{state['today_before_seam']} → {state['today_after_seam']}"),
        _render_metric_html("지연 블록", f"{state['delayed_before']} → {state['delayed_after']}"),
        _render_metric_html("남은 블록", f"{state['remaining_after_prefix']} / {state['total_expected']}"),
    ])

    st.markdown(
        f"""
        <div class='status-card'>
            <h4>현황판</h4>
            <div class='status-grid'>
                {metrics_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns(2)
    with left:
        st.markdown("#### 이미 현장 반영된 블록")
        _render_chip_list(state["committed_blocks"], "고정된 prefix가 없습니다.")
        st.caption("실시간 작업중 대신, 현재 기준에서 이미 확정된 prefix를 보여줍니다.")
    with right:
        st.markdown("#### 재조정 직전 기본 예정")
        _render_chip_list(state["next_before_blocks"], "예정 블록이 없습니다.")
        st.markdown("#### 요청 반영 후 다음 예정")
        _render_chip_list(state["next_after_blocks"], "변경 예정 블록이 없습니다.")



def _render_method_boards(results: Dict[str, Dict[str, Any]]) -> None:
    ordered_methods = [method for method in METHOD_ORDER if method in results]
    if not ordered_methods:
        ordered_methods = list(results.keys())
    columns = st.columns(len(ordered_methods)) if ordered_methods else []
    for col, method in zip(columns, ordered_methods):
        payload = results[method]
        bundle = payload["bundle"]
        summary = payload["summary"]
        state = _derive_reference_state(payload)
        delta = bundle["delta"]
        top_constraints = bundle["after"].get("top_constraints", {})
        top_constraint_text = ", ".join(
            f"{_constraint_label(key)} {value}건" for key, value in list(top_constraints.items())[:2]
        ) or "-"
        primary_breakdown = _primary_breakdown_df(payload)
        primary_breakdown_text = "; ".join(
            f"{row['constraint']} {int(row['count'])}건"
            for _, row in primary_breakdown.iterrows()
        ) or "없음"
        moved = bundle["delta"].get("top_moved_blocks", [])[:3]
        moved_text = ", ".join(
            f"{item['block_id']}({item['before_position']}→{item['after_position']})" for item in moved
        ) or "변화 적음"
        lane_35a = _lane_block_tokens(payload, "35A")
        lane_36b = _lane_block_tokens(payload, "36B")

        with col:
            with st.container(border=True):
                st.markdown(f"#### {method.upper()}")
                st.metric("Makespan", f"{bundle['after']['makespan_hours']:.2f}h", f"{delta['makespan_hours']:+.2f}h")
                k1, k2 = st.columns(2)
                k1.metric("Primary", f"{bundle['after']['primary_violations']}", f"{delta['primary_violations']:+d}")
                raw_delta = _safe_int(delta.get('raw_violations'), 0)
                k2.metric("Raw", f"{_safe_int(bundle['after'].get('raw_violations'), 0)}", f"{raw_delta:+d}")
                st.markdown(f"**오늘 계획**  블록 `{state['today_after_blocks']}개` / 심수 `{state['today_after_seam']}`")
                st.markdown(f"**다음 예정**  {', '.join(state['next_after_blocks'][:2]) if state['next_after_blocks'] else '-'}")
                _render_lane_box(method, "35A", lane_35a)
                _render_lane_box(method, "36B", lane_36b)
                st.markdown("**오늘 공정 흐름**")
                _render_process_timeline(payload, method, height=300, key_suffix=f"board_{method}")
                st.markdown(f"**대표 이동**  {moved_text}")
                st.markdown(f"**Primary 위반**  {primary_breakdown_text}")
                st.markdown(f"**상위 제약**  {top_constraint_text}")
                st.markdown(f"<div class='small-note'>결과 CSV: {Path(summary.get('after_csv', '')).name}</div>", unsafe_allow_html=True)



def _display_method_tab(method: str, payload: Dict[str, Any]) -> None:
    bundle = payload["bundle"]
    summary = payload["summary"]
    before = bundle["before"]
    after = bundle["after"]
    delta = bundle["delta"]
    state = _derive_reference_state(payload)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Makespan", f"{after['makespan_hours']:.2f}h", f"{delta['makespan_hours']:+.2f}h")
    c2.metric("Primary", f"{after['primary_violations']}", f"{delta['primary_violations']:+d}")
    raw_delta = _safe_int(delta.get('raw_violations'), 0)
    c3.metric("Raw", f"{_safe_int(after.get('raw_violations'), 0)}", f"{raw_delta:+d}")
    c4.metric("재조정 시작", f"{state['editable_start_step']}번째", f"고정 {state['committed_prefix_len']}개")

    st.markdown("### 해석")
    st.write(summary.get("explanation", "설명 없음"))

    top_left, top_right = st.columns(2)
    with top_left:
        st.markdown("#### Before 요약")
        st.json(before)
    with top_right:
        st.markdown("#### After 요약")
        st.json(after)

    violation_rows = _primary_violation_rows(payload)
    if not violation_rows.empty:
        st.markdown("#### Primary 위반 상세 (final audit)")
        st.dataframe(violation_rows, use_container_width=True, hide_index=True)
    else:
        st.markdown("#### Primary 위반 상세 (final audit)")
        st.success("Primary 위반이 없습니다.")

    st.markdown("#### 공정 타임라인")
    _render_process_timeline(payload, method, height=360, key_suffix="detail")

    moved = pd.DataFrame(delta.get("top_moved_blocks", []))
    if not moved.empty:
        st.markdown("#### 많이 이동한 블록")
        st.dataframe(moved, use_container_width=True)

    changed_steps = _humanize_trace_df(pd.DataFrame(bundle.get("trace", {}).get("changed_steps_preview", [])))
    if not changed_steps.empty:
        st.markdown("#### 변경된 초기 step")
        st.dataframe(changed_steps, use_container_width=True)

    trace_cols = st.columns(2)
    with trace_cols[0]:
        st.markdown("#### 현장 반영 prefix")
        _render_chip_list(state["committed_blocks"], "고정 prefix 없음")
    with trace_cols[1]:
        st.markdown("#### 요청 반영 후 다음 예정")
        _render_chip_list(state["next_after_blocks"], "예정 없음")

    after_trace_path = summary.get("after_decision_trace_csv")
    if after_trace_path and Path(after_trace_path).exists():
        trace_df = _humanize_trace_df(load_csv(after_trace_path))
        st.markdown("#### After decision trace 미리보기")
        st.dataframe(trace_df.head(20), use_container_width=True)

    recommendation = payload.get("recommendation")
    if recommendation:
        st.markdown("#### 권고안")
        if recommendation.get("error"):
            st.error(recommendation["error"])
        else:
            st.write(recommendation.get("recommendation_text", "권고안 없음"))
            st.json(recommendation.get("recommended", {}))

    with st.expander("Trace-grounded analysis report", expanded=False):
        st.markdown(payload["report_text"])

    with st.expander("파일 경로 / 디버그", expanded=False):
        st.json({
            "summary_json": str(Path(summary.get("after_csv", "")).parent / "summary.json"),
            "analysis_bundle_json": summary.get("analysis_bundle_json"),
            "after_csv": summary.get("after_csv"),
            "after_decision_trace_csv": summary.get("after_decision_trace_csv"),
            "report_path": payload.get("report_path"),
        })
        if payload.get("stderr"):
            st.code(payload["stderr"])



def _render_empty_state() -> None:
    st.markdown(
        """
        <div style='text-align:center; padding: 42px 24px 24px 24px;'>
            <div style='font-size:0.9rem; color:#64748b; font-weight:700; letter-spacing:0.08em;'>PBS INTERACTIVE SCHEDULER</div>
            <div style='font-size:2.3rem; font-weight:800; margin-top:10px; color:#0f172a;'>무엇을 다시 스케줄링할까요?</div>
            <div style='font-size:1rem; color:#64748b; margin-top:10px;'>왼쪽 설정은 접혀 있고, 오른쪽 GPT 패널에서 요청을 넣으면 RL 현황판과 결과 보드가 생성됩니다.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    cards = [
        ("긴급 투입", "11번 블록은 네 번째로 해줘", "앞 prefix를 유지하고 지정 위치부터 재스케줄링"),
        ("운영 제한", "11일날에는 15개 블록만 해야해. 최대.", "일일 처리량 제한을 걸고 makespan/위반 변화 확인"),
        ("제약 What-if", "C/Seam 간격은 runtime만 끄고 audit는 유지해줘", "생성 중 제약 완화와 final audit 결과 분리"),
    ]
    for col, (title, example, desc) in zip([c1, c2, c3], cards):
        with col:
            st.markdown(f"### {title}")
            st.code(example, language="text")
            st.caption(desc)
    st.markdown("---")
    st.markdown("#### 화면에 표시되는 핵심 정보")
    st.markdown("이미 확정된 prefix, 재조정 시작 step, 오늘 블록 수/심수, 다음 예정 블록, RL 결과, final audit 기반 설명을 한 화면에서 보여줍니다.")


def _init_state() -> None:
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("last_prompt", "")
    st.session_state.setdefault("latest_results", None)
    st.session_state.setdefault("latest_request_text", "")
    st.session_state.setdefault("draft_prompt", "")
    st.session_state.setdefault("sample_autoloaded", False)
    # [AGENT-ADD] New dashboard state.
    st.session_state.setdefault("selected_sequence", None)  # entry_id or (method, phase)
    st.session_state.setdefault("chatroom_sessions", [])   # [{"title": str, "time": str}]
    st.session_state.setdefault("active_chatroom_idx", 0)
    # [AGENT-ADD] Sequence-history refactor (request #4, #5).
    # 각 entry: {"entry_id", "round_idx", "method", "phase", "request_text", "created_at", "payload", "bundle"}
    st.session_state.setdefault("sequence_history", [])
    st.session_state.setdefault("user_request_made", False)   # 사용자가 요청을 제출했는가?
    st.session_state.setdefault("overlay_entry_ids", [])       # 그래프 overlay 선택 리스트
    st.session_state.setdefault("next_entry_seq", 0)
    st.session_state.setdefault("next_round_idx", 0)


# =========================================================================
# [AGENT-ADD] Dashboard layout helpers — matches Gemini image reference.
# =========================================================================

def _sequence_keys(results: Dict[str, Dict[str, Any]]) -> List[tuple[str, str]]:
    """(legacy) Return ordered (method, phase) keys for a single run. Kept for callers that
    still pass a `results` dict. 요청 전이면 (first_method, 'before') 하나만 반환."""
    ordered = [m for m in METHOD_ORDER if m in results]
    if not ordered:
        ordered = list(results.keys())
    keys: List[tuple[str, str]] = []
    if ordered:
        keys.append((ordered[0], "before"))
    if st.session_state.get("user_request_made", False):
        for m in ordered:
            keys.append((m, "after"))
    return keys


def _ensure_selected_sequence(results: Dict[str, Dict[str, Any]]) -> Optional[tuple[str, str]]:
    keys = _sequence_keys(results)
    if not keys:
        st.session_state.selected_sequence = None
        return None
    current = st.session_state.get("selected_sequence")
    if current not in keys:
        st.session_state.selected_sequence = keys[0]
    return st.session_state.selected_sequence


def _sequence_label(key: tuple[str, str], index: int) -> str:
    method, phase = key
    phase_text = "처음 시퀀스" if phase == "before" else "요청 반영 후"
    return f"시퀀스 리스트 {index + 1} — {method.upper()} · {phase_text}"


def _sequence_bundle(results: Dict[str, Dict[str, Any]], key: tuple[str, str]) -> Dict[str, Any]:
    method, phase = key
    bundle = results[method]["bundle"]
    return bundle.get(phase, {})


# =========================================================================
# [AGENT-ADD] Sequence history helpers (Request #4, #5).
# 매 요청을 "라운드"로 기록해 결과 비교 열에서 모두 선택·overlay 가능하게 함.
# =========================================================================

def _new_entry_id() -> str:
    seq = int(st.session_state.get("next_entry_seq", 0))
    st.session_state.next_entry_seq = seq + 1
    return f"seq_{seq}"


def _make_entry(round_idx: int, method: str, phase: str, request_text: str,
                payload: Dict[str, Any]) -> Dict[str, Any]:
    bundle = payload.get("bundle", {}).get(phase, {}) or {}
    return {
        "entry_id": _new_entry_id(),
        "round_idx": int(round_idx),
        "method": method,
        "phase": phase,
        "request_text": request_text,
        "created_at": datetime.now().strftime("%m-%d %H:%M"),
        "payload": payload,
        "bundle": bundle,
    }


def _ingest_initial_results(results: Dict[str, Dict[str, Any]]) -> None:
    """최초 auto-load / 샘플 결과 -> round 0 (baseline + 초기 after는 무시)."""
    if not results:
        return
    history: List[Dict[str, Any]] = st.session_state.get("sequence_history", [])
    # 이미 baseline entry가 있으면 skip (중복 방지).
    if any(e.get("round_idx") == 0 for e in history):
        return
    ordered = [m for m in METHOD_ORDER if m in results] or list(results.keys())
    if not ordered:
        return
    first_method = ordered[0]
    entry = _make_entry(0, first_method, "before", "", results[first_method])
    history.append(entry)
    st.session_state.sequence_history = history
    # 기본 선택: baseline.
    if not st.session_state.get("selected_sequence_id"):
        st.session_state.selected_sequence_id = entry["entry_id"]
    if not st.session_state.get("overlay_entry_ids"):
        st.session_state.overlay_entry_ids = [entry["entry_id"]]


def _ingest_request_results(results: Dict[str, Dict[str, Any]], request_text: str) -> List[str]:
    """사용자 요청 결과 -> 새 라운드로 추가. 추가된 entry_id 리스트 반환."""
    if not results:
        return []
    history: List[Dict[str, Any]] = st.session_state.get("sequence_history", [])
    # 첫 라운드에서 baseline이 비어있으면 한 번만 채운다.
    if not any(e.get("round_idx") == 0 for e in history):
        ordered_pre = [m for m in METHOD_ORDER if m in results] or list(results.keys())
        first_method_pre = ordered_pre[0]
        base_entry = _make_entry(0, first_method_pre, "before", "", results[first_method_pre])
        history.append(base_entry)
    round_idx = int(st.session_state.get("next_round_idx", 0)) + 1
    st.session_state.next_round_idx = round_idx
    added_ids: List[str] = []
    ordered = [m for m in METHOD_ORDER if m in results] or list(results.keys())
    for m in ordered:
        entry = _make_entry(round_idx, m, "after", request_text, results[m])
        history.append(entry)
        added_ids.append(entry["entry_id"])
    st.session_state.sequence_history = history
    st.session_state.user_request_made = True
    if added_ids:
        st.session_state.selected_sequence_id = added_ids[0]
        # overlay에 자동 추가
        overlay = list(st.session_state.get("overlay_entry_ids", []))
        for eid in added_ids:
            if eid not in overlay:
                overlay.append(eid)
        st.session_state.overlay_entry_ids = overlay
    return added_ids


def _history_entries() -> List[Dict[str, Any]]:
    return list(st.session_state.get("sequence_history", []) or [])


def _entry_by_id(entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not entry_id:
        return None
    for entry in _history_entries():
        if entry.get("entry_id") == entry_id:
            return entry
    return None


def _ensure_selected_entry() -> Optional[Dict[str, Any]]:
    entries = _history_entries()
    if not entries:
        st.session_state.selected_sequence_id = None
        return None
    current = st.session_state.get("selected_sequence_id")
    entry = _entry_by_id(current)
    if entry is None:
        entry = entries[0]
        st.session_state.selected_sequence_id = entry["entry_id"]
    return entry


def _entry_label(entry: Dict[str, Any], index: int) -> str:
    method = entry.get("method", "")
    phase = entry.get("phase", "")
    if phase == "before":
        phase_text = "처음 시퀀스"
    else:
        round_idx = entry.get("round_idx", 0)
        phase_text = f"요청 {round_idx} 반영 후"
    return f"시퀀스 리스트 {index + 1} — {method.upper()} · {phase_text}"


def _utilization_from_timeline(df: pd.DataFrame, machine_order: List[str]) -> float:
    """Rough utilization = mean(busy_minutes / total_span_minutes) across machines."""
    if df.empty:
        return 0.0
    span_start = df["start_datetime"].min()
    span_end = df["end_datetime"].max()
    total_minutes = max(1.0, (span_end - span_start).total_seconds() / 60.0)
    ratios: List[float] = []
    for machine in machine_order:
        rows = df.loc[df["machine_line"] == machine]
        if rows.empty:
            continue
        busy = ((rows["end_datetime"] - rows["start_datetime"]).dt.total_seconds() / 60.0).clip(lower=0).sum()
        ratios.append(min(1.0, busy / total_minutes))
    if not ratios:
        return 0.0
    return sum(ratios) / len(ratios)


def _gantt_df_for_payload(payload: Dict[str, Any], phase: str) -> pd.DataFrame:
    """Build a dataframe suitable for `create_enhanced_gantt_chart` (matplotlib)."""
    summary = payload["summary"]
    # Try to use the detailed process CSVs for the requested phase.
    paths = _process_csv_paths(summary, phase=phase)
    frames: List[pd.DataFrame] = []
    for path in paths:
        df = load_csv(path)
        if not df.empty:
            frames.append(df)
    if frames:
        raw = pd.concat(frames, ignore_index=True)
    else:
        # Fallback: the timeline reconstruction already handles the after phase only.
        if phase == "after":
            raw = _timeline_from_block_csv(payload, limit=60)
        else:
            return pd.DataFrame()

    df = raw.copy()
    if "start_time" in df.columns:
        df["start_datetime"] = pd.to_datetime(df["start_time"], errors="coerce")
    elif "start" in df.columns:
        df["start_datetime"] = pd.to_datetime(df["start"], errors="coerce")
    if "end_time" in df.columns:
        df["end_datetime"] = pd.to_datetime(df["end_time"], errors="coerce")
    elif "end" in df.columns:
        df["end_datetime"] = pd.to_datetime(df["end"], errors="coerce")
    df = df.dropna(subset=["start_datetime", "end_datetime"]).copy()
    if df.empty:
        return pd.DataFrame()

    if "machine_line" not in df.columns:
        df = assign_machine_line(df)
    else:
        df = assign_machine_line(df)
    if "block_name" not in df.columns:
        df["block_name"] = df.get("block_id", pd.Series(dtype=object)).astype(str).map(lambda x: f"BLK_{x}")
    if "assigned_bay" not in df.columns:
        df["assigned_bay"] = ""
    df["block_id"] = df["block_id"].map(_safe_int)
    return df[[
        "block_id", "block_name", "start_datetime", "end_datetime",
        "machine_line", "assigned_bay", "process_name",
    ]]


def _render_utils_gantt(results: Dict[str, Dict[str, Any]], selected: tuple[str, str]) -> None:
    method, phase = selected
    payload = results[method]
    df = _gantt_df_for_payload(payload, phase)
    if df.empty:
        st.caption("간트차트를 만들 상세 공정 데이터가 없습니다.")
        return
    phase_text = "처음 시퀀스" if phase == "before" else "요청 반영 후"
    title = f"{method.upper()} · {phase_text} — 공정별 간트차트"
    fig = create_enhanced_gantt_chart(df, method.upper(), title, save_path=None, return_fig=True)
    if fig is None:
        st.caption("간트차트 생성에 실패했습니다.")
        return
    st.pyplot(fig, clear_figure=True, use_container_width=True)


def _render_utils_gantt_entry(entry: Dict[str, Any]) -> None:
    """entry 기반 간트. (sequence_history 방식)"""
    payload = entry.get("payload", {}) or {}
    phase = entry.get("phase", "before")
    method = entry.get("method", "")
    df = _gantt_df_for_payload(payload, phase)
    if df.empty:
        st.caption("간트차트를 만들 상세 공정 데이터가 없습니다.")
        return
    phase_text = "처음 시퀀스" if phase == "before" else f"요청 {entry.get('round_idx', 0)} 반영 후"
    title = f"{method.upper()} · {phase_text} — 공정별 간트차트"
    fig = create_enhanced_gantt_chart(df, method.upper(), title, save_path=None, return_fig=True)
    if fig is None:
        st.caption("간트차트 생성에 실패했습니다.")
        return
    st.pyplot(fig, clear_figure=True, use_container_width=True)


def _render_sequence_analysis_metrics_entry(entry: Dict[str, Any]) -> None:
    """entry 기반 4-metric grid."""
    payload = entry.get("payload", {}) or {}
    phase = entry.get("phase", "before")
    bundle = entry.get("bundle", {}) or {}
    before_bundle = payload.get("bundle", {}).get("before", {}) or {}

    primary_now = _safe_int(bundle.get("primary_violations"))
    makespan_hours = float(bundle.get("makespan_hours", 0.0) or 0.0)
    makespan_days = makespan_hours / 24.0

    before_makespan = float(before_bundle.get("makespan_hours", 0.0) or 0.0)
    if phase == "after" and before_makespan > 0:
        delta_pct = (makespan_hours - before_makespan) / before_makespan * 100.0
        delta_text = f"{delta_pct:+.1f}%"
    else:
        delta_text = "기준"

    gantt_df = _gantt_df_for_payload(payload, phase)
    machine_order = list(create_machine_mapping().keys())
    utilization = _utilization_from_timeline(gantt_df, machine_order)
    util_pct = int(round(utilization * 100))

    summary = payload.get("summary", {}) or {}
    after_csv = summary.get(f"{phase}_csv")
    delayed_count = _count_delayed_blocks(load_csv(after_csv)) if after_csv else 0

    html = f"""
    <div class='analysis-card'>
      <div class='analysis-title'>선택 시퀀스 분석 · {entry.get('method','').upper()} · {('처음 시퀀스' if phase=='before' else '요청 '+str(entry.get('round_idx',0))+' 반영 후')}</div>
      <div class='analysis-grid'>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>제약 조건 위배</div>
          <div class='analysis-cell-value'>{primary_now}건</div>
        </div>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>Makespan</div>
          <div class='analysis-cell-value'>{makespan_days:.1f}일</div>
          <div class='analysis-cell-sub'>{delta_text}</div>
        </div>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>설비 가동률</div>
          <div class='analysis-cell-value'>{util_pct}%</div>
        </div>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>총 지연 건수</div>
          <div class='analysis-cell-value'>{delayed_count}</div>
        </div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _render_sequence_analysis_metrics(results: Dict[str, Dict[str, Any]], selected: tuple[str, str]) -> None:
    """4-metric grid: 제약조건 위배 · Makespan · 설비 가동률 · 총 지연."""
    method, phase = selected
    payload = results[method]
    bundle = payload["bundle"]
    current = bundle.get(phase, {})
    before = bundle.get("before", {})

    primary_now = _safe_int(current.get("primary_violations"))
    makespan_hours = float(current.get("makespan_hours", 0.0) or 0.0)
    makespan_days = makespan_hours / 24.0

    before_makespan = float(before.get("makespan_hours", 0.0) or 0.0)
    if phase == "after" and before_makespan > 0:
        delta_pct = (makespan_hours - before_makespan) / before_makespan * 100.0
        delta_text = f"{delta_pct:+.1f}%"
    else:
        delta_text = "기준"

    gantt_df = _gantt_df_for_payload(payload, phase)
    machine_order = list(create_machine_mapping().keys())
    utilization = _utilization_from_timeline(gantt_df, machine_order)
    util_pct = int(round(utilization * 100))

    summary = payload.get("summary", {})
    after_csv = summary.get(f"{phase}_csv") if phase in ("before", "after") else None
    delayed_count = _count_delayed_blocks(load_csv(after_csv)) if after_csv else 0

    html = f"""
    <div class='analysis-card'>
      <div class='analysis-title'>시퀀스 리스트에 대한 분석</div>
      <div class='analysis-grid'>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>제약 조건 위배</div>
          <div class='analysis-cell-value'>{primary_now}건</div>
        </div>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>Makespan</div>
          <div class='analysis-cell-value'>{makespan_days:.1f}일</div>
          <div class='analysis-cell-sub'>{delta_text}</div>
        </div>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>설비 가동률</div>
          <div class='analysis-cell-value'>{util_pct}%</div>
        </div>
        <div class='analysis-cell'>
          <div class='analysis-cell-label'>총 지연 건수</div>
          <div class='analysis-cell-value'>{delayed_count}</div>
        </div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _render_sequence_list_picker(results: Dict[str, Dict[str, Any]]) -> None:
    """(legacy) 단일 results 기반 picker — 유지하되 현재는 entry 기반 picker가 주 경로."""
    keys = _sequence_keys(results)
    if not keys:
        st.caption("시퀀스 결과가 없습니다.")
        return
    selected = _ensure_selected_sequence(results)

    st.markdown("<div class='seq-list-title'>결과 비교</div>", unsafe_allow_html=True)
    for idx, key in enumerate(keys):
        method, phase = key
        bundle = _sequence_bundle(results, key)
        makespan_hours = float(bundle.get("makespan_hours", 0.0) or 0.0)
        primary = _safe_int(bundle.get("primary_violations"))
        head = _sequence_head_text(bundle.get("sequence_head", []), limit=6)
        active = selected == key
        is_before = phase == "before"
        subtitle = "처음 시퀀스 (baseline)" if is_before else "요청 반영 후 결과"
        badge_color = "#64748b" if is_before else METHOD_COLORS.get(method, "#2563eb")

        active_class = "seq-card-active" if active else ""
        label = _sequence_label(key, idx)
        card_html = f"""
        <div class='seq-card {active_class}' style='border-color:{badge_color};'>
          <div class='seq-card-head'>
            <span class='seq-card-badge' style='background:{badge_color};'>{method.upper()}</span>
            <span class='seq-card-title'>{label}</span>
          </div>
          <div class='seq-card-sub'>{subtitle}</div>
          <div class='seq-card-kpis'>
            <span class='seq-card-kpi'><b>{makespan_hours/24:.1f}</b>일</span>
            <span class='seq-card-kpi'>위배 <b>{primary}</b>건</span>
          </div>
          <div class='seq-card-head-seq'>{head}</div>
        </div>
        """
        st.markdown(card_html, unsafe_allow_html=True)
        if st.button("클릭해 자세히", key=f"seq_pick_{idx}_{method}_{phase}", use_container_width=True):
            st.session_state.selected_sequence = key
            try:
                st.rerun()
            except Exception:
                pass


def _render_sequence_entries_picker() -> Optional[Dict[str, Any]]:
    """[AGENT-ADD] 누적된 시퀀스 history 전체를 카드 형태로 노출. 선택 entry를 반환."""
    entries = _history_entries()
    if not entries:
        return None
    selected_entry = _ensure_selected_entry()
    selected_id = selected_entry["entry_id"] if selected_entry else None

    overlay = set(st.session_state.get("overlay_entry_ids", []))

    st.markdown("<div class='seq-list-title'>결과 비교 · 시퀀스 목록</div>", unsafe_allow_html=True)
    st.caption("카드를 클릭하면 아래 상세·간트·지표가 바뀝니다. 체크박스로 그래프 overlay에 비교 시퀀스를 여러 개 추가할 수 있습니다.")

    for idx, entry in enumerate(entries):
        method = entry.get("method", "")
        phase = entry.get("phase", "")
        bundle = entry.get("bundle", {}) or {}
        makespan_hours = float(bundle.get("makespan_hours", 0.0) or 0.0)
        primary = _safe_int(bundle.get("primary_violations"))
        head = _sequence_head_text(bundle.get("sequence_head", []), limit=6)
        is_before = phase == "before"
        subtitle = "처음 시퀀스 (baseline)" if is_before else f"요청 {entry.get('round_idx',0)}: {entry.get('request_text','') or '(요청 적용)'}"
        badge_color = "#64748b" if is_before else METHOD_COLORS.get(method, "#2563eb")
        label = _entry_label(entry, idx)
        active = entry["entry_id"] == selected_id
        active_class = "seq-card-active" if active else ""

        card_html = f"""
        <div class='seq-card {active_class}' style='border-color:{badge_color};'>
          <div class='seq-card-head'>
            <span class='seq-card-badge' style='background:{badge_color};'>{method.upper()}</span>
            <span class='seq-card-title'>{label}</span>
            <span class='seq-card-time'>{entry.get('created_at','')}</span>
          </div>
          <div class='seq-card-sub'>{subtitle}</div>
          <div class='seq-card-kpis'>
            <span class='seq-card-kpi'><b>{makespan_hours/24:.1f}</b>일</span>
            <span class='seq-card-kpi'>위배 <b>{primary}</b>건</span>
          </div>
          <div class='seq-card-head-seq'>{head}</div>
        </div>
        """
        st.markdown(card_html, unsafe_allow_html=True)
        col_pick, col_ovl = st.columns([3, 2])
        if col_pick.button("상세 보기", key=f"entry_pick_{entry['entry_id']}", use_container_width=True):
            st.session_state.selected_sequence_id = entry["entry_id"]
            try:
                st.rerun()
            except Exception:
                pass
        new_checked = col_ovl.checkbox(
            "그래프 overlay",
            value=entry["entry_id"] in overlay,
            key=f"entry_overlay_{entry['entry_id']}",
        )
        if new_checked:
            overlay.add(entry["entry_id"])
        else:
            overlay.discard(entry["entry_id"])

    st.session_state.overlay_entry_ids = [e["entry_id"] for e in entries if e["entry_id"] in overlay]
    return _entry_by_id(st.session_state.get("selected_sequence_id"))


def _daily_trend_df(results: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate per-date block count + seam load per method/phase from after CSVs."""
    rows: List[Dict[str, Any]] = []
    for method, payload in results.items():
        summary = payload["summary"]
        for phase in ("before", "after"):
            csv_path = summary.get(f"{phase}_csv")
            if not csv_path:
                continue
            df = load_csv(csv_path)
            if df.empty or "date" not in df.columns:
                continue
            df = df.copy()
            df["date_key"] = df["date"].map(_date_key)
            df = df.loc[df["date_key"].astype(str).str.len() == 8]
            if df.empty:
                continue
            grouped = df.groupby("date_key")
            for date_key, sub in grouped:
                seam_sum = int(sub["seam_count"].sum()) if "seam_count" in sub.columns else 0
                rows.append({
                    "date": pd.to_datetime(date_key, format="%Y%m%d", errors="coerce"),
                    "date_label": _date_label(date_key),
                    "method": method.upper(),
                    "phase": phase,
                    "blocks": int(len(sub)),
                    "seam": seam_sum,
                    "series": f"{method.upper()} · {'Before' if phase == 'before' else 'After'}",
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["series", "date"]).reset_index(drop=True)


def _daily_trend_entries_df(entries: List[Dict[str, Any]]) -> pd.DataFrame:
    """[AGENT-ADD] entry 리스트 기준 날짜별 집계. 각 entry = 별도 series."""
    rows: List[Dict[str, Any]] = []
    machine_order = list(create_machine_mapping().keys())
    for entry in entries:
        payload = entry.get("payload", {}) or {}
        phase = entry.get("phase", "before")
        method = entry.get("method", "")
        label = entry.get("method", "").upper()
        if phase == "before":
            label += " · 처음"
        else:
            label += f" · 요청{entry.get('round_idx',0)}"
        summary = payload.get("summary", {}) or {}
        csv_path = summary.get(f"{phase}_csv")
        if not csv_path:
            continue
        df = load_csv(csv_path)
        if df.empty or "date" not in df.columns:
            continue
        df = df.copy()
        df["date_key"] = df["date"].map(_date_key)
        df = df.loc[df["date_key"].astype(str).str.len() == 8]
        if df.empty:
            continue
        # [AGENT-EDIT] 날짜별 실제 위배 수 — CSV row당 `violations_primary_count` 우선, 없으면 `violations` 사용.
        viol_col = None
        for cand in ("violations_primary_count", "violations", "violations_raw_count"):
            if cand in df.columns:
                viol_col = cand
                break
        grouped = df.groupby("date_key")
        for date_key, sub in grouped:
            seam_sum = int(sub["seam_count"].sum()) if "seam_count" in sub.columns else 0
            blocks = int(len(sub))
            if viol_col is not None:
                try:
                    viol_sum = int(pd.to_numeric(sub[viol_col], errors="coerce").fillna(0).sum())
                except Exception:
                    viol_sum = 0
            else:
                viol_sum = 0
            bay_col = None
            for cand in ("bay_group", "assigned_bay", "bay"):
                if cand in sub.columns:
                    bay_col = cand
                    break
            if bay_col is not None:
                try:
                    counts = sub[bay_col].astype(str).str.upper().str[-1].value_counts()
                    a_cnt = int(counts.get("A", 0))
                    b_cnt = int(counts.get("B", 0))
                    bay_balance = abs(a_cnt - b_cnt)
                except Exception:
                    bay_balance = 0
            else:
                bay_balance = 0
            rows.append({
                "date": pd.to_datetime(date_key, format="%Y%m%d", errors="coerce"),
                "date_label": _date_label(date_key),
                "series": label,
                "entry_id": entry["entry_id"],
                "blocks": blocks,
                "seam": seam_sum,
                "violations": viol_sum,
                "bay_balance": bay_balance,
            })
    df_out = pd.DataFrame(rows)
    if df_out.empty:
        return df_out
    return df_out.sort_values(["series", "date"]).reset_index(drop=True)


def _render_daily_trend_entries() -> None:
    """[AGENT-ADD] overlay로 선택된 entry 전부를 하나의 그래프에 오버레이한다.
    요청 전에는 baseline 한 개만 그려진다."""
    overlay_ids: List[str] = list(st.session_state.get("overlay_entry_ids", []) or [])
    if not overlay_ids:
        selected = _ensure_selected_entry()
        if selected:
            overlay_ids = [selected["entry_id"]]
    entries = [e for e in _history_entries() if e["entry_id"] in overlay_ids]
    if not entries:
        st.caption("그래프에 그릴 시퀀스가 없습니다.")
        return

    metric = st.radio(
        "추이 지표",
        options=["일일 블록 수", "일일 심수", "Bay 부하 평준화", "제약조건 위배 수"],
        horizontal=True,
        key="daily_trend_metric_entry",
        index=0,
    )
    metric_map = {
        "일일 블록 수": ("blocks", "블록 수"),
        "일일 심수": ("seam", "심수"),
        "Bay 부하 평준화": ("bay_balance", "|A-B| 블록 차 (작을수록 평준)"),
        "제약조건 위배 수": ("violations", "해당 날짜 위배 건수"),
    }
    y_col, y_label = metric_map[metric]

    df = _daily_trend_entries_df(entries)
    if df.empty:
        st.caption("날짜별 추이를 그릴 데이터가 없습니다.")
        return

    fig = px.line(
        df,
        x="date",
        y=y_col,
        color="series",
        markers=True,
        hover_data={"date_label": True, "series": True, "date": False},
    )
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        title=f"선택 시퀀스 overlay — 일별 {y_label}",
        yaxis_title=y_label,
        xaxis_title=None,
        font=dict(size=11),
    )
    fig.update_traces(line=dict(width=2.2))
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        key="daily_trend_chart_entries",
    )


def _render_daily_trend(results: Dict[str, Dict[str, Any]]) -> None:
    df = _daily_trend_df(results)
    if df.empty:
        st.caption("날짜별 추이를 그릴 데이터가 없습니다.")
        return

    metric = st.radio(
        "추이 지표",
        options=["일일 블록 수", "일일 심수"],
        horizontal=True,
        key="daily_trend_metric",
        index=0,
    )
    y_col = "blocks" if metric == "일일 블록 수" else "seam"
    y_label = "블록 수" if metric == "일일 블록 수" else "심수"

    fig = px.line(
        df,
        x="date",
        y=y_col,
        color="series",
        markers=True,
        line_dash="phase",
        hover_data={"date_label": True, "method": True, "phase": True, "date": False},
    )
    fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=24, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        title=f"전체 비교 — 방법론별 일별 {y_label} 추이",
        yaxis_title=y_label,
        xaxis_title=None,
        font=dict(size=11),
    )
    fig.update_traces(line=dict(width=2.2))
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        key="daily_trend_chart",
    )


def _render_status_header_strip(results: Dict[str, Dict[str, Any]]) -> None:
    """Top strip: [현황판] title + time + quick KPIs."""
    now_label = datetime.now().strftime("%Y-%m-%d %H:%M")
    if results:
        ref_method, ref_payload = _reference_payload(results)
        state = _derive_reference_state(ref_payload)
        kpi_html = (
            f"<span class='status-strip-kpi'>기준 방법 <b>{ref_method.upper()}</b></span>"
            f"<span class='status-strip-kpi'>기준 시점 <b>{state['reference_datetime']}</b></span>"
            f"<span class='status-strip-kpi'>이미 확정 <b>{state['committed_prefix_len']}</b>개</span>"
            f"<span class='status-strip-kpi'>재조정 시작 <b>{state['editable_start_step']}</b> step</span>"
            f"<span class='status-strip-kpi'>지연 <b>{state['delayed_before']} → {state['delayed_after']}</b></span>"
        )
    else:
        kpi_html = "<span class='status-strip-kpi'>아직 결과가 없습니다.</span>"

    st.markdown(
        f"""
        <div class='status-strip'>
          <div class='status-strip-title'>[현황판]</div>
          <div class='status-strip-meta'>
            <span class='status-strip-kpi'>시간 <b>{now_label}</b></span>
            {kpi_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_chatroom_list() -> None:
    """Left column: list of recent chat sessions (titles from chat_history)."""
    st.markdown("<div class='chatroom-title'>채팅방 목록</div>", unsafe_allow_html=True)
    sessions: List[Dict[str, str]] = st.session_state.get("chatroom_sessions", []) or []
    # Backfill with recent user prompts so the pane is not empty.
    recent_user_prompts = [m["content"] for m in st.session_state.get("chat_history", []) if m.get("role") == "user"][-4:]
    if not sessions and recent_user_prompts:
        sessions = [
            {"title": (p if len(p) <= 24 else p[:22] + "…"), "time": datetime.now().strftime("%m-%d %H:%M")}
            for p in recent_user_prompts
        ]
    if not sessions:
        sessions = [
            {"title": "새 대화", "time": datetime.now().strftime("%m-%d %H:%M")},
        ]

    for idx, session in enumerate(sessions):
        active = idx == st.session_state.get("active_chatroom_idx", 0)
        active_class = "chatroom-item-active" if active else ""
        st.markdown(
            f"""
            <div class='chatroom-item {active_class}'>
              <div class='chatroom-item-title'>{session['title']}</div>
              <div class='chatroom-item-time'>{session['time']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<div class='chatroom-hint'>최근 대화 기록은 자동으로 목록에 쌓입니다.</div>", unsafe_allow_html=True)
    st.markdown(
        """
        <div class='chatroom-footer'>
          <div class='chatroom-footer-avatar'>PBS</div>
          <div>
            <div class='chatroom-footer-name'>현장 스케줄러</div>
            <div class='chatroom-footer-sub'>LPT · SPT · RL 비교 데모</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_chat_conversation() -> None:
    """Middle column: ChatGPT-style conversation log.
    [AGENT-EDIT] 메시지를 자르지 않고 전체 누적 표시. 컨테이너는 스크롤."""
    st.markdown("<div class='chat-panel-title'>대화</div>", unsafe_allow_html=True)
    messages = st.session_state.get("chat_history", [])
    if not messages:
        st.markdown(
            "<div class='chat-empty'>안녕하세요. 어떤 최적화를 도와드릴까요?</div>",
            unsafe_allow_html=True,
        )
        return
    with st.container(height=480, border=False):
        for message in messages:
            role = message.get("role", "assistant")
            with st.chat_message(role):
                st.markdown(message.get("content", ""))


def _render_chat_action_buttons(results: Dict[str, Dict[str, Any]]) -> tuple[bool, bool]:
    # [AGENT-EDIT] 전송 = 바로 적용. 적용/비교 버튼은 제거됨. 비교는 결과 비교 탭에서 처리.
    return False, False


# =========================================================================
# [AGENT-ADD END]
# =========================================================================



def _render_run_settings_panel(excel_files: List[str], rl_models: List[str], default_rl_model: str) -> tuple[Dict[str, Any], bool]:
    """모든 실행 설정을 좌측 채팅방 열 내부 expander에 모아서 렌더한다.

    반환: (controls dict, "현재 설정으로 실행" 버튼 클릭 여부)
    """
    default_excel = "environment/판넬 블록 데이터셋_250618_SNU.xlsx"
    excel_path = st.selectbox(
        "Excel 데이터",
        options=excel_files,
        index=excel_files.index(default_excel) if default_excel in excel_files else 0,
        key="settings_excel_path",
    )
    sheet_name = st.text_input("Sheet 이름", value="Sheet1", key="settings_sheet_name")
    methods = st.multiselect(
        "실행 방법",
        options=["rl", "lpt", "spt"],
        default=DEFAULT_VISIBLE_METHODS,
        key="settings_methods",
    )
    rl_model_path = (
        st.selectbox(
            "RL 모델",
            options=rl_models,
            index=rl_models.index(default_rl_model) if default_rl_model in rl_models else 0,
            key="settings_rl_model",
        )
        if rl_models
        else st.text_input("RL 모델", value="", key="settings_rl_model_text")
    )
    freeze_existing_prefix = st.checkbox(
        "앞 시퀀스 고정",
        value=True,
        key="settings_freeze_prefix",
        help="이미 현장 반영된 앞 시퀀스를 고정하고 그 다음부터만 재스케줄링",
    )
    run_recommendation = st.checkbox("권고안도 같이 계산", value=False, key="settings_recommend")

    with st.expander("LLM 연결", expanded=False):
        parser_label = st.selectbox(
            "요청 해석 방식",
            options=list(LLM_PARSER_OPTIONS.keys()),
            index=0,
            key="settings_parser_label",
            help="규칙 기반은 API 없이 동작합니다. Groq/Ollama는 더 자유로운 자연어 요청을 구조화합니다.",
        )
        parser_mode = LLM_PARSER_OPTIONS[parser_label]
        llm_provider = "" if parser_mode == "deterministic" else parser_mode
        default_model = ""
        default_base_url = ""
        if parser_mode == "groq":
            default_model = os.environ.get("GROQ_MODEL") or os.environ.get("PBS_LLM_MODEL") or "llama-3.3-70b-versatile"
        elif parser_mode == "gemini":
            default_model = os.environ.get("GEMINI_MODEL") or os.environ.get("PBS_LLM_MODEL") or "gemini-2.5-flash-lite"
        elif parser_mode == "ollama":
            default_model = os.environ.get("OLLAMA_MODEL") or os.environ.get("PBS_LLM_MODEL") or "llama3.1:8b"
            default_base_url = os.environ.get("PBS_LLM_BASE_URL") or "http://localhost:11434/v1"
        elif parser_mode == "openai":
            default_model = os.environ.get("OPENAI_MODEL") or os.environ.get("PBS_LLM_MODEL") or "gpt-4.1-mini"
        elif parser_mode == "openai_compatible":
            default_model = os.environ.get("PBS_LLM_MODEL") or ""
            default_base_url = os.environ.get("PBS_LLM_BASE_URL") or ""
        llm_model = st.text_input("모델", value=default_model, key="settings_llm_model")
        llm_base_url = ""
        if parser_mode in {"ollama", "openai_compatible"}:
            llm_base_url = st.text_input("Base URL", value=default_base_url, key="settings_llm_base_url")
        llm_api_key = ""
        if parser_mode in {"groq", "gemini", "openai", "openai_compatible"}:
            llm_api_key = st.text_input(
                "API key",
                value="",
                type="password",
                key="settings_llm_api_key",
                help="비워두면 쉘 환경변수(GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY 등)를 사용합니다.",
            )
        if parser_mode == "ollama":
            st.caption("Ollama는 로컬에서 `ollama serve`와 모델 pull이 되어 있어야 합니다.")

    anchor_date = infer_planning_anchor_date(excel_path, sheet_name) or date.today()
    default_plan_start = anchor_date
    default_plan_end = anchor_date + timedelta(days=19)
    plan_range = st.date_input(
        "계획 기간",
        value=(default_plan_start, default_plan_end),
        key="settings_plan_range",
    )
    if isinstance(plan_range, tuple) and len(plan_range) == 2:
        plan_start, plan_end = plan_range
    elif isinstance(plan_range, tuple) and len(plan_range) == 1:
        plan_start = plan_range[0]
        plan_end = plan_start
    else:
        plan_start = plan_range
        plan_end = plan_start
    if plan_start > plan_end:
        plan_start, plan_end = plan_end, plan_start
    date_offset = max(0, (plan_start - anchor_date).days)
    max_days = max(1, (plan_end - plan_start).days + 1)
    st.caption(f"실행 기준: {anchor_date:%Y-%m-%d} + {date_offset}일, {max_days}일 계획")

    with st.expander("작업 달력", expanded=False):
        closed_dates_text = st.text_area("작업 안 하는 날", value="", placeholder="예: 2025-06-11, 2025-06-14~2025-06-15", height=68, key="settings_closed_dates")
        work_dates_text = st.text_area("정상 작업으로 둘 날", value="", placeholder="예: 2025-06-15", height=68, key="settings_work_dates")
        morning_only_dates_text = st.text_area("오전만 작업하는 날", value="", placeholder="예: 2025-06-12", height=68, key="settings_morning_only")
        afternoon_shutdown = st.text_input("오전만 작업 시 중지 시간", value="15:00-08:00", key="settings_afternoon_shutdown")
        afternoon_only_dates_text = st.text_area("오후만 작업하는 날", value="", placeholder="예: 2025-06-13", height=68, key="settings_afternoon_only")
        morning_shutdown = st.text_input("오후만 작업 시 중지 시간", value="08:00-12:00", key="settings_morning_shutdown")
        enable_lunch_break = st.checkbox("점심시간 제외", value=False, key="settings_lunch_break_enable")
        lunch_break = st.text_input("점심시간", value="12:00-13:00", key="settings_lunch_break")

    constraint_states: Dict[str, str] = {}
    with st.expander("Runtime 제약 토글", expanded=False):
        for item in CONSTRAINT_TOGGLES:
            constraint_states[item["key"]] = st.selectbox(
                item["label"],
                options=TOGGLE_OPTIONS,
                index=0,
                key=f"toggle_{item['key']}",
            )

    with st.expander("후보 축소 / 날짜 제한", expanded=False):
        bias_mode = st.selectbox("후보 축소 preset", options=list(BIAS_OPTIONS.keys()), index=0, key="settings_bias_mode")
        enable_block_cap = st.checkbox("일일 블록 수 상한 사용", value=False, key="settings_enable_block_cap")
        block_cap_day = st.number_input("블록 상한 날짜(일)", min_value=1, max_value=31, value=11, step=1, key="settings_block_cap_day")
        block_cap_value = st.number_input("최대 블록 수", min_value=1, max_value=100, value=15, step=1, key="settings_block_cap_value")
        enable_seam_cap = st.checkbox("일일 심수 상한 사용", value=False, key="settings_enable_seam_cap")
        seam_cap_day = st.number_input("심수 상한 날짜(일)", min_value=1, max_value=31, value=11, step=1, key="settings_seam_cap_day")
        seam_cap_value = st.number_input("최대 심수", min_value=1, max_value=200, value=72, step=1, key="settings_seam_cap_value")

    run_sidebar = st.button("현재 설정으로 실행", use_container_width=True, key="settings_run_btn")

    controls = {
        "excel_path": excel_path,
        "sheet_name": sheet_name,
        "methods": methods,
        "rl_model_path": rl_model_path,
        "freeze_existing_prefix": freeze_existing_prefix,
        "run_recommendation": run_recommendation,
        "parser_mode": parser_mode,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_base_url": llm_base_url,
        "llm_api_key": llm_api_key,
        "date_offset": date_offset,
        "max_days": max_days,
        "closed_dates_text": closed_dates_text,
        "work_dates_text": work_dates_text,
        "morning_only_dates_text": morning_only_dates_text,
        "afternoon_shutdown": afternoon_shutdown,
        "afternoon_only_dates_text": afternoon_only_dates_text,
        "morning_shutdown": morning_shutdown,
        "enable_lunch_break": enable_lunch_break,
        "lunch_break": lunch_break,
        "constraint_states": constraint_states,
        "bias_mode": bias_mode,
        "enable_block_cap": enable_block_cap,
        "block_cap_day": block_cap_day,
        "block_cap_value": block_cap_value,
        "enable_seam_cap": enable_seam_cap,
        "seam_cap_day": seam_cap_day,
        "seam_cap_value": seam_cap_value,
    }
    return controls, run_sidebar


def main() -> None:
    # [AGENT-EDIT] ChatGPT homepage 스타일: 내장 사이드바 접고 3열 레이아웃만 사용한다.
    st.set_page_config(
        page_title="PBS Interactive Scheduler",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()
    _init_state()

    if not st.session_state.sample_autoloaded and st.session_state.latest_results is None:
        sample_results = _load_sample_results(DEFAULT_VISIBLE_METHODS)
        if sample_results:
            st.session_state.latest_results = sample_results
            # [AGENT-EDIT] 자동 로드는 요청이 아님 → request_text 비워두고 baseline만 history에 등록.
            st.session_state.latest_request_text = ""
            st.session_state.draft_prompt = ""
            st.session_state.last_prompt = ""
            st.session_state.sample_autoloaded = True
            _ingest_initial_results(sample_results)

    excel_files = discover_excel_files()
    rl_models = discover_rl_models()
    default_rl_model = next((path for path in rl_models if "best_ppo" in Path(path).name.lower()), rl_models[0] if rl_models else "")

    # [AGENT-EDIT] 재설계된 레이아웃: 좌측 레일(채팅방 + 설정) + 우측 메인(탭)
    top_results = st.session_state.latest_results or {}

    col_rail, col_main = st.columns([1.0, 5.0], gap="medium")

    # ---- 좌측 레일 ----------------------------------------------------------
    with col_rail:
        st.markdown(
            "<div class='rail-brand'>PBS<br/><span>Interactive Scheduler</span></div>",
            unsafe_allow_html=True,
        )
        if st.button("＋ 새 대화", key="new_chat_btn", use_container_width=True):
            st.session_state.chat_history = []
            st.session_state.draft_prompt = ""
            st.session_state.last_prompt = ""
            st.session_state.chatroom_sessions = []
            st.session_state.sequence_history = []
            st.session_state.overlay_entry_ids = []
            st.session_state.user_request_made = False
            st.session_state.selected_sequence_id = None
            st.session_state.next_round_idx = 0
            st.session_state.next_entry_seq = 0
            st.session_state.sample_autoloaded = False
            st.session_state.latest_results = None
            try:
                st.rerun()
            except Exception:
                pass
        _render_chatroom_list()
        with st.expander("⚙ 실행 설정", expanded=False):
            controls, run_sidebar = _render_run_settings_panel(excel_files, rl_models, default_rl_model)
        with st.expander("빠른 데모", expanded=False):
            for idx, (label_text, prompt_value) in enumerate(DEMO_PROMPTS.items()):
                if st.button(label_text, key=f"demo_btn_{idx}", use_container_width=True):
                    st.session_state.draft_prompt = prompt_value
                    st.session_state.last_prompt = prompt_value
            if st.button("샘플 결과 불러오기", key="load_sample_btn", use_container_width=True):
                sample_results = _load_sample_results(controls.get("methods") or DEFAULT_VISIBLE_METHODS)
                if sample_results:
                    st.session_state.latest_results = sample_results
                    st.session_state.latest_request_text = "11번 블록은 네 번째로 해줘 (앞 prefix 고정 샘플)"
                    st.session_state.draft_prompt = "11번 블록은 네 번째로 해줘"
                    st.session_state.last_prompt = "11번 블록은 네 번째로 해줘"
                    st.session_state.chat_history.append({"role": "user", "content": "샘플 결과 바로 불러오기"})
                    st.session_state.chat_history.append({"role": "assistant", "content": "저장된 샘플 결과를 불러왔습니다. (샘플 요청 1회 라운드)"})
                    _ingest_request_results(sample_results, "11번 블록은 네 번째로 해줘 (샘플)")
                else:
                    st.session_state.chat_history.append({"role": "assistant", "content": "불러올 샘플 결과가 없습니다. 먼저 요청을 한 번 실행해 주세요."})

    methods = controls.get("methods") or []

    # ---- 우측 메인 (탭) -----------------------------------------------------
    with col_main:
        _render_status_header_strip(top_results)
        tab_chat, tab_results, tab_status = st.tabs(["💬 대화", "📊 결과 비교", "📈 현황 요약"])

        with tab_chat:
            chat_container = st.container()
            with chat_container:
                _render_chat_conversation()
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                with st.form("request_form", clear_on_submit=False):
                    draft_prompt = st.text_area(
                        "최적화 요청 또는 명령어를 입력하세요...",
                        value=st.session_state.draft_prompt,
                        height=110,
                        label_visibility="collapsed",
                        placeholder=(
                            "예: 11번 블록은 네 번째로 해줘\n"
                            "예: 11번은 4번보다 먼저 오게 해줘\n"
                            "예: 11번 블록은 35A로 배정해줘"
                        ),
                    )
                    submit_chat = st.form_submit_button("전송", use_container_width=True)
                if st.session_state.latest_request_text:
                    with st.expander("실제 실행된 요청문", expanded=False):
                        st.code(st.session_state.latest_request_text, language="text")

        with tab_results:
            entries_all = _history_entries()
            if entries_all:
                res_left, res_right = st.columns([1.2, 3.0], gap="medium")
                with res_left:
                    selected_entry = _render_sequence_entries_picker()
                with res_right:
                    if selected_entry is not None:
                        _render_sequence_analysis_metrics_entry(selected_entry)
                        st.markdown("<div class='seq-list-title'>선택 시퀀스 간트차트</div>", unsafe_allow_html=True)
                        _render_utils_gantt_entry(selected_entry)
                    st.markdown("<div class='seq-list-title'>Overlay — 일별 추이</div>", unsafe_allow_html=True)
                    _render_daily_trend_entries()
                    st.markdown(
                        "<div class='rl-note'>"
                        "<b>RL 해 선택 기준 (Pareto)</b><br/>"
                        "1차: LPT 대비 <span class='rl-note-tag'>위배 &lt; LPT</span> 이고 "
                        "<span class='rl-note-tag'>Makespan &lt; LPT</span> 인 후보가 있으면 그 시퀀스를 채택.<br/>"
                        "2차 (동점 처리):"
                        "<ul style='margin:4px 0 0 18px;padding:0;'>"
                        "<li>위배 = LPT, Makespan &lt; LPT → 해당 시퀀스 채택</li>"
                        "<li>위배 &lt; LPT, Makespan = LPT → 해당 시퀀스 채택</li>"
                        "<li>그 외 (둘 다 &gt; LPT) → RL policy deterministic 출력 그대로 반환</li>"
                        "</ul>"
                        "가중합은 쓰지 않습니다. 두 지표를 각각 독립으로 비교합니다."
                        "</div>",
                        unsafe_allow_html=True,
                    )
            else:
                _render_empty_state()

        with tab_status:
            if top_results:
                _render_current_block_banner(top_results)
                st.markdown("<div class='seq-list-title'>공정 흐름</div>", unsafe_allow_html=True)
                _render_pipeline_flow(top_results)
            else:
                st.info("요청을 먼저 제출해 주세요. 현황 요약은 결과가 생긴 뒤에 표시됩니다.")

    # ---- Run trigger (preserved semantics) ---------------------------------
    should_run = False
    display_user_text: Optional[str] = None
    if submit_chat:
        st.session_state.draft_prompt = draft_prompt
        st.session_state.last_prompt = draft_prompt
        display_user_text = draft_prompt.strip() or "(채팅 입력 없음)"
        should_run = True
    elif run_sidebar:
        display_user_text = st.session_state.last_prompt.strip() or "(사이드바 설정만 실행)"
        should_run = True

    if should_run:
        if not methods:
            st.session_state.chat_history.append({"role": "assistant", "content": "최소 한 개의 메서드를 선택해야 합니다."})
            st.session_state.latest_results = None
        else:
            request_text = _compose_request_text(st.session_state.last_prompt, controls)
            st.session_state.latest_request_text = request_text
            st.session_state.chat_history.append({"role": "user", "content": display_user_text})

            if not request_text.strip():
                st.session_state.chat_history.append({"role": "assistant", "content": "실행할 요청이 없습니다. 채팅을 입력하거나 사이드바 제한을 하나 이상 켜야 합니다."})
                st.session_state.latest_results = None
            else:
                run_root = RESULTS_ROOT / f"streamlit_gui_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                run_root.mkdir(parents=True, exist_ok=True)
                results: Dict[str, Dict[str, Any]] = {}
                errors: List[str] = []
                with st.spinner("스케줄러를 다시 실행하는 중입니다..."):
                    for method in methods:
                        try:
                            results[method] = _run_reschedule_case(method, request_text, controls, run_root / "cases")
                        except Exception as exc:
                            errors.append(f"{method.upper()}: {exc}")
                st.session_state.latest_results = results
                st.session_state.selected_sequence = None
                # [AGENT-ADD] 누적 시퀀스 history에 이번 라운드 결과 push + 채팅방 세션 등록.
                added_ids = _ingest_request_results(results, request_text)
                chatroom_sessions: List[Dict[str, str]] = list(st.session_state.get("chatroom_sessions", []) or [])
                title = display_user_text or request_text.splitlines()[0]
                if len(title) > 26:
                    title = title[:24] + "…"
                chatroom_sessions.append({"title": title, "time": datetime.now().strftime("%m-%d %H:%M")})
                st.session_state.chatroom_sessions = chatroom_sessions
                st.session_state.active_chatroom_idx = len(chatroom_sessions) - 1
                assistant_text = _assistant_summary(request_text, results)
                if added_ids:
                    assistant_text += f"\n\n시퀀스 리스트에 {len(added_ids)}개 항목을 추가했습니다. 결과 비교 열에서 확인·overlay 선택이 가능합니다."
                if errors:
                    assistant_text += "\n" + "\n".join(f"- 오류: {msg}" for msg in errors)
                st.session_state.chat_history.append({"role": "assistant", "content": assistant_text})

    latest_results = st.session_state.latest_results or {}

    # ---- Bottom: 상세 분석 -------------------------------------------------
    if latest_results:
        st.divider()
        with st.expander("상세 비교 표 · 방법별 탭", expanded=False):
            comparison_df = pd.DataFrame(_comparison_rows(latest_results))
            st.dataframe(comparison_df, use_container_width=True)

            ordered_methods = [method for method in METHOD_ORDER if method in latest_results]
            if not ordered_methods:
                ordered_methods = list(latest_results.keys())
            tabs = st.tabs([method.upper() for method in ordered_methods])
            for tab, method in zip(tabs, ordered_methods):
                with tab:
                    _display_method_tab(method, latest_results[method])


if __name__ == "__main__":
    main()
