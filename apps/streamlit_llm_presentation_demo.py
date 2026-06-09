#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Presentation-focused LLM emergency rescheduling demo."""

# [AGENT-ADD] A compact Streamlit app for thesis/presentation demos. It uses
# the existing interactive rescheduling runner and final-audit outputs without
# replacing the PBS scheduler.

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


RESULTS_ROOT = REPO_ROOT / "PPO" / "eval"
DEFAULT_SAMPLE_DIR = RESULTS_ROOT / "llm_connect_schema_expanded_current_state_fixed"
DEFAULT_REQUEST = (
    "현재 3번과 2번 블록은 이미 작업 중이니까 순서를 고정하고, "
    "설비 복구 후 1번 블록을 가장 먼저 투입해줘. "
    "4번 블록은 고장 때문에 바로 다음 투입에서 제외하고, "
    "15번 블록은 36B 베이에 배정해줘."
)
DEFAULT_EXCEL_PATH = REPO_ROOT / "environment" / "판넬 블록 데이터셋_250618_SNU.xlsx"


CONSTRAINT_LABELS = {
    "freeze_prefix": "현재 작업 고정",
    "priority_block": "긴급 블록 우선 투입",
    "delayed_block": "고장 블록 투입 지연",
    "manual_bay_assignment": "베이 지정",
    "precedence": "투입 순서 조건",
    "fixed_position": "위치 지정",
}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_result_dir(result_dir: Path) -> Dict[str, Any]:
    summary = _load_json(result_dir / "summary.json")
    solution_eval = _load_json(result_dir / "solution_evaluation.json")
    request_json = _load_json(result_dir / "request.json")
    before_df = _read_csv(result_dir / "before_results.csv")
    after_df = _read_csv(result_dir / "after_results.csv")
    checks_df = _read_csv(result_dir / "solution_request_checks.csv")
    current_df = _read_csv(result_dir / "solution_current_state_checks.csv")
    return {
        "result_dir": result_dir,
        "summary": summary,
        "solution_eval": solution_eval,
        "request_json": request_json,
        "before_df": before_df,
        "after_df": after_df,
        "checks_df": checks_df,
        "current_df": current_df,
    }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def _metric_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "is_metric_anchor_row" not in df.columns:
        return df.copy()
    mask = pd.to_numeric(df["is_metric_anchor_row"], errors="coerce").fillna(0).astype(int) == 1
    if not mask.any():
        return df.copy()
    return df.loc[mask].copy()


def _sequence(df: pd.DataFrame, limit: int = 12) -> List[int]:
    metric = _metric_df(df)
    if metric.empty or "block_id" not in metric.columns:
        return []
    if "am_sequence" in metric.columns:
        metric = metric.sort_values("am_sequence")
    return [int(value) for value in metric["block_id"].dropna().head(limit).tolist()]


def _metric_value(df: pd.DataFrame, column: str, mode: str = "sum") -> float:
    metric = _metric_df(df)
    if metric.empty or column not in metric.columns:
        return 0.0
    values = pd.to_numeric(metric[column], errors="coerce").fillna(0.0)
    return float(values.max() if mode == "max" else values.sum())


