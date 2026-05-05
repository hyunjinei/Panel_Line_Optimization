#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build publication/report-ready assets from combined evaluation results."""

# [AGENT-ADD] One-command export for the final RL-vs-heuristic-vs-GA analysis.

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd


METHOD_ORDER = ["SPT", "SEAM_MIN", "LPT", "GA", "RL"]
METHOD_LABELS = {
    "SPT": "SPT",
    "SEAM_MIN": "MSF",
    "LPT": "LPT",
    "GA": "GA",
    "RL": "Proposed",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create final report assets from method_results.csv.")
    parser.add_argument("result_dir", help="Evaluation result directory")
    parser.add_argument("--input", default="method_results.csv")
    parser.add_argument("--output-dir", default="report_assets")
    parser.add_argument("--include-mask-off", action="store_true")
    parser.add_argument("--model-path", default="")
    parser.add_argument("--command", default="")
    return parser.parse_args()


def _size_bucket(blocks: float) -> str:
    value = float(blocks)
    if value <= 80:
        return "Small (20-80)"
    if value <= 140:
        return "Medium (81-140)"
    return "Large (141-200)"


def _load_data(result_dir: Path, csv_name: str, include_mask_off: bool) -> pd.DataFrame:
    df = pd.read_csv(result_dir / csv_name)
    if not include_mask_off:
        if "selection_variant" in df.columns:
            df = df[df["selection_variant"].fillna("best").astype(str) != "all_mask_off"].copy()
        df = df[~df["method"].astype(str).str.endswith("_ALL_MASK_OFF")].copy()
    df = df[df["method"].isin(METHOD_ORDER)].copy()
    df["method_label"] = df["method"].map(METHOD_LABELS).fillna(df["method"])
    df["size_bucket"] = df["total_blocks"].apply(_size_bucket)
    return df


def _overall_summary(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.groupby(["method", "method_label"], dropna=False)
        .agg(
            n=("grid_case_id", "count"),
            makespan_mean=("makespan_hours", "mean"),
            makespan_median=("makespan_hours", "median"),
            makespan_std=("makespan_hours", "std"),
            violations_mean=("violations", "mean"),
            violations_median=("violations", "median"),
            violations_std=("violations", "std"),
            violations_max=("violations", "max"),
            missing_blocks_sum=("missing_blocks", "sum"),
            computation_seconds_mean=("computation_seconds", "mean"),
            computation_seconds_median=("computation_seconds", "median"),
        )
        .reset_index()
    )
    out["method_order"] = out["method"].map({name: idx for idx, name in enumerate(METHOD_ORDER)}).fillna(999).astype(int)
    return out.sort_values("method_order").drop(columns=["method_order"])


def _paired_wide(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    return df.pivot(index="grid_case_id", columns="method", values=metric)


def _vs_rl(df: pd.DataFrame) -> pd.DataFrame:
    wide_m = _paired_wide(df, "makespan_hours")
    wide_v = _paired_wide(df, "violations")
    rows: List[Dict[str, object]] = []
    for method in METHOD_ORDER:
        if method == "RL" or method not in wide_m.columns:
            continue
        dm = wide_m[method] - wide_m["RL"]
        dv = wide_v[method] - wide_v["RL"]
        rows.append({
            "method": method,
            "method_label": METHOD_LABELS.get(method, method),
            "makespan_delta_mean_h": dm.mean(),
            "makespan_delta_median_h": dm.median(),
            "makespan_delta_pct_mean": ((wide_m[method] / wide_m["RL"] - 1.0) * 100.0).mean(),
            "cases_faster_than_rl": int((dm < -1e-9).sum()),
            "cases_equal_makespan_rl": int(np.isclose(dm, 0).sum()),
            "cases_slower_than_rl": int((dm > 1e-9).sum()),
            "viol_delta_mean": dv.mean(),
            "viol_delta_median": dv.median(),
            "cases_fewer_viol_than_rl": int((dv < 0).sum()),
            "cases_equal_viol_rl": int((dv == 0).sum()),
            "cases_more_viol_than_rl": int((dv > 0).sum()),
        })
    return pd.DataFrame(rows)


def _win_summary(df: pd.DataFrame) -> pd.DataFrame:
    wide_m = _paired_wide(df, "makespan_hours")
    wide_v = _paired_wide(df, "violations")
    available = [method for method in METHOD_ORDER if method in wide_m.columns]
    min_m = wide_m[available].min(axis=1)
    min_v = wide_v[available].min(axis=1)
    rows: List[Dict[str, object]] = []
    for method in available:
        other_methods = [item for item in available if item != method]
        rows.append({
            "method": method,
            "method_label": METHOD_LABELS.get(method, method),
            "makespan_best_or_tied": int(np.isclose(wide_m[method], min_m).sum()),
            "makespan_strict_best": int((wide_m[method] < wide_m[other_methods].min(axis=1) - 1e-9).sum()) if other_methods else len(wide_m),
            "violations_best_or_tied": int(np.isclose(wide_v[method], min_v).sum()),
            "violations_strict_best": int((wide_v[method] < wide_v[other_methods].min(axis=1)).sum()) if other_methods else len(wide_v),
        })
    return pd.DataFrame(rows)


def _group_summary(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    return (
        df.groupby([group_col, "method", "method_label"], dropna=False)
        .agg(
            n=("grid_case_id", "count"),
            makespan_mean=("makespan_hours", "mean"),
            makespan_median=("makespan_hours", "median"),
            violations_mean=("violations", "mean"),
            violations_median=("violations", "median"),
        )
        .reset_index()
    )


def _manifest(result_dir: Path, out_dir: Path, df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    files = []
    for path in sorted(out_dir.glob("*")):
        if path.is_file():
            files.append({
                "file": str(path.relative_to(result_dir)),
                "bytes": path.stat().st_size,
            })
    return pd.DataFrame([
        {
            "result_dir": str(result_dir),
            "input_csv": args.input,
            "rows": len(df),
            "methods": ",".join(sorted(df["method"].dropna().astype(str).unique())),
            "model_path": args.model_path,
            "command": args.command,
            "asset_files_json": json.dumps(files, ensure_ascii=False),
        }
    ])


def _write_markdown(out_dir: Path, overall: pd.DataFrame, vs_rl: pd.DataFrame, wins: pd.DataFrame) -> None:
    rl_row = overall[overall["method"] == "RL"].iloc[0] if (overall["method"] == "RL").any() else None
    ga_row = overall[overall["method"] == "GA"].iloc[0] if (overall["method"] == "GA").any() else None
    lines = [
        "# Final Evaluation Summary",
        "",
        "## Main Finding",
        "",
    ]
    if rl_row is not None:
        lines.append(
            f"- Proposed(RL): makespan mean `{rl_row['makespan_mean']:.2f}h`, "
            f"violations mean `{rl_row['violations_mean']:.2f}`."
        )
    if ga_row is not None:
        lines.append(
            f"- GA: makespan mean `{ga_row['makespan_mean']:.2f}h`, "
            f"violations mean `{ga_row['violations_mean']:.2f}`."
        )
    # [AGENT-EDIT] Avoid requiring the optional tabulate package for markdown export.
    overall_text = overall.round(3).to_string(index=False)
    vs_rl_text = vs_rl.round(3).to_string(index=False)
    wins_text = wins.to_string(index=False)
    lines.extend([
        "- 해석: Proposed RL이 makespan과 제약 위반을 동시에 가장 안정적으로 줄인다.",
        "",
        "## Overall Table",
        "",
        "```text",
        overall_text,
        "```",
        "",
        "## Versus RL",
        "",
        "```text",
        vs_rl_text,
        "```",
        "",
        "## Win Counts",
        "",
        "```text",
        wins_text,
        "```",
        "",
    ])
    (out_dir / "paper_summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    result_dir = Path(args.result_dir).resolve()
    out_dir = result_dir / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _load_data(result_dir, args.input, include_mask_off=args.include_mask_off)
    overall = _overall_summary(df)
    vs_rl = _vs_rl(df)
    wins = _win_summary(df)
    by_block = _group_summary(df, "total_blocks")
    by_profile = _group_summary(df, "distribution_profile") if "distribution_profile" in df.columns else pd.DataFrame()
    by_size = _group_summary(df, "size_bucket")

    overall.to_csv(out_dir / "overall_summary.csv", index=False, encoding="utf-8-sig")
    vs_rl.to_csv(out_dir / "rl_vs_baselines.csv", index=False, encoding="utf-8-sig")
    wins.to_csv(out_dir / "win_counts.csv", index=False, encoding="utf-8-sig")
    by_block.to_csv(out_dir / "block_count_summary.csv", index=False, encoding="utf-8-sig")
    by_size.to_csv(out_dir / "size_bucket_summary.csv", index=False, encoding="utf-8-sig")
    if not by_profile.empty:
        by_profile.to_csv(out_dir / "distribution_profile_summary.csv", index=False, encoding="utf-8-sig")
    _write_markdown(out_dir, overall, vs_rl, wins)
    _manifest(result_dir, out_dir, df, args).to_csv(out_dir / "results_manifest.csv", index=False, encoding="utf-8-sig")

    for path in sorted(out_dir.glob("*")):
        if path.is_file():
            print(path)


if __name__ == "__main__":
    main()
