#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export Excel-ready CSV files from generated eval results."""

# [AGENT-ADD] This keeps plotting input reproducible outside Python.

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Dict, List

import pandas as pd


METHOD_LABELS: Dict[str, str] = {
    "SPT": "SPT",
    "SEAM_MIN": "MSF",
    "LPT": "LPT",
    "GA": "GA",
    "RL": "Proposed",
}
# [AGENT-EDIT] Include GA when exporting combined all-method result folders.
METHOD_ORDER: List[str] = ["SPT", "SEAM_MIN", "LPT", "GA", "RL"]
PROFILE_ORDER: List[str] = ["base", "ps_heavy", "sub_heavy", "mixed_ps_sub", "heavy_work", "overload"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Excel-ready CSVs from method_results.csv.")
    parser.add_argument("result_dir", help="Directory containing method_results.csv")
    parser.add_argument("--input", default="method_results.csv")
    parser.add_argument("--output_dir", default="excel_ready")
    parser.add_argument("--include_mask_off", action="store_true")
    return parser.parse_args()


def _size_bucket(blocks: float) -> str:
    try:
        val = float(blocks)
    except (TypeError, ValueError):
        return "Unknown"
    if val <= 80:
        return "Small (20-80)"
    if val <= 140:
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
    df["profile_order"] = df["distribution_profile"].map({name: idx for idx, name in enumerate(PROFILE_ORDER)}).fillna(999).astype(int)
    df["method_order"] = df["method"].map({name: idx for idx, name in enumerate(METHOD_ORDER)}).fillna(999).astype(int)
    df = df.sort_values(["total_blocks", "profile_order", "method_order"]).reset_index(drop=True)
    return df


def _summary(df: pd.DataFrame, group_cols: List[str]) -> pd.DataFrame:
    return (
        df.groupby(group_cols, dropna=False)
        .agg(
            n=("makespan_hours", "count"),
            makespan_mean=("makespan_hours", "mean"),
            makespan_std=("makespan_hours", "std"),
            makespan_min=("makespan_hours", "min"),
            makespan_q1=("makespan_hours", lambda s: s.quantile(0.25)),
            makespan_median=("makespan_hours", "median"),
            makespan_q3=("makespan_hours", lambda s: s.quantile(0.75)),
            makespan_max=("makespan_hours", "max"),
            violations_mean=("violations", "mean"),
            violations_std=("violations", "std"),
            violations_min=("violations", "min"),
            violations_q1=("violations", lambda s: s.quantile(0.25)),
            violations_median=("violations", "median"),
            violations_q3=("violations", lambda s: s.quantile(0.75)),
            violations_max=("violations", "max"),
        )
        .reset_index()
    )


def _wide_box_data(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    block_counts = sorted(int(v) for v in df["total_blocks"].dropna().unique())
    rows: List[Dict[str, object]] = []
    for profile in PROFILE_ORDER:
        row: Dict[str, object] = {"distribution_profile": profile}
        for block_count in block_counts:
            for method in METHOD_ORDER:
                label = METHOD_LABELS[method]
                subset = df[
                    (df["distribution_profile"] == profile)
                    & (df["total_blocks"] == block_count)
                    & (df["method"] == method)
                ]
                column = f"b{block_count:03d}_{label}"
                row[column] = float(subset[metric].iloc[0]) if not subset.empty else ""
        rows.append(row)
    return pd.DataFrame(rows)


def _mean_pivot(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    pivot = (
        df.pivot_table(
            index="total_blocks",
            columns="method_label",
            values=metric,
            aggfunc="mean",
        )
        .reset_index()
    )
    ordered_cols = ["total_blocks"] + [METHOD_LABELS[m] for m in METHOD_ORDER if METHOD_LABELS[m] in pivot.columns]
    return pivot[ordered_cols]


def main() -> None:
    args = _parse_args()
    result_dir = Path(args.result_dir).resolve()
    out_dir = result_dir / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _load_data(result_dir, args.input, include_mask_off=args.include_mask_off)
    suffix = "with_mask_off" if args.include_mask_off else "no_mask_off"

    outputs: List[Path] = []

    long_cols = [
        "gen", "grid_case_id", "distribution_profile", "method", "method_label",
        "selection_variant", "makespan_hours", "violations", "total_blocks",
        "size_bucket", "spread_days", "actual_util", "ps_ratio_actual",
        "sub_ratio_actual", "seam_scale", "tact_time_scale", "length_scale",
        "width_scale", "thickness_scale", "result_csv_name", "result_folder",
    ]
    long_path = out_dir / f"excel_long_{suffix}.csv"
    df[[col for col in long_cols if col in df.columns]].to_csv(long_path, index=False, encoding="utf-8-sig")
    outputs.append(long_path)

    summary_specs = [
        ("block_count", ["total_blocks", "method", "method_label"]),
        ("size_bucket", ["size_bucket", "method", "method_label"]),
        ("distribution_profile", ["distribution_profile", "method", "method_label"]),
    ]
    for name, cols in summary_specs:
        path = out_dir / f"excel_{name}_summary_{suffix}.csv"
        _summary(df, cols).to_csv(path, index=False, encoding="utf-8-sig")
        outputs.append(path)

    for metric in ("makespan_hours", "violations"):
        wide_path = out_dir / f"excel_box_{metric}_by_block_count_wide_{suffix}.csv"
        _wide_box_data(df, metric).to_csv(wide_path, index=False, encoding="utf-8-sig")
        outputs.append(wide_path)

        pivot_path = out_dir / f"excel_mean_{metric}_by_block_count_pivot_{suffix}.csv"
        _mean_pivot(df, metric).to_csv(pivot_path, index=False, encoding="utf-8-sig")
        outputs.append(pivot_path)

    manifest_path = out_dir / f"excel_manifest_{suffix}.csv"
    pd.DataFrame([
        {"file": path.name, "rows": pd.read_csv(path).shape[0], "columns": pd.read_csv(path).shape[1]}
        for path in outputs
    ]).to_csv(manifest_path, index=False, encoding="utf-8-sig")
    outputs.append(manifest_path)

    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
