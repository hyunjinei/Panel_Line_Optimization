#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate generated-eval summary plots from method_results.csv."""

# [AGENT-ADD] Standalone plotter for existing MODE 1 result folders.

from __future__ import annotations

import argparse
import os
from typing import List

import numpy as np
import pandas as pd


os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create generated-eval summary plots.")
    parser.add_argument("result_dir", help="Directory containing method_results.csv")
    parser.add_argument("--input", default="method_results.csv")
    parser.add_argument("--suffix", default="_no_mask_off", help="Suffix inserted before .png")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite original plot filenames")
    parser.add_argument("--include_mask_off", action="store_true", help="Include *_ALL_MASK_OFF rows")
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


def _load_plot_df(result_dir: str, csv_name: str, include_mask_off: bool) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(result_dir, csv_name))
    if not include_mask_off:
        if "selection_variant" in df.columns:
            df = df[df["selection_variant"].fillna("best").astype(str) != "all_mask_off"].copy()
        df = df[~df["method"].astype(str).str.endswith("_ALL_MASK_OFF")].copy()
    df["size_bucket"] = df["total_blocks"].apply(_size_bucket)
    return df


def _output_path(result_dir: str, filename: str, suffix: str, overwrite: bool) -> str:
    if overwrite:
        return os.path.join(result_dir, filename)
    stem, ext = os.path.splitext(filename)
    return os.path.join(result_dir, f"{stem}{suffix}{ext}")


def _method_order(df: pd.DataFrame) -> List[str]:
    # [AGENT-EDIT] Keep generated-summary legends aligned with all-method boxplots.
    defaults = ["SPT", "SEAM_MIN", "LPT", "GA", "RL", "착수일기준휴리스틱"]
    available = list(df["method"].dropna().astype(str).unique())
    ordered = [method for method in defaults if method in available]
    for method in available:
        if method not in ordered:
            ordered.append(method)
    return ordered


