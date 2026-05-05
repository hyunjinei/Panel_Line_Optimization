"""Compare canonical metrics across replay/start_date/assembly_start CSV outputs."""

# [AGENT-ADD] Standalone comparison entrypoint for thesis-quality fresh-run tables.

from __future__ import annotations

import argparse
from pathlib import Path

from scheduling.common.run_artifacts import save_comparison_report, summarize_result_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="세 결과 CSV의 canonical metric 비교")
    parser.add_argument("--replay-csv", required=True, help="replay 결과 CSV 경로")
    parser.add_argument("--assembly-csv", required=True, help="assembly_start heuristic 결과 CSV 경로")
    parser.add_argument("--start-date-csv", required=True, help="start_date 결과 CSV 경로")
    parser.add_argument("--output-dir", default="results/fresh_runs", help="비교표 저장 디렉터리")
    parser.add_argument("--run-tag", default=None, help="비교 결과 저장용 run tag")
    args = parser.parse_args()

    summaries = [
        summarize_result_csv(Path(args.replay_csv), mode="replay"),
        summarize_result_csv(Path(args.assembly_csv), mode="assembly_start_heuristic"),
        summarize_result_csv(Path(args.start_date_csv), mode="start_date"),
    ]
    saved = save_comparison_report(
        summaries=summaries,
        output_dir=args.output_dir,
        run_tag=args.run_tag,
    )

    print("Fresh run comparison complete")
    for summary in summaries:
        print(
            f"- {summary['mode']}: makespan={summary['canonical_makespan_hours']:.2f}h, "
            f"primary={summary['primary_count']}, raw={summary['raw_count']}, "
            f"meta={summary['meta_count']}, info={summary['info_count']}"
        )
    print(f"CSV report: {saved['csv']}")
    print(f"Markdown report: {saved['md']}")


if __name__ == "__main__":
    main()
