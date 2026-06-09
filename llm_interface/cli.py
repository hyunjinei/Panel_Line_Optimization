#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command line tools for LLM Connect."""

# [AGENT-ADD] Thin CLI for parsing, validating, and explaining interactive requests.

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

import pandas as pd

from .analysis_bundle import build_analysis_bundle, save_analysis_bundle
from .analysis_report import save_analysis_report
from .evaluation import write_bundle_evaluation, write_parse_evaluation
from .openai_parser import parse_request_auto
from .result_explainer import explain_comparison
from .schemas import ComparisonSummary
from .summary_adapters import summarize_results_csv
from .validation import request_to_dict, validate_request


# [AGENT-EDIT] Common LLM provider options keep Groq/Gemini/OpenAI setup consistent.
def _add_llm_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-provider", default=None, help="openai, groq, gemini, ollama, or openai_compatible")
    parser.add_argument("--llm-base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--llm-api-key-env", default=None, help="Environment variable name that stores the API key")


def _sequence_from_csv(path: str | None) -> List[int]:
    if not path:
        return []
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "block_id" not in df.columns:
        return []
    if "am_sequence" in df.columns:
        df = df.sort_values("am_sequence")
    return [int(value) for value in df["block_id"].dropna().tolist()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Connect helper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="Parse natural-language request into ScheduleEditRequest JSON")
    parse_cmd.add_argument("--request", required=True)
    parse_cmd.add_argument("--sequence-csv", help="Optional existing result CSV for validation")
    parse_cmd.add_argument("--parser", default="deterministic", choices=["deterministic", "llm", "openai", "groq", "gemini", "ollama", "openai_compatible", "auto"])
    _add_llm_provider_args(parse_cmd)
    parse_cmd.add_argument("--output", help="Optional JSON output path")

    explain_cmd = sub.add_parser("explain", help="Build before/after explanation bundle")
    explain_cmd.add_argument("--before-csv", required=True)
    explain_cmd.add_argument("--after-csv", required=True)
    explain_cmd.add_argument("--request", default="")
    explain_cmd.add_argument("--before-trace-csv")
    explain_cmd.add_argument("--after-trace-csv")
    explain_cmd.add_argument("--output-dir", default="llm_connect_output")

    eval_parse_cmd = sub.add_parser("eval-parse", help="Evaluate request understanding against labeled cases")
    eval_parse_cmd.add_argument("--cases", required=True, help="JSONL/JSON/CSV file with request and expected_constraints")
    eval_parse_cmd.add_argument("--sequence-csv", help="Optional existing result CSV for validation")
    eval_parse_cmd.add_argument("--parser", default="deterministic", choices=["deterministic", "llm", "openai", "groq", "gemini", "ollama", "openai_compatible", "auto"])
    _add_llm_provider_args(eval_parse_cmd)
    eval_parse_cmd.add_argument("--output-dir", default="llm_connect_eval")

    eval_bundle_cmd = sub.add_parser("eval-bundle", help="Evaluate groundedness of analysis_bundle.json files")
    eval_bundle_cmd.add_argument("bundles", nargs="+", help="analysis_bundle.json paths")
    eval_bundle_cmd.add_argument("--output-dir", default="llm_connect_eval")

    return parser.parse_args()


def _run_parse(args: argparse.Namespace) -> None:
    sequence = _sequence_from_csv(args.sequence_csv)
    request = parse_request_auto(
        args.request,
        current_sequence=sequence,
        parser_mode=args.parser,
        model=args.llm_model,
        provider=args.llm_provider,
        base_url=args.llm_base_url,
        api_key_env=args.llm_api_key_env,
    )
    errors, warnings = validate_request(request, sequence=sequence)
    payload = {
        "request": request_to_dict(request),
        "validation": {
            "errors": errors,
            "warnings": warnings,
            "ok": not errors,
        },
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    if errors:
        raise SystemExit(2)


def _run_explain(args: argparse.Namespace) -> None:
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    before = summarize_results_csv(args.before_csv, label="before")
    after = summarize_results_csv(args.after_csv, label="after")
    comparison = ComparisonSummary(before=before, after=after, user_request=args.request)
    explanation = explain_comparison(comparison)
    bundle = build_analysis_bundle(
        comparison,
        user_request=args.request,
        before_stats={},
        after_stats={},
        before_trace_csv=args.before_trace_csv,
        after_trace_csv=args.after_trace_csv,
        explanation=explanation,
    )
    bundle_path = out_dir / "analysis_bundle.json"
    report_path = out_dir / "analysis_report.md"
    save_analysis_bundle(bundle, bundle_path)
    save_analysis_report(bundle, report_path)
    print(json.dumps({
        "analysis_bundle_json": str(bundle_path),
        "analysis_report_md": str(report_path),
        "explanation": explanation,
    }, ensure_ascii=False, indent=2))


def _run_eval_parse(args: argparse.Namespace) -> None:
    sequence = _sequence_from_csv(args.sequence_csv)
    detail_path, summary_path = write_parse_evaluation(
        args.cases,
        args.output_dir,
        parser_mode=args.parser,
        sequence=sequence,
        llm_model=args.llm_model,
        llm_provider=args.llm_provider,
        llm_base_url=args.llm_base_url,
        llm_api_key_env=args.llm_api_key_env,
    )
    print(json.dumps({
        "detail_csv": str(detail_path),
        "summary_csv": str(summary_path),
    }, ensure_ascii=False, indent=2))


def _run_eval_bundle(args: argparse.Namespace) -> None:
    detail_path, summary_path = write_bundle_evaluation(args.bundles, args.output_dir)
    print(json.dumps({
        "detail_csv": str(detail_path),
        "summary_csv": str(summary_path),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    args = _parse_args()
    if args.command == "parse":
        _run_parse(args)
        return
    if args.command == "explain":
        _run_explain(args)
        return
    if args.command == "eval-parse":
        _run_eval_parse(args)
        return
    if args.command == "eval-bundle":
        _run_eval_bundle(args)
        return
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
