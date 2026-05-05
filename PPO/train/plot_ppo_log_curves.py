#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plot PPO train/evaluation log curves from CSV files."""

# [AGENT-ADD] Reusable plotting script for PPO train/eval CSV logs.
# [AGENT-ADD] Keeps output naming aligned with existing *_ppo_train_curves.png and *_ppo_evaluation_curves.png artifacts.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _find_csv(log_dir: Path, run_prefix: str, suffix: str) -> Path:
    path = log_dir / f"{run_prefix}_{suffix}.csv"
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    return path


def _rolling(series: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        return series
    return series.rolling(window=window, min_periods=1).mean()


def plot_train_curves(train_csv: Path, output_png: Optional[Path] = None, smooth: int = 25) -> Path:
    df = pd.read_csv(train_csv)
    if output_png is None:
        output_png = train_csv.with_name(train_csv.stem.replace("_ppo_train", "_ppo_train_curves") + ".png")

    episodes = df["episode"]
    fig, axes = plt.subplots(4, 2, figsize=(16, 18))
    axes = axes.ravel()

    # 1. makespan
    axes[0].plot(episodes, _rolling(df["real_makespan"], smooth), label="Actor", color="#1f77b4", linewidth=2.0)
    axes[0].plot(episodes, _rolling(df["baseline_makespan"], smooth), label="LPT Baseline", color="#ff7f0e", linewidth=2.0)
    axes[0].set_title("Makespan")
    axes[0].set_xlabel("Episode")
    axes[0].set_ylabel("Hours")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()

    # 2. violations
    axes[1].plot(episodes, _rolling(df["violations"], smooth), label="Actor", color="#d62728", linewidth=2.0)
    axes[1].plot(episodes, _rolling(df["baseline_violations"], smooth), label="LPT Baseline", color="#2ca02c", linewidth=2.0)
    axes[1].set_title("Primary Violations")
    axes[1].set_xlabel("Episode")
    axes[1].set_ylabel("Count")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    # 3. advantage
    axes[2].plot(episodes, _rolling(df["advantage"], smooth), color="#9467bd", linewidth=2.0)
    axes[2].axhline(0.0, color="black", linestyle="--", linewidth=1.0)
    axes[2].set_title("Advantage")
    axes[2].set_xlabel("Episode")
    axes[2].set_ylabel("Score Gap")
    axes[2].grid(True, alpha=0.3)

    # 4. actor loss
    axes[3].plot(episodes, _rolling(df["actor_loss"], smooth), color="#8c564b", linewidth=2.0)
    axes[3].set_title("Actor Loss")
    axes[3].set_xlabel("Episode")
    axes[3].set_ylabel("Loss")
    axes[3].grid(True, alpha=0.3)

    # 5. longi balance
    axes[4].plot(episodes, _rolling(df["actor_longi_balance"], smooth), label="Actor", color="#17becf", linewidth=2.0)
    axes[4].plot(episodes, _rolling(df["baseline_longi_balance"], smooth), label="LPT Baseline", color="#bcbd22", linewidth=2.0)
    axes[4].set_title("Longi Balance Ratio")
    axes[4].set_xlabel("Episode")
    axes[4].set_ylabel("Ratio")
    axes[4].grid(True, alpha=0.3)
    axes[4].legend()

    # 6. delta makespan
    axes[5].plot(episodes, _rolling(df["delta_makespan"], smooth), color="#ff7f0e", linewidth=2.0)
    axes[5].axhline(0.0, color="black", linestyle="--", linewidth=1.0)
    axes[5].set_title("Delta Makespan (Baseline - Actor)")
    axes[5].set_xlabel("Episode")
    axes[5].set_ylabel("Hours")
    axes[5].grid(True, alpha=0.3)

    # 7. delta violations
    axes[6].plot(episodes, _rolling(df["delta_violations"], smooth), color="#2ca02c", linewidth=2.0)
    axes[6].axhline(0.0, color="black", linestyle="--", linewidth=1.0)
    axes[6].set_title("Delta Violations (Baseline - Actor)")
    axes[6].set_xlabel("Episode")
    axes[6].set_ylabel("Count")
    axes[6].grid(True, alpha=0.3)

    # 8. update source ratio
    if "update_source" in df.columns:
        update_from_lpt = (df["update_source"].astype(str).str.lower() == "lpt").astype(float)
        update_from_self = (df["update_source"].astype(str).str.lower() == "self_label").astype(float)
        axes[7].plot(episodes, _rolling(update_from_self, smooth), label="Self-label teacher", color="#1f77b4", linewidth=2.0)
        axes[7].plot(episodes, _rolling(update_from_lpt, smooth), label="LPT teacher", color="#d62728", linewidth=2.0)
        axes[7].set_ylim(-0.05, 1.05)
        axes[7].legend()
    else:
        axes[7].plot(episodes, _rolling(df["update_applied"].astype(float), smooth), color="#1f77b4", linewidth=2.0)
        axes[7].set_ylim(-0.05, 1.05)
    axes[7].set_title("Teacher Source / Update Applied")
    axes[7].set_xlabel("Episode")
    axes[7].set_ylabel("Ratio")
    axes[7].grid(True, alpha=0.3)

    fig.suptitle(f"PPO Train Curves: {train_csv.stem}", fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(output_png, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_png


def plot_evaluation_curves(eval_csv: Path, output_png: Optional[Path] = None, smooth: int = 1) -> Path:
    df = pd.read_csv(eval_csv)
    if output_png is None:
        output_png = eval_csv.with_name(eval_csv.stem.replace("_ppo_evaluation", "_ppo_evaluation_curves") + ".png")

    episodes = df["episode"]
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    axes = axes.ravel()

    # 1. mean makespan comparison
    for col, label, color in [
        ("rl_mean", "RL", "#1f77b4"),
        ("random_mean", "Random", "#7f7f7f"),
        ("spt_mean", "SPT", "#d62728"),
        ("lpt_mean", "LPT", "#2ca02c"),
        ("seam_mean", "SEAM_MIN", "#ff7f0e"),
    ]:
        if col in df.columns:
            axes[0].plot(episodes, _rolling(df[col], smooth), label=label, linewidth=2.0, color=color)
    if {"rl_mean", "rl_std"}.issubset(df.columns):
        rl_mean = _rolling(df["rl_mean"], smooth)
        rl_std = _rolling(df["rl_std"], smooth)
        axes[0].fill_between(episodes, rl_mean - rl_std, rl_mean + rl_std, color="#1f77b4", alpha=0.15)
    axes[0].set_title("Evaluation Makespan")
    axes[0].set_xlabel("Episode")
    axes[0].set_ylabel("Hours")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()

    # 2. win counts
    for col, label, color in [
        ("rl_wins", "RL", "#1f77b4"),
        ("random_wins", "Random", "#7f7f7f"),
        ("spt_wins", "SPT", "#d62728"),
        ("lpt_wins", "LPT", "#2ca02c"),
        ("seam_wins", "SEAM_MIN", "#ff7f0e"),
    ]:
        if col in df.columns:
            axes[1].plot(episodes, _rolling(df[col], smooth), label=label, linewidth=2.0, color=color)
    axes[1].set_title("Win Counts")
    axes[1].set_xlabel("Episode")
    axes[1].set_ylabel("Wins")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    # 3. RL improvement against heuristics
    for col, label, color in [
        ("improvement_vs_random", "vs Random", "#7f7f7f"),
        ("improvement_vs_spt", "vs SPT", "#d62728"),
        ("improvement_vs_lpt", "vs LPT", "#2ca02c"),
        ("improvement_vs_seam", "vs SEAM_MIN", "#ff7f0e"),
    ]:
        if col in df.columns:
            axes[2].plot(episodes, _rolling(df[col], smooth), label=label, linewidth=2.0, color=color)
    axes[2].axhline(0.0, color="black", linestyle="--", linewidth=1.0)
    axes[2].set_title("RL Improvement")
    axes[2].set_xlabel("Episode")
    axes[2].set_ylabel("Hours Saved (+ better)")
    axes[2].grid(True, alpha=0.3)
    axes[2].legend()

    # 4. RL range
    if {"rl_mean", "rl_min", "rl_max"}.issubset(df.columns):
        axes[3].plot(episodes, _rolling(df["rl_mean"], smooth), label="RL mean", color="#1f77b4", linewidth=2.0)
        axes[3].plot(episodes, _rolling(df["rl_min"], smooth), label="RL min", color="#2ca02c", linestyle="--", linewidth=1.8)
        axes[3].plot(episodes, _rolling(df["rl_max"], smooth), label="RL max", color="#d62728", linestyle="--", linewidth=1.8)
        axes[3].fill_between(episodes, _rolling(df["rl_min"], smooth), _rolling(df["rl_max"], smooth), color="#1f77b4", alpha=0.15)
    axes[3].set_title("RL Sampling Range")
    axes[3].set_xlabel("Episode")
    axes[3].set_ylabel("Hours")
    axes[3].grid(True, alpha=0.3)
    axes[3].legend()

    fig.suptitle(f"PPO Evaluation Curves: {eval_csv.stem}", fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(output_png, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_png


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot PPO train/evaluation curves from CSV logs")
    parser.add_argument("--run-prefix", help="Run prefix such as 0407_22_53")
    parser.add_argument("--log-dir", default="PPO/train/result/log/ppo", help="CSV log directory")
    parser.add_argument("--train-csv", help="Explicit train CSV path")
    parser.add_argument("--eval-csv", help="Explicit evaluation CSV path")
    parser.add_argument("--smooth", type=int, default=25, help="Rolling window for train curves")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    train_csv = Path(args.train_csv) if args.train_csv else _find_csv(log_dir, args.run_prefix, "ppo_train")
    eval_csv = Path(args.eval_csv) if args.eval_csv else _find_csv(log_dir, args.run_prefix, "ppo_evaluation")

    train_png = plot_train_curves(train_csv, smooth=max(1, args.smooth))
    eval_png = plot_evaluation_curves(eval_csv)
    print(train_png)
    print(eval_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
