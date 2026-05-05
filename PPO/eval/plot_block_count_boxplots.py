#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate paper-style boxplots from MODE 1 evaluation results."""

# [AGENT-ADD] Reusable plotter for generated-data grid results.

from __future__ import annotations

import argparse
import os
from typing import Dict, List

# [AGENT-ADD] Keep matplotlib cache writable even when called directly.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)

import numpy as np
import pandas as pd


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot makespan/violation boxplots by block count.")
    parser.add_argument("result_dir", help="Directory containing method_results.csv")
    parser.add_argument("--input", default="method_results.csv", help="Input CSV filename")
    parser.add_argument("--prefix", default="", help="Optional output filename prefix")
    parser.add_argument("--show_fliers", action="store_true", help="Show boxplot outlier points")
    parser.add_argument("--hide_fliers_for", default="", help="Comma-separated method names whose outlier points are hidden")
    parser.add_argument("--include_mask_off", action="store_true", help="Include *_ALL_MASK_OFF rows")
    return parser.parse_args()


def _load_plot_data(result_dir: str, csv_name: str, include_mask_off: bool) -> pd.DataFrame:
    csv_path = os.path.join(result_dir, csv_name)
    df = pd.read_csv(csv_path)
    if not include_mask_off:
        if "selection_variant" in df.columns:
            df = df[df["selection_variant"].fillna("best").astype(str) != "all_mask_off"].copy()
        df = df[~df["method"].astype(str).str.endswith("_ALL_MASK_OFF")].copy()
    return df


def _plot_box_by_block_count(
    df: pd.DataFrame,
    *,
    metric: str,
    ylabel: str,
    title: str,
    output_path: str,
    show_fliers: bool,
    hide_fliers_for: set[str],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    method_order = ["SPT", "SEAM_MIN", "LPT", "GA", "RL"]
    label_map: Dict[str, str] = {
        "SPT": "SPT",
        "SEAM_MIN": "MSF",
        "LPT": "LPT",
        "GA": "GA",
        "RL": "Proposed",
    }
    color_map: Dict[str, str] = {
        "SPT": "#F3C51B",
        "SEAM_MIN": "#0B7D12",
        "LPT": "#2837C8",
        "GA": "#F58518",
        "RL": "#E71A1A",
    }

    methods = [method for method in method_order if method in set(df["method"].astype(str))]
    block_counts: List[int] = sorted(int(value) for value in df["total_blocks"].dropna().unique())
    if not methods or not block_counts:
        raise ValueError("No plottable methods or block counts found.")

    fig_width = max(14.0, len(block_counts) * 0.72)
    fig, ax = plt.subplots(figsize=(fig_width, 5.2))
    center_positions = np.arange(len(block_counts), dtype=float)
    group_width = 0.72
    box_width = group_width / max(len(methods), 1) * 0.82
    offsets = np.linspace(-group_width / 2 + box_width / 2, group_width / 2 - box_width / 2, len(methods))

    legend_handles = []
    for method_idx, method in enumerate(methods):
        data = []
        positions = []
        for x_idx, block_count in enumerate(block_counts):
            values = df[(df["method"] == method) & (df["total_blocks"] == block_count)][metric].dropna().astype(float)
            if values.empty:
                continue
            data.append(values.to_numpy())
            positions.append(center_positions[x_idx] + offsets[method_idx])
        if not data:
            continue
        # [AGENT-ADD] Allow hiding outlier dots for selected methods only, e.g. RL.
        method_show_fliers = bool(show_fliers and method not in hide_fliers_for)
        plot = ax.boxplot(
            data,
            positions=positions,
            widths=box_width,
            patch_artist=True,
            showfliers=method_show_fliers,
            manage_ticks=False,
            medianprops={"color": "#222222", "linewidth": 1.0},
            whiskerprops={"color": "#666666", "linewidth": 1.0},
            capprops={"color": "#666666", "linewidth": 1.0},
        )
        for box in plot["boxes"]:
            box.set_facecolor(color_map.get(method, "#999999"))
            box.set_edgecolor("#4D4D4D")
            box.set_alpha(0.92)
        legend_handles.append(plt.Rectangle((0, 0), 1, 1, facecolor=color_map.get(method), edgecolor="#4D4D4D", label=label_map.get(method, method)))

    ax.set_xticks(center_positions)
    ax.set_xticklabels([str(value) for value in block_counts], rotation=25)
    ax.set_xlabel("Number of Blocks")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.28)
    ax.legend(handles=legend_handles, loc="upper left", ncol=min(len(legend_handles), 4), frameon=False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=220)
    plt.close(fig)


def main() -> None:
    args = _parse_args()
    result_dir = os.path.abspath(args.result_dir)
    df = _load_plot_data(result_dir, args.input, include_mask_off=args.include_mask_off)
    hide_fliers_for = {
        item.strip()
        for item in str(args.hide_fliers_for or "").replace(";", ",").split(",")
        if item.strip()
    }

    prefix = args.prefix.strip()
    if prefix and not prefix.endswith("_"):
        prefix += "_"

    makespan_path = os.path.join(result_dir, f"{prefix}plot_makespan_box_by_block_count_no_mask_off.png")
    violation_path = os.path.join(result_dir, f"{prefix}plot_violations_box_by_block_count_no_mask_off.png")

    _plot_box_by_block_count(
        df,
        metric="makespan_hours",
        ylabel="Makespan (h)",
        title="Makespan Distribution by Number of Blocks",
        output_path=makespan_path,
        show_fliers=args.show_fliers,
        hide_fliers_for=hide_fliers_for,
    )
    _plot_box_by_block_count(
        df,
        metric="violations",
        ylabel="Violations",
        title="Violation Distribution by Number of Blocks",
        output_path=violation_path,
        show_fliers=args.show_fliers,
        hide_fliers_for=hide_fliers_for,
    )
    print(makespan_path)
    print(violation_path)


if __name__ == "__main__":
    main()