def _plot_all(result_dir: str, df: pd.DataFrame, suffix: str, overwrite: bool) -> List[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except Exception:
        pass
    plt.rcParams.update({
        "axes.titlesize": 12,
        "axes.labelsize": 11,
        "legend.fontsize": 9,
        "font.size": 10,
    })

    method_order = _method_order(df)
    label_map = {
        "SPT": "SPT",
        "SEAM_MIN": "MSF",
        "LPT": "LPT",
        "GA": "GA",
        "RL": "Proposed",
        "착수일기준휴리스틱": "Start-date heuristic",
    }
    color_map = {
        "SPT": "#4C78A8",
        "SEAM_MIN": "#54A24B",
        "LPT": "#2837C8",
        "GA": "#F58518",
        "RL": "#E45756",
        "착수일기준휴리스틱": "#72B7B2",
    }
    marker_map = {
        "SPT": "o",
        "SEAM_MIN": "^",
        "LPT": "v",
        "GA": "s",
        "RL": "D",
        "착수일기준휴리스틱": "P",
    }
    if "util_bucket" in df.columns and df["util_bucket"].notna().any():
        bucket_order = list(dict.fromkeys(df["util_bucket"].dropna().astype(str).tolist()))
    else:
        bucket_order = []
    size_bucket_order = ["Small (20-80)", "Medium (81-140)", "Large (141-200)"]

    summary_df = (
        df
        .groupby(["util_bucket", "method"], dropna=False)
        .agg(
            n=("makespan_hours", "count"),
            makespan_mean=("makespan_hours", "mean"),
            makespan_std=("makespan_hours", "std"),
            violations_mean=("violations", "mean"),
            violations_std=("violations", "std"),
            actual_util_mean=("actual_util", "mean"),
            actual_util_std=("actual_util", "std"),
        )
        .reset_index()
    )
    if bucket_order:
        summary_df["util_bucket"] = pd.Categorical(summary_df["util_bucket"], categories=bucket_order, ordered=True)
        summary_df = summary_df.sort_values(["util_bucket", "method"])

    size_summary_df = (
        df
        .groupby(["size_bucket", "method"], dropna=False)
        .agg(
            n=("makespan_hours", "count"),
            makespan_mean=("makespan_hours", "mean"),
            makespan_std=("makespan_hours", "std"),
            violations_mean=("violations", "mean"),
            violations_std=("violations", "std"),
            actual_util_mean=("actual_util", "mean"),
            actual_util_std=("actual_util", "std"),
        )
        .reset_index()
    )
    size_summary_df["size_bucket"] = pd.Categorical(size_summary_df["size_bucket"], categories=size_bucket_order, ordered=True)
    size_summary_df = size_summary_df.sort_values(["size_bucket", "method"])

    written: List[str] = []

    def _save(fig, filename: str, *, bbox_inches=None) -> None:
        path = _output_path(result_dir, filename, suffix, overwrite)
        fig.savefig(path, dpi=200, bbox_inches=bbox_inches)
        plt.close(fig)
        written.append(path)

    def _plot_grouped_metric(metric: str, ylabel: str, filename: str) -> None:
        if not bucket_order:
            return
        fig, ax = plt.subplots(figsize=(10, 6))
        x = np.arange(len(bucket_order))
        n_methods = max(len(method_order), 1)
        width = min(0.2, 0.8 / n_methods)
        offset_base = (n_methods - 1) * width / 2
        for idx, method in enumerate(method_order):
            values = []
            for bucket in bucket_order:
                subset = summary_df[(summary_df["util_bucket"] == bucket) & (summary_df["method"] == method)]
                values.append(float(subset[f"{metric}_mean"].iloc[0]) if not subset.empty else np.nan)
            ax.bar(x + idx * width - offset_base, values, width, label=label_map.get(method, method), color=color_map.get(method), edgecolor="white")
        ax.set_xticks(x)
        ax.set_xticklabels(bucket_order)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("util_bucket")
        ax.set_title(f"{metric} by util_bucket")
        ax.legend(loc="best")
        fig.tight_layout()
        _save(fig, filename)

    def _plot_grouped_metric_size(metric: str, ylabel: str, filename: str) -> None:
        fig, ax = plt.subplots(figsize=(10, 6))
        x = np.arange(len(size_bucket_order))
        n_methods = max(len(method_order), 1)
        width = min(0.2, 0.8 / n_methods)
        offset_base = (n_methods - 1) * width / 2
        for idx, method in enumerate(method_order):
            values = []
            for bucket in size_bucket_order:
                subset = size_summary_df[(size_summary_df["size_bucket"] == bucket) & (size_summary_df["method"] == method)]
                values.append(float(subset[f"{metric}_mean"].iloc[0]) if not subset.empty else np.nan)
            ax.bar(x + idx * width - offset_base, values, width, label=label_map.get(method, method), color=color_map.get(method), edgecolor="white")
        ax.set_xticks(x)
        ax.set_xticklabels(size_bucket_order)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("instance size")
        ax.set_title(f"{metric} by size bucket")
        ax.legend(loc="best")
        fig.tight_layout()
        _save(fig, filename)

    def _plot_scatter(metric: str, ylabel: str, filename: str) -> None:
        fig, ax = plt.subplots(figsize=(8, 6))
        for method in method_order:
            subset = df[df["method"] == method]
            if subset.empty:
                continue
            ax.scatter(subset["actual_util"], subset[metric], label=label_map.get(method, method), alpha=0.7, color=color_map.get(method), marker=marker_map.get(method, "o"))
        ax.set_xlabel("actual_util (blocks / (17 * days))")
        ax.set_ylabel(ylabel)
        ax.set_title(f"{metric} vs actual_util")
        ax.legend(loc="best")
        fig.tight_layout()
        _save(fig, filename)

    def _plot_panel(metric: str, ylabel: str, filename: str) -> None:
        fig, axes = plt.subplots(1, len(size_bucket_order), figsize=(15, 4), sharey=True)
        util_positions = {bucket: idx for idx, bucket in enumerate(bucket_order)}
        for ax, size_bucket in zip(axes, size_bucket_order):
            subset_bucket = df[df["size_bucket"] == size_bucket]
            for idx, method in enumerate(method_order):
                subset = subset_bucket[subset_bucket["method"] == method]
                if subset.empty:
                    continue
                x_vals = subset["util_bucket"].map(util_positions).astype(float)
                jitter = (idx - (len(method_order) - 1) / 2) * 0.06
                ax.scatter(
                    x_vals + jitter,
                    subset[metric],
                    label=label_map.get(method, method),
                    alpha=0.75,
                    s=35,
                    color=color_map.get(method),
                    marker=marker_map.get(method, "o"),
                )
            ax.set_title(size_bucket)
            ax.set_xticks(np.arange(len(bucket_order)))
            ax.set_xticklabels(bucket_order)
            ax.set_xlabel("util_bucket")
        axes[0].set_ylabel(ylabel)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="upper center", ncol=min(len(method_order), 5))
        fig.suptitle(f"{metric} comparison by size bucket", y=1.04)
        fig.tight_layout()
        _save(fig, filename, bbox_inches="tight")

    _plot_grouped_metric("makespan", "Makespan (h)", "plot_makespan_by_util_bucket.png")
    _plot_grouped_metric("violations", "Violations", "plot_violations_by_util_bucket.png")
    _plot_grouped_metric_size("makespan", "Makespan (h)", "plot_makespan_by_size_bucket.png")
    _plot_grouped_metric_size("violations", "Violations", "plot_violations_by_size_bucket.png")
    _plot_scatter("makespan_hours", "Makespan (h)", "plot_makespan_scatter_util.png")
    _plot_scatter("violations", "Violations", "plot_violations_scatter_util.png")
    _plot_panel("makespan_hours", "Makespan (h)", "plot_panel_makespan.png")
    _plot_panel("violations", "Violations", "plot_panel_violations.png")
    return written


def main() -> None:
    args = _parse_args()
    result_dir = os.path.abspath(args.result_dir)
    suffix = "" if args.overwrite else str(args.suffix or "")
    df = _load_plot_df(result_dir, args.input, include_mask_off=args.include_mask_off)
    written = _plot_all(result_dir, df, suffix=suffix, overwrite=args.overwrite)
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