def _constraints_from_request(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    request = payload.get("request", {}) if payload else {}
    return list(request.get("constraints") or [])


def _clean_text(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "freeze_prefix": "현재 작업 고정",
        "priority_block": "긴급 블록 우선 투입",
        "delayed_block": "고장 블록 투입 지연",
        "manual_bay_assignment": "베이 지정",
        "fixed_position": "위치 지정",
        "precedence": "투입 순서 조건",
        "next_after_recovery": "설비 복구 직후",
        "not_immediate_next": "바로 다음 투입 제외",
        "immediate_next": "다음 투입",
        "primary violation": "주요 제약 위반",
        "raw violation": "전체 검출 이벤트",
        "makespan": "전체 완료시간",
        "_": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _constraint_narrative(item: Dict[str, Any]) -> Dict[str, str]:
    ctype = str(item.get("type") or "")
    if ctype == "freeze_prefix":
        blocks = ", ".join(f"{b}번" for b in item.get("block_ids") or [])
        return {
            "title": "현재 작업 고정",
            "body": f"{blocks} 블록은 이미 공정에 들어간 상태로 보고 순서를 변경하지 않습니다.",
            "role": "운영 상태",
        }
    if ctype == "priority_block":
        return {
            "title": "긴급 블록 우선 투입",
            "body": f"{item.get('block_id')}번 블록을 설비 복구 직후 남은 작업 중 가장 앞쪽에 배치합니다.",
            "role": "작업자 요청",
        }
    if ctype == "delayed_block":
        return {
            "title": "고장 영향 반영",
            "body": f"{item.get('block_id')}번 블록은 바로 다음 투입 대상에서 제외하고 우선 블록 뒤로 미룹니다.",
            "role": "현장 이벤트",
        }
    if ctype == "manual_bay_assignment":
        return {
            "title": "베이 지정",
            "body": f"{item.get('block_id')}번 블록은 {item.get('bay')} 베이에 배정합니다.",
            "role": "작업 조건",
        }
    if ctype == "precedence":
        return {
            "title": "투입 순서 조건",
            "body": f"{item.get('before_block_id')}번 블록을 {item.get('after_block_id')}번 블록보다 먼저 처리합니다.",
            "role": "순서 제약",
        }
    return {
        "title": CONSTRAINT_LABELS.get(ctype, "추가 조건"),
        "body": _clean_text(json.dumps(item, ensure_ascii=False)),
        "role": "조건",
    }


def _solution_reasoning(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    solution = payload["solution_eval"]
    before_df = payload["before_df"]
    after_df = payload["after_df"]
    before_seq = _sequence(before_df, 8)
    after_seq = _sequence(after_df, 8)
    before_primary = int(_metric_value(before_df, "violations_primary_count"))
    after_primary = int(_metric_value(after_df, "violations_primary_count"))
    before_make = _metric_value(before_df, "makespan_hours", "max")
    after_make = _metric_value(after_df, "makespan_hours", "max")

    reasons: List[Dict[str, str]] = [
        {
            "title": "이미 시작된 작업은 변경하지 않음",
            "body": f"기준안과 변경안 모두 앞 순서를 {before_seq[:2]}로 유지했습니다. 따라서 재스케줄링은 이미 진행 중인 작업 이후의 남은 투입 순서만 조정합니다.",
        },
        {
            "title": "긴급 요청을 가장 빠른 가능한 위치에 반영",
            "body": f"변경안의 앞 순서는 {after_seq[:4]}입니다. 1번 블록이 작업 고정 prefix 뒤에 배치되어 요청 조건을 만족합니다.",
        },
        {
            "title": "고장 블록은 긴급 블록 뒤로 지연",
            "body": "4번 블록은 바로 다음 투입에서 제외되고 1번 블록 이후로 이동했습니다. 고장 이벤트를 단순 삭제가 아니라 순서 조건으로 반영한 것입니다.",
        },
        {
            "title": "제약 검증은 사후 감사 기준으로 확인",
            "body": f"주요 제약 위반은 {before_primary}건에서 {after_primary}건으로 변했습니다. 이 값은 스케줄 생성 중 로그가 아니라 final audit 결과입니다.",
        },
        {
            "title": "시간 성능도 함께 비교",
            "body": f"전체 완료시간은 {before_make:.2f}시간에서 {after_make:.2f}시간으로 변했습니다. 요청 만족 후 제약 위반과 완료시간을 함께 검토합니다.",
        },
    ]
    if not solution.get("interpretation_flags", {}).get("use_metric_delta_as_operational_evidence"):
        reasons.insert(
            0,
            {
                "title": "운영 해석 주의",
                "body": "현재상태 일관성 검사가 통과하지 않아 지표 개선을 운영 성능 근거로 직접 해석하지 않습니다.",
            },
        )
    return reasons


def _run_demo(request_text: str, parser_mode: str, output_dir: Path) -> subprocess.CompletedProcess[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("PBS_FORCE_DEBUG", "0")
    env.setdefault("PBS_DEBUG_VERBOSE", "0")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "experiments" / "interactive_llm_reschedule.py"),
        "--excel-path",
        str(DEFAULT_EXCEL_PATH),
        "--sheet-name",
        "Sheet1",
        "--method",
        "lpt",
        "--parser",
        parser_mode,
        "--request",
        request_text,
        "--output-dir",
        str(output_dir),
        "--start-date",
        "2025-01-01",
        "--max-days",
        "20",
    ]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        env=env,
        timeout=180,
    )


def _status_chip(label: str, value: Any) -> str:
    ok = bool(value)
    cls = "ok" if ok else "bad"
    text = "PASS" if ok else "CHECK"
    return f"<span class='chip {cls}'>{label}: {text}</span>"


def _seq_html(values: List[int], highlight: Dict[int, str] | None = None) -> str:
    highlight = highlight or {}
    items = []
    for value in values:
        cls = highlight.get(int(value), "normal")
        items.append(f"<span class='block {cls}'>{value}</span>")
    return "<div class='seq'>" + "".join(items) + "</div>"


def _render_css() -> None:
    st.markdown(
        """
        <style>
        .main .block-container {max-width: 1480px; padding-top: 1.2rem;}
        h1, h2, h3 {letter-spacing: 0;}
        .top-title {font-size: 1.6rem; font-weight: 760; color: #0f2742; margin-bottom: .15rem;}
        .subtle {color: #52616f; font-size: .92rem;}
        .band {border-top: 1px solid #dde5ee; padding-top: .9rem; margin-top: .9rem;}
        .panel-title {font-size: 1rem; font-weight: 720; color: #0f2742; margin-bottom: .45rem;}
        .metric-row {display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .65rem; margin: .45rem 0 .8rem;}
        .metric-box {border: 1px solid #d5dee8; border-radius: 7px; padding: .62rem .7rem; background: #ffffff;}
        .metric-label {font-size: .76rem; color: #657181;}
        .metric-value {font-size: 1.35rem; font-weight: 760; color: #10243a;}
        .metric-delta {font-size: .78rem; color: #66717c;}
        .chip {display: inline-block; border-radius: 999px; padding: .28rem .55rem; font-weight: 700; font-size: .78rem; margin-right: .35rem;}
        .chip.ok {background: #e7f6ec; color: #166534; border: 1px solid #b7e2c4;}
        .chip.bad {background: #fff1e8; color: #a13c13; border: 1px solid #ffc79f;}
        .seq {display: flex; flex-wrap: wrap; gap: .35rem; min-height: 2.2rem; align-items: center;}
        .block {display: inline-flex; align-items: center; justify-content: center; min-width: 2.15rem; height: 2.05rem; border-radius: 6px; border: 1px solid #bfcbd7; font-weight: 760; background: #f4f7fb; color: #10243a;}
        .block.freeze {background: #eaf2ff; border-color: #8fb7e8;}
        .block.priority {background: #fff2c6; border-color: #dfaa29;}
        .block.delayed {background: #ffe8e2; border-color: #e69a87;}
        .block.normal {background: #edf7ed; border-color: #b7d9b7;}
        .constraint-grid {display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .5rem;}
        .constraint {border: 1px solid #d4dee8; border-radius: 7px; padding: .62rem .68rem; min-height: 6.1rem; background: #fbfcfe;}
        .constraint .type {font-size: .74rem; color: #52616f; font-weight: 700;}
        .constraint .body {font-size: .92rem; color: #0f2742; font-weight: 700; margin-top: .28rem; line-height: 1.38;}
        .constraint .role {font-size: .72rem; color: #7b8794; margin-top: .32rem;}
        .reason-list {display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .55rem;}
        .reason {border: 1px solid #d6e0ea; border-radius: 7px; padding: .68rem .72rem; background: #ffffff; min-height: 8.1rem;}
        .reason .title {font-weight: 760; color: #0f2742; font-size: .9rem; margin-bottom: .38rem;}
        .reason .body {font-size: .82rem; color: #52616f; line-height: 1.42;}
        .request-box {border-left: 4px solid #0f4c81; background: #f5f8fc; padding: .75rem .85rem; border-radius: 6px; font-size: .98rem;}
        .small-table-title {font-weight: 730; color: #0f2742; margin: .45rem 0 .25rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_constraints(constraints: List[Dict[str, Any]]) -> None:
    html_parts = ["<div class='constraint-grid'>"]
    for item in constraints:
        narrative = _constraint_narrative(item)
        html_parts.append(
            f"<div class='constraint'><div class='type'>{narrative['title']}</div><div class='body'>{narrative['body']}</div><div class='role'>{narrative['role']}</div></div>"
        )
    html_parts.append("</div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def _render_metrics(before_df: pd.DataFrame, after_df: pd.DataFrame) -> None:
    before_make = _metric_value(before_df, "makespan_hours", "max")
    after_make = _metric_value(after_df, "makespan_hours", "max")
    before_primary = int(_metric_value(before_df, "violations_primary_count"))
    after_primary = int(_metric_value(after_df, "violations_primary_count"))
    before_raw = int(_metric_value(before_df, "violations_raw_count"))
    after_raw = int(_metric_value(after_df, "violations_raw_count"))
    before_blocks = len(_metric_df(before_df))
    after_blocks = len(_metric_df(after_df))
    st.markdown(
        f"""
        <div class='metric-row'>
          <div class='metric-box'><div class='metric-label'>Makespan</div><div class='metric-value'>{after_make:.2f} h</div><div class='metric-delta'>{after_make - before_make:+.2f} h</div></div>
          <div class='metric-box'><div class='metric-label'>Primary violations</div><div class='metric-value'>{after_primary}</div><div class='metric-delta'>{after_primary - before_primary:+d}</div></div>
          <div class='metric-box'><div class='metric-label'>Raw events</div><div class='metric-value'>{after_raw}</div><div class='metric-delta'>{after_raw - before_raw:+d}</div></div>
          <div class='metric-box'><div class='metric-label'>Blocks</div><div class='metric-value'>{after_blocks}</div><div class='metric-delta'>baseline {before_blocks}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sequence(before_df: pd.DataFrame, after_df: pd.DataFrame) -> None:
    highlight = {3: "freeze", 2: "freeze", 1: "priority", 4: "delayed"}
    st.markdown("<div class='panel-title'>Before current-state baseline</div>", unsafe_allow_html=True)
    st.markdown(_seq_html(_sequence(before_df), highlight), unsafe_allow_html=True)
    st.markdown("<div class='panel-title' style='margin-top:.7rem;'>After request applied</div>", unsafe_allow_html=True)
    st.markdown(_seq_html(_sequence(after_df), highlight), unsafe_allow_html=True)


def _render_checks(solution_eval: Dict[str, Any]) -> None:
    st.markdown(
        " ".join(
            [
                _status_chip("요청 만족", solution_eval.get("request_satisfaction_ok")),
                _status_chip("현재상태 일관성", solution_eval.get("current_state_consistency_ok")),
                _status_chip("Final audit", solution_eval.get("final_audit_used")),
                _status_chip("운영 해석 가능", solution_eval.get("interpretation_flags", {}).get("use_metric_delta_as_operational_evidence")),
            ]
        ),
        unsafe_allow_html=True,
    )


def _render_reasoning(payload: Dict[str, Any]) -> None:
    reasons = _solution_reasoning(payload)
    parts = ["<div class='reason-list'>"]
    for item in reasons[:5]:
        parts.append(
            f"<div class='reason'><div class='title'>{item['title']}</div><div class='body'>{item['body']}</div></div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _presentation_checks_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        ctype = str(row.get("type") or "")
        if ctype == "freeze_prefix":
            item = {
                "검사 항목": "현재 작업 고정",
                "기대값": row.get("expected_prefix", row.get("expected_current_prefix", "")),
                "결과값": row.get("actual_after_prefix", row.get("actual_before_prefix", "")),
                "판정": "통과" if bool(row.get("ok")) else "확인 필요",
            }
        elif ctype == "priority_block":
            item = {
                "검사 항목": "긴급 블록 위치",
                "기대값": f"{row.get('block_id')}번이 고정 작업 직후",
                "결과값": f"위치 {row.get('actual_position')}",
                "판정": "통과" if bool(row.get("ok")) else "확인 필요",
            }
        elif ctype == "delayed_block":
            item = {
                "검사 항목": "고장 블록 지연",
                "기대값": f"{row.get('priority_before_ids')} 이후",
                "결과값": f"위치 {row.get('actual_position')}",
                "판정": "통과" if bool(row.get("ok")) else "확인 필요",
            }
        elif ctype == "manual_bay_assignment":
            item = {
                "검사 항목": "베이 지정",
                "기대값": row.get("expected_bay", ""),
                "결과값": row.get("actual_bay", ""),
                "판정": "통과" if bool(row.get("ok")) else "확인 필요",
            }
        else:
            item = {
                "검사 항목": CONSTRAINT_LABELS.get(ctype, ctype),
                "기대값": "",
                "결과값": "",
                "판정": "통과" if bool(row.get("ok")) else "확인 필요",
            }
        rows.append(item)
    return pd.DataFrame(rows)


def main() -> None:
    st.set_page_config(page_title="LLM Re-scheduling Demo", layout="wide")
    _render_css()

    st.markdown("<div class='top-title'>LLM-assisted Emergency Re-scheduling</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='subtle'>Panel-line scheduling demo · natural-language request to existing LPT/RL scheduler constraints · final audit grounded result</div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### Demo Control")
        parser_label = st.selectbox("Parser", ["Gemini", "Deterministic"], index=0)
        parser_mode = "gemini" if parser_label == "Gemini" else "deterministic"
        request_text = st.text_area("Operator request", value=DEFAULT_REQUEST, height=190)
        sample_dir_text = st.text_input("Result folder", value=str(DEFAULT_SAMPLE_DIR))
        run_clicked = st.button("Run demo", use_container_width=True)
        load_clicked = st.button("Load saved result", use_container_width=True)

    result_dir = Path(sample_dir_text).expanduser()
    if run_clicked:
        result_dir = RESULTS_ROOT / f"llm_presentation_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        with st.spinner("Running scheduler and final audit..."):
            completed = _run_demo(request_text, parser_mode, result_dir)
        if completed.returncode != 0:
            st.error("Demo run failed.")
            with st.expander("stderr", expanded=True):
                st.code(completed.stderr)
            return
        st.session_state["presentation_result_dir"] = str(result_dir)
    elif load_clicked:
        st.session_state["presentation_result_dir"] = str(result_dir)

    result_dir = Path(st.session_state.get("presentation_result_dir", str(result_dir)))
    payload = _load_result_dir(result_dir)
    if not payload["summary"]:
        st.warning("표시할 결과가 없습니다. 저장 결과를 불러오거나 demo를 실행하세요.")
        return

    solution_eval = payload["solution_eval"]
    before_df = payload["before_df"]
    after_df = payload["after_df"]
    request_payload = payload["request_json"]
    constraints = _constraints_from_request(request_payload)

    top_left, top_right = st.columns([1.1, 1.4], gap="large")
    with top_left:
        st.markdown("<div class='panel-title'>Operator Request</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='request-box'>{request_payload.get('request', {}).get('raw_request') or request_text}</div>", unsafe_allow_html=True)
        st.markdown("<div class='band'></div>", unsafe_allow_html=True)
        st.markdown("<div class='panel-title'>LLM이 변환한 실행 조건</div>", unsafe_allow_html=True)
        _render_constraints(constraints)
    with top_right:
        st.markdown("<div class='panel-title'>해 평가 결과</div>", unsafe_allow_html=True)
        _render_checks(solution_eval)
        _render_metrics(before_df, after_df)
        _render_sequence(before_df, after_df)

    st.markdown("<div class='band'></div>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>LLM 기반 해석 reasoning</div>", unsafe_allow_html=True)
    _render_reasoning(payload)

    st.markdown("<div class='band'></div>", unsafe_allow_html=True)
    lower_left, lower_mid, lower_right = st.columns([1.05, 1.0, 1.35], gap="large")
    with lower_left:
        st.markdown("<div class='small-table-title'>요청 반영 확인</div>", unsafe_allow_html=True)
        st.dataframe(_presentation_checks_table(payload["checks_df"]), use_container_width=True, hide_index=True)
    with lower_mid:
        st.markdown("<div class='small-table-title'>현재상태 확인</div>", unsafe_allow_html=True)
        st.dataframe(_presentation_checks_table(payload["current_df"]), use_container_width=True, hide_index=True)
    with lower_right:
        st.markdown("<div class='small-table-title'>감사 결과 기반 설명</div>", unsafe_allow_html=True)
        explanation = payload["summary"].get("explanation") or ""
        st.write(_clean_text(explanation))

    with st.expander("Files", expanded=False):
        st.json(
            {
                "result_dir": str(result_dir),
                "summary_json": str(result_dir / "summary.json"),
                "solution_evaluation_json": str(result_dir / "solution_evaluation.json"),
                "before_csv": str(result_dir / "before_results.csv"),
                "after_csv": str(result_dir / "after_results.csv"),
            }
        )


if __name__ == "__main__":
    main()
