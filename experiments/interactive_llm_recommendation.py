#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate nearby interactive requests and choose a grounded recommendation."""

# [AGENT-ADD] Recommendation runner for the Streamlit side-car workflow.

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.interactive_llm_reschedule import run_case
from llm_interface.openai_parser import parse_request_auto
from llm_interface.recommendation_engine import (
    build_recommendation_scenarios,
    choose_recommended_scenario,
    explain_recommendation,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LLM Connect recommendation scenarios.")
    parser.add_argument("--excel-path", required=True)
    parser.add_argument("--sheet-name", default="Sheet1")
    parser.add_argument("--method", default="lpt", choices=["lpt", "spt", "seam_min", "msf", "rl"])
    parser.add_argument("--request", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--date-offset", type=int, default=0)
    parser.add_argument("--max-days", type=int, default=20)
    parser.add_argument("--rl-model-path")
    parser.add_argument("--parser", default="deterministic", choices=["deterministic", "llm", "openai", "groq", "ollama", "openai_compatible", "auto"])
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key-env", default=None)
    parser.add_argument("--runtime-config-json", default="")
    parser.add_argument("--freeze-existing-prefix", action="store_true")
    parser.add_argument("--save-detailed-process", action="store_true")
    parser.add_argument("--verbose-scheduler", action="store_true")
    return parser.parse_args()


def _scenario_args(base: argparse.Namespace, label: str, request_text: str, output_dir: Path) -> argparse.Namespace:
    copied = copy.copy(base)
    copied.request = request_text
    copied.output_dir = str(output_dir / label)
    return copied


def _row_from_summary(label: str, request_text: str, distance: int, summary: Dict[str, Any]) -> Dict[str, Any]:
    after = summary.get("after") or {}
    return {
        "label": label,
        "request_text": request_text,
        "request_distance": int(distance),
        "makespan_hours": float(after.get("makespan_hours", 0.0) or 0.0),
        "primary_violations": int(after.get("primary_violations", 0) or 0),
        "raw_violations": int(after.get("raw_violations", 0) or 0),
        "summary_json": str(Path(summary.get("after_csv", "")).parent / "summary.json"),
        "analysis_bundle_json": summary.get("analysis_bundle_json"),
        "after_csv": summary.get("after_csv"),
    }


def main() -> None:
    args = _parse_args()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    request = parse_request_auto(
        args.request,
        parser_mode=args.parser,
        model=args.llm_model,
        provider=args.llm_provider,
        base_url=args.llm_base_url,
        api_key_env=args.llm_api_key_env,
    )
    scenarios = build_recommendation_scenarios(request)
    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for scenario in scenarios:
        try:
            summary = run_case(_scenario_args(args, scenario.label, scenario.request_text, out_dir / "scenarios"))
            row = _row_from_summary(
                scenario.label,
                scenario.request_text,
                scenario.request_distance,
                summary,
            )
            row["rationale"] = scenario.rationale
            rows.append(row)
        except Exception as exc:
            errors.append({
                "label": scenario.label,
                "request_text": scenario.request_text,
                "error": str(exc),
            })

    if not rows:
        raise RuntimeError("No recommendation scenarios completed successfully: " + json.dumps(errors, ensure_ascii=False))

    recommended = choose_recommended_scenario(rows)
    payload = {
        "request": args.request,
        "method": args.method,
        "rows": rows,
        "errors": errors,
        "recommended": recommended,
        "explanation": explain_recommendation(rows),
    }
    output_path = out_dir / "recommendation_summary.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"recommendation_summary_json": str(output_path), "recommended": recommended}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
