#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export presentation-ready CSV tables for synthetic and practical cases."""

# [AGENT-ADD] Builds the exact table inputs requested for PPT/Excel plotting:
# synthetic size/profile summaries and practical makespan/violation/Bay-longi tables.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


METHOD_LABELS: Dict[str, str] = {
    "SPT": "SPT",
    "SEAM_MIN": "MSF",
    "LPT": "LPT",
    "GA": "GA",
    "RL": "Proposed",
}
METHOD_ORDER: List[str] = ["SPT", "SEAM_MIN", "LPT", "GA", "RL"]
PROFILE_ORDER: List[str] = ["base", "ps_heavy", "sub_heavy", "mixed_ps_sub", "heavy_work", "overload"]
SIZE_ORDER: List[str] = ["Small (20-80)", "Medium (81-140)", "Large (141-200)"]

PRACTICAL_FILES: Dict[str, Tuple[str, str]] = {
    "EXCEL": ("Actual", "excel_evaluation_results.csv"),
    "ACTIONMASKING": ("ActionMasking", "actionmasking_evaluation_results.csv"),
    "SPT": ("SPT", "spt_evaluation_results.csv"),
    "SEAM_MIN": ("MSF", "seam_min_evaluation_results.csv"),
    "LPT": ("LPT", "lpt_evaluation_results.csv"),
    "GA": ("GA", "ga_evaluation_results.csv"),
    "RL": ("Proposed", "rl_best_pm_results.csv"),
}
PRACTICAL_ORDER: List[str] = ["EXCEL", "ACTIONMASKING", "SPT", "SEAM_MIN", "LPT", "GA", "RL"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create presentation CSV tables.")
    parser.add_argument("--synthetic-results", default="PPO/eval/all_methods/method_results.csv")
    parser.add_argument("--practical-dir", default="PPO/eval/20250609_0429_12_46_seed42")
    parser.add_argument("--practical-ga-dir", default="")
    parser.add_argument("--output-dir", default="PPO/eval/all_methods/report_assets")
    parser.add_argument("--include-mask-off", action="store_true")
    return parser.parse_args()


def _size_bucket(blocks: object) -> str:
    try:
        value = float(blocks)
    except (TypeError, ValueError):
        return "Unknown"
    if value <= 80:
        return "Small (20-80)"
    if value <= 140:
        return "Medium (81-140)"
    return "Large (141-200)"


def _ordered(df: pd.DataFrame, source_col: str, order_col: str, values: Iterable[str]) -> pd.DataFrame:
    order = {value: idx for idx, value in enumerate(values)}
    df[order_col] = df[source_col].map(order).fillna(999).astype(int)
    return df


def _load_synthetic(path: Path, include_mask_off: bool) -> pd.DataFrame:
    df = pd.read_csv(path)
    if not include_mask_off:
        if "selection_variant" in df.columns:
            df = df[df["selection_variant"].fillna("best").astype(str) != "all_mask_off"].copy()
        df = df[~df["method"].astype(str).str.endswith("_ALL_MASK_OFF")].copy()
    df = df[df["method"].isin(METHOD_ORDER)].copy()
    df["method_label"] = df["method"].map(METHOD_LABELS).fillna(df["method"])
    df["size_bucket"] = df["total_blocks"].apply(_size_bucket)
    df = _ordered(df, "method", "method_order", METHOD_ORDER)
    df = _ordered(df, "distribution_profile", "profile_order", PROFILE_ORDER)
    df = _ordered(df, "size_bucket", "size_order", SIZE_ORDER)
    return df.sort_values(["size_order", "profile_order", "total_blocks", "method_order"]).reset_index(drop=True)


def _summary(df: pd.DataFrame, group_cols: List[str]) -> pd.DataFrame:
    out = (
        df.groupby(group_cols, dropna=False)
        .agg(
            n=("grid_case_id", "count"),
            block_count_min=("total_blocks", "min"),
            block_count_max=("total_blocks", "max"),
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
    if "size_bucket" in out.columns:
        out = _ordered(out, "size_bucket", "size_order", SIZE_ORDER)
    if "distribution_profile" in out.columns:
        out = _ordered(out, "distribution_profile", "profile_order", PROFILE_ORDER)
    if "method" in out.columns:
        out = _ordered(out, "method", "method_order", METHOD_ORDER)
    sort_cols = [col for col in ["size_order", "profile_order", "method_order", "total_blocks"] if col in out.columns]
    return out.sort_values(sort_cols).reset_index(drop=True)


def _wide_metric(df: pd.DataFrame, index_cols: List[str], metric: str) -> pd.DataFrame:
    pivot = (
        df.pivot_table(index=index_cols, columns="method_label", values=metric, aggfunc="mean")
        .reset_index()
    )
    desired = index_cols + [METHOD_LABELS[m] for m in METHOD_ORDER if METHOD_LABELS[m] in pivot.columns]
    return pivot[desired]


def _synthetic_outputs(df: pd.DataFrame, out_dir: Path) -> List[Path]:
    outputs: List[Path] = []
    long_cols = [
        "source_run", "gen", "seed", "grid_case_id", "distribution_profile", "size_bucket",
        "total_blocks", "method", "method_label", "selection_variant", "makespan_hours",
        "violations", "missing_blocks", "spread_days", "util_target", "actual_util",
        "ps_ratio_actual", "sub_ratio_actual", "seam_scale", "tact_time_scale",
        "length_scale", "width_scale", "thickness_scale", "result_csv_name", "result_folder",
    ]
    path = out_dir / "synthetic_all_graph_values_long.csv"
    df[[col for col in long_cols if col in df.columns]].to_csv(path, index=False, encoding="utf-8-sig")
    outputs.append(path)

    summary_specs = [
        ("synthetic_size_profile_summary.csv", ["size_bucket", "distribution_profile", "method", "method_label"]),
        ("synthetic_block_profile_summary.csv", ["total_blocks", "distribution_profile", "method", "method_label"]),
        ("synthetic_size_summary.csv", ["size_bucket", "method", "method_label"]),
        ("synthetic_profile_summary.csv", ["distribution_profile", "method", "method_label"]),
    ]
    for name, cols in summary_specs:
        path = out_dir / name
        _summary(df, cols).to_csv(path, index=False, encoding="utf-8-sig")
        outputs.append(path)

    wide_specs = [
        ("synthetic_size_profile_makespan_wide.csv", ["size_bucket", "distribution_profile"], "makespan_hours"),
        ("synthetic_size_profile_violations_wide.csv", ["size_bucket", "distribution_profile"], "violations"),
        ("synthetic_block_profile_makespan_wide.csv", ["total_blocks", "distribution_profile"], "makespan_hours"),
        ("synthetic_block_profile_violations_wide.csv", ["total_blocks", "distribution_profile"], "violations"),
        ("synthetic_block_count_makespan_wide.csv", ["total_blocks"], "makespan_hours"),
        ("synthetic_block_count_violations_wide.csv", ["total_blocks"], "violations"),
    ]
    for name, cols, metric in wide_specs:
        path = out_dir / name
        _wide_metric(df, cols, metric).to_csv(path, index=False, encoding="utf-8-sig")
        outputs.append(path)

    ranges = (
        df.groupby(["size_bucket", "distribution_profile"], dropna=False)
        .agg(
            block_count_min=("total_blocks", "min"),
            block_count_max=("total_blocks", "max"),
            block_count_values=("total_blocks", lambda s: ",".join(str(int(v)) for v in sorted(set(s.dropna())))),
            cases=("grid_case_id", "nunique"),
        )
        .reset_index()
    )
    ranges = _ordered(ranges, "size_bucket", "size_order", SIZE_ORDER)
    ranges = _ordered(ranges, "distribution_profile", "profile_order", PROFILE_ORDER)
    path = out_dir / "synthetic_size_profile_block_ranges.csv"
    ranges.sort_values(["size_order", "profile_order"]).to_csv(path, index=False, encoding="utf-8-sig")
    outputs.append(path)
    return outputs


def _metric_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "is_metric_anchor_row" in out.columns:
        text = out["is_metric_anchor_row"].astype(str).str.lower()
        mask = text.isin({"true", "1", "yes"})
        if mask.any():
            out = out[mask].copy()
    return out


def _read_result(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def _bay_longi(df: pd.DataFrame) -> Dict[str, float]:
    if "assigned_bay" not in df.columns or "longi_count" not in df.columns:
        return {
            "bay_35a_longi_total": 0,
            "bay_36b_longi_total": 0,
            "bay_35a_block_count": 0,
            "bay_36b_block_count": 0,
            "total_longi_count": 0,
            "longi_balance_abs_diff": 0,
            "longi_balance_ratio_35a": 0.0,
            "longi_balance_ratio_36b": 0.0,
        }
    longi_df = df[["block_id", "assigned_bay", "longi_count"]].copy()
    longi_df["assigned_bay"] = longi_df["assigned_bay"].astype(str).str.upper()
    longi_df["longi_count"] = pd.to_numeric(longi_df["longi_count"], errors="coerce").fillna(0)
    if "block_id" in longi_df.columns:
        longi_df = longi_df.sort_values(["block_id", "assigned_bay"]).drop_duplicates(subset=["block_id"], keep="last")
    bay_35a = longi_df[longi_df["assigned_bay"].str.contains("35A", na=False)]
    bay_36b = longi_df[longi_df["assigned_bay"].str.contains("36B", na=False)]
    a_total = float(bay_35a["longi_count"].sum())
    b_total = float(bay_36b["longi_count"].sum())
    total = a_total + b_total
    return {
        "bay_35a_longi_total": a_total,
        "bay_36b_longi_total": b_total,
        "bay_35a_block_count": int(len(bay_35a)),
        "bay_36b_block_count": int(len(bay_36b)),
        "total_longi_count": total,
        "longi_balance_abs_diff": abs(a_total - b_total),
        "longi_balance_ratio_35a": a_total / total if total else 0.0,
        "longi_balance_ratio_36b": b_total / total if total else 0.0,
    }


def _practical_makespan_hours(df: pd.DataFrame, metric_df: pd.DataFrame) -> Tuple[float, float]:
    # [AGENT-EDIT] Practical case makespan should mean the global elapsed schedule span, not only the
    # largest within-day row value. Keep the row max as a separate diagnostic column for Excel checks.
    row_makespan = pd.to_numeric(metric_df.get("makespan_hours", pd.Series(dtype=float)), errors="coerce").max()
    if pd.isna(row_makespan):
        row_makespan = pd.to_numeric(df.get("makespan_hours", pd.Series(dtype=float)), errors="coerce").max()
    row_value = float(row_makespan) if pd.notna(row_makespan) else 0.0

    if "panel_start_time" in metric_df.columns and "final_end_time" in metric_df.columns:
        starts = pd.to_datetime(metric_df["panel_start_time"], errors="coerce")
        ends = pd.to_datetime(metric_df["final_end_time"], errors="coerce")
        if starts.notna().any() and ends.notna().any():
            global_hours = (ends.max() - starts.min()).total_seconds() / 3600.0
            if global_hours > 0:
                return float(global_hours), row_value
    return row_value, row_value


def _practical_outputs(practical_dir: Path, out_dir: Path, practical_ga_dir: Optional[Path] = None) -> List[Path]:
    # [AGENT-EDIT] GA practical run can live in a separate result folder because it is often run after the
    # main Actual/heuristic/RL practical evaluation. Merge it into the same presentation tables when provided.
    summary_rows: List[Dict[str, object]] = []
    bay_rows: List[Dict[str, object]] = []
    plot_rows: List[Dict[str, object]] = []

    for method_order, method in enumerate(PRACTICAL_ORDER):
        label, filename = PRACTICAL_FILES[method]
        source_dir = practical_ga_dir if method == "GA" and practical_ga_dir else practical_dir
        df = _read_result(source_dir / filename)
        if df is None:
            continue
        metric_df = _metric_rows(df)
        makespan, max_row_makespan = _practical_makespan_hours(df, metric_df)
        if "violations_representative" in metric_df.columns:
            violations = pd.to_numeric(metric_df["violations_representative"], errors="coerce").fillna(0).sum()
        elif "violations_primary_count" in metric_df.columns:
            violations = pd.to_numeric(metric_df["violations_primary_count"], errors="coerce").fillna(0).sum()
        elif "violations" in metric_df.columns:
            violations = pd.to_numeric(metric_df["violations"], errors="coerce").fillna(0).sum()
        else:
            violations = 0
        raw_violations = (
            pd.to_numeric(metric_df["violations_raw_count"], errors="coerce").fillna(0).sum()
            if "violations_raw_count" in metric_df.columns
            else 0
        )
        bay_stats = _bay_longi(df)
        row = {
            "source_dir": str(source_dir),
            "method": method,
            "method_label": label,
            "method_order": method_order,
            "result_file": filename,
            "rows": len(df),
            "metric_rows": len(metric_df),
            "makespan_hours": makespan,
            "max_row_makespan_hours": max_row_makespan,
            "violations": int(violations),
            "raw_violations": int(raw_violations),
            **bay_stats,
        }
        summary_rows.append(row)
        bay_rows.extend([
            {
                "method": method,
                "method_label": label,
                "method_order": method_order,
                "bay": "35A",
                "longi_total": bay_stats["bay_35a_longi_total"],
                "block_count": bay_stats["bay_35a_block_count"],
                "longi_ratio": bay_stats["longi_balance_ratio_35a"],
            },
            {
                "method": method,
                "method_label": label,
                "method_order": method_order,
                "bay": "36B",
                "longi_total": bay_stats["bay_36b_longi_total"],
                "block_count": bay_stats["bay_36b_block_count"],
                "longi_ratio": bay_stats["longi_balance_ratio_36b"],
            },
        ])
        plot_rows.extend([
            {"method": method, "method_label": label, "method_order": method_order, "metric": "Makespan", "value": row["makespan_hours"], "unit": "h"},
            {"method": method, "method_label": label, "method_order": method_order, "metric": "Violation", "value": row["violations"], "unit": "count"},
            {"method": method, "method_label": label, "method_order": method_order, "metric": "Bay 35A longi", "value": bay_stats["bay_35a_longi_total"], "unit": "count"},
            {"method": method, "method_label": label, "method_order": method_order, "metric": "Bay 36B longi", "value": bay_stats["bay_36b_longi_total"], "unit": "count"},
        ])

    outputs: List[Path] = []
    for name, rows in [
        ("practical_case_summary.csv", summary_rows),
        ("practical_case_bay_longi_distribution.csv", bay_rows),
        ("practical_case_plot_values_long.csv", plot_rows),
    ]:
        path = out_dir / name
        pd.DataFrame(rows).sort_values([col for col in ["method_order", "bay", "metric"] if rows and col in rows[0]]).to_csv(
            path,
            index=False,
            encoding="utf-8-sig",
        )
        outputs.append(path)
    return outputs


def main() -> None:
    args = _parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs: List[Path] = []
    synthetic = _load_synthetic(Path(args.synthetic_results), args.include_mask_off)
    outputs.extend(_synthetic_outputs(synthetic, out_dir))
    practical_ga_dir = Path(args.practical_ga_dir) if args.practical_ga_dir else None
    outputs.extend(_practical_outputs(Path(args.practical_dir), out_dir, practical_ga_dir))

    manifest = pd.DataFrame(
        [{"file": path.name, "rows": pd.read_csv(path).shape[0], "columns": pd.read_csv(path).shape[1]} for path in outputs]
    )
    manifest_path = out_dir / "presentation_tables_manifest.csv"
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")
    outputs.append(manifest_path)

    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
