#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Combine compatible generated-eval result folders."""

# [AGENT-ADD] Merge heuristic/RL and GA runs generated from the same case grid.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd


CASE_KEY_COLUMNS: List[str] = [
    "grid_case_id",
    "seed",
    "total_blocks",
    "distribution_profile",
    "ps_ratio_target",
    "sub_ratio_target",
    "seam_scale",
    "tact_time_scale",
    "length_scale",
    "width_scale",
    "thickness_scale",
    "spread_days",
]

METHOD_LABELS: Dict[str, str] = {
    "SPT": "SPT",
    "SEAM_MIN": "MSF",
    "LPT": "LPT",
    "GA": "GA",
    "RL": "Proposed",
}
METHOD_ORDER: List[str] = ["SPT", "SEAM_MIN", "LPT", "GA", "RL"]
PROFILE_ORDER: List[str] = ["base", "ps_heavy", "sub_heavy", "mixed_ps_sub", "heavy_work", "overload"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Combine generated eval result folders.")
    parser.add_argument("result_dirs", nargs="+", help="Input eval result directories")
    parser.add_argument("--output", default="PPO/eval/all_methods", help="Combined output directory")
    parser.add_argument("--allow_mismatch", action="store_true", help="Allow different gen_parameters grids")
    return parser.parse_args()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def _available_case_columns(df: pd.DataFrame) -> List[str]:
    return [col for col in CASE_KEY_COLUMNS if col in df.columns]


def _normalise_cases(df: pd.DataFrame) -> pd.DataFrame:
    cols = _available_case_columns(df)
    if not cols:
        raise ValueError("gen_parameters.csv has no compatible case key columns.")
    normalised = df[cols].copy()
    return normalised.sort_values(cols).reset_index(drop=True)


def _validate_same_grid(input_dirs: Iterable[Path], allow_mismatch: bool) -> pd.DataFrame:
    reference_dir: Path | None = None
    reference_cases: pd.DataFrame | None = None
    reference_gen: pd.DataFrame | None = None
    rows: List[Dict[str, object]] = []

    for result_dir in input_dirs:
        gen = _read_csv(result_dir / "gen_parameters.csv")
        cases = _normalise_cases(gen)
        rows.append({
            "source_run": result_dir.name,
            "gen_rows": len(gen),
            "case_key_rows": len(cases),
            "matches_reference": True if reference_cases is None else bool(cases.equals(reference_cases)),
        })
        if reference_cases is None:
            reference_dir = result_dir
            reference_cases = cases
            reference_gen = gen
            continue
        if not cases.equals(reference_cases) and not allow_mismatch:
            raise ValueError(
                f"gen_parameters grid mismatch: {result_dir} does not match {reference_dir}. "
                "Use --allow_mismatch only for exploratory joins."
            )

    if reference_gen is None:
        raise ValueError("No input result directories were provided.")
    return reference_gen, pd.DataFrame(rows)


def _add_method_metadata(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["method"] = out["method"].astype(str)
    out["method_label"] = out["method"].map(METHOD_LABELS).fillna(out["method"])
    out["method_order"] = out["method"].map({name: idx for idx, name in enumerate(METHOD_ORDER)}).fillna(999).astype(int)
    out["profile_order"] = (
        out["distribution_profile"].map({name: idx for idx, name in enumerate(PROFILE_ORDER)}).fillna(999).astype(int)
        if "distribution_profile" in out.columns
        else 999
    )
    return out


def _drop_mask_off_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "selection_variant" in out.columns:
        out = out[out["selection_variant"].fillna("best").astype(str) != "all_mask_off"].copy()
    out = out[~out["method"].astype(str).str.endswith("_ALL_MASK_OFF")].copy()
    return out


def _summary(df: pd.DataFrame, group_cols: List[str]) -> pd.DataFrame:
    return (
        df.groupby(group_cols, dropna=False)
        .agg(
            n=("makespan_hours", "count"),
            makespan_mean=("makespan_hours", "mean"),
            makespan_std=("makespan_hours", "std"),
            makespan_min=("makespan_hours", "min"),
            makespan_median=("makespan_hours", "median"),
            makespan_max=("makespan_hours", "max"),
            violations_mean=("violations", "mean"),
            violations_std=("violations", "std"),
            violations_min=("violations", "min"),
            violations_median=("violations", "median"),
            violations_max=("violations", "max"),
            computation_seconds_mean=("computation_seconds", "mean") if "computation_seconds" in df.columns else ("makespan_hours", "count"),
        )
        .reset_index()
    )


def _write_summaries(df: pd.DataFrame, out_dir: Path) -> None:
    if "total_blocks" in df.columns:
        _summary(df, ["total_blocks", "method", "method_label"]).to_csv(
            out_dir / "block_count_summary.csv", index=False, encoding="utf-8-sig"
        )
    if "distribution_profile" in df.columns:
        _summary(df, ["distribution_profile", "method", "method_label"]).to_csv(
            out_dir / "distribution_profile_summary.csv", index=False, encoding="utf-8-sig"
        )
    if "grid_case_id" in df.columns:
        _summary(df, ["grid_case_id", "total_blocks", "distribution_profile", "method", "method_label"]).to_csv(
            out_dir / "grid_case_summary.csv", index=False, encoding="utf-8-sig"
        )
    if "util_bucket" in df.columns:
        _summary(df, ["util_bucket", "method", "method_label"]).to_csv(
            out_dir / "util_bucket_summary.csv", index=False, encoding="utf-8-sig"
        )
    if "size_bucket" in df.columns:
        _summary(df, ["size_bucket", "method", "method_label"]).to_csv(
            out_dir / "size_bucket_summary.csv", index=False, encoding="utf-8-sig"
        )


def main() -> None:
    args = _parse_args()
    input_dirs = [Path(item).resolve() for item in args.result_dirs]
    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    reference_gen, grid_check = _validate_same_grid(input_dirs, allow_mismatch=args.allow_mismatch)
    frames: List[pd.DataFrame] = []
    for result_dir in input_dirs:
        frame = _read_csv(result_dir / "method_results.csv")
        frame.insert(0, "source_run", result_dir.name)
        frames.append(frame)

    combined_with_mask_off = _add_method_metadata(pd.concat(frames, ignore_index=True, sort=False))
    combined_plot = _add_method_metadata(_drop_mask_off_rows(combined_with_mask_off))
    combined_plot = combined_plot.sort_values(["total_blocks", "profile_order", "method_order", "source_run"]).reset_index(drop=True)
    combined_with_mask_off = combined_with_mask_off.sort_values(
        ["total_blocks", "profile_order", "method_order", "source_run", "method"]
    ).reset_index(drop=True)

    reference_gen.to_csv(out_dir / "gen_parameters.csv", index=False, encoding="utf-8-sig")
    grid_check.to_csv(out_dir / "source_runs.csv", index=False, encoding="utf-8-sig")
    combined_with_mask_off.to_csv(out_dir / "method_results_with_mask_off.csv", index=False, encoding="utf-8-sig")
    combined_plot.to_csv(out_dir / "method_results.csv", index=False, encoding="utf-8-sig")
    _write_summaries(combined_plot, out_dir)

    print(f"combined_with_mask_off={len(combined_with_mask_off)}")
    print(f"combined_plot={len(combined_plot)}")
    print(f"output={out_dir}")


if __name__ == "__main__":
    main()
