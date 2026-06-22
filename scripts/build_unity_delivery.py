#!/usr/bin/env python3
"""Build Unity delivery CSVs from unity/by_case outputs.

# [AGENT-ADD] The source CSVs are preserved as original_*.csv in the delivery
# folder, while visualization-facing CSVs keep only stable minimal columns.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path
from typing import Iterable


METHODS = ("SPT", "MSF", "LPT", "GA", "Proposed")

SLIM_COLUMNS: dict[str, list[str]] = {
    "case_graph_values_all_methods.csv": [
        "grid_case_id",
        "method_label",
        "total_blocks",
        "distribution_profile",
        "makespan_hours",
        "violations",
    ],
    "process_gantt.csv": [
        "grid_case_id",
        "method_label",
        "block_id",
        "block_name",
        "sequence",
        "process_name",
        "process_index",
        "start_time",
        "end_time",
        "duration_min",
        "assigned_bay",
        "machine_type",
    ],
    "block_results.csv": [
        "grid_case_id",
        "method_label",
        "block_id",
        "block_name",
        "am_sequence",
        "assigned_bay",
        "total_time_min",
        "port_starboard",
        "assembly_type",
        "line_group",
        "width_m",
        "longi_count",
        "seam_count",
        "c_seam_count",
        "violations",
        "start_time",
        "end_time",
        "final_end_time",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build slim Unity delivery CSVs and original_*.csv files from unity/by_case."
    )
    parser.add_argument(
        "--source",
        default="unity/by_case",
        help="Source by-case directory. Default: unity/by_case",
    )
    parser.add_argument(
        "--output",
        default="unity_for_delivery",
        help="Output delivery directory. Default: unity_for_delivery",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify an existing output directory. Do not write files.",
    )
    return parser.parse_args()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return reader.fieldnames or [], list(reader)


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return next(csv.reader(file), [])


def write_slim(source: Path, output: Path, columns: list[str]) -> None:
    fields, rows = read_rows(source)
    missing = [column for column in columns if column not in fields]
    if missing:
        raise RuntimeError(f"{source} is missing required slim columns: {missing}")

    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def copy_original_and_slim(source: Path, original_output: Path, slim_output: Path, columns: list[str]) -> None:
    original_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, original_output)
    write_slim(original_output, slim_output, columns)


def iter_case_dirs(source_root: Path) -> Iterable[Path]:
    for path in sorted(source_root.iterdir()):
        if path.is_dir() and path.name != "__pycache__":
            yield path


def write_case_readme(case_output: Path, case_id: str) -> None:
    text = f"""# {case_id}

이 폴더는 `{case_id}` 문제 케이스의 Unity 전달용 CSV입니다.

- `case_graph_values_all_methods.csv`: 방법별 makespan, violation 비교용 slim CSV
- `original_case_graph_values_all_methods.csv`: 전체 컬럼 원본 보존 파일
- `{{Method}}/process_gantt.csv`: 공정 간트와 애니메이션용 slim CSV
- `{{Method}}/block_results.csv`: 블록 클릭 상세용 slim CSV
- `{{Method}}/original_*.csv`: 전체 컬럼 원본 보존 파일
"""
    case_output.joinpath("README.md").write_text(text, encoding="utf-8")


def build(source_root: Path, output_root: Path) -> dict[str, int]:
    stats = {
        "cases": 0,
        "case_graph_files": 0,
        "process_files": 0,
        "block_files": 0,
        "verification_files": 0,
    }

    for case_source in iter_case_dirs(source_root):
        case_output = output_root / case_source.name
        case_output.mkdir(parents=True, exist_ok=True)
        write_case_readme(case_output, case_source.name)
        stats["cases"] += 1

        graph_source = case_source / "case_graph_values_all_methods.csv"
        if not graph_source.exists():
            raise RuntimeError(f"Missing graph values file: {graph_source}")
        copy_original_and_slim(
            graph_source,
            case_output / "original_case_graph_values_all_methods.csv",
            case_output / "case_graph_values_all_methods.csv",
            SLIM_COLUMNS["case_graph_values_all_methods.csv"],
        )
        stats["case_graph_files"] += 1

        for method in METHODS:
            method_source = case_source / method
            if not method_source.exists():
                raise RuntimeError(f"Missing method directory: {method_source}")
            method_output = case_output / method
            method_output.mkdir(parents=True, exist_ok=True)

            process_source = method_source / f"{method}_process_gantt_expanded.csv"
            block_source = method_source / f"{method}_block_results.csv"
            verification_source = method_source / f"{method}_expanded_verification.csv"

            copy_original_and_slim(
                process_source,
                method_output / "original_process_gantt.csv",
                method_output / "process_gantt.csv",
                SLIM_COLUMNS["process_gantt.csv"],
            )
            stats["process_files"] += 1

            copy_original_and_slim(
                block_source,
                method_output / "original_block_results.csv",
                method_output / "block_results.csv",
                SLIM_COLUMNS["block_results.csv"],
            )
            stats["block_files"] += 1

            if verification_source.exists():
                shutil.copy2(verification_source, method_output / "original_expanded_verification.csv")
                stats["verification_files"] += 1

    return stats


def verify(output_root: Path) -> dict[str, int]:
    counts = {
        "case_graph_values_all_methods.csv": 0,
        "process_gantt.csv": 0,
        "block_results.csv": 0,
        "original_expanded_verification.csv": 0,
    }

    for filename, columns in SLIM_COLUMNS.items():
        for path in sorted(output_root.rglob(filename)):
            if path.name.startswith("original_"):
                continue
            if read_header(path) != columns:
                raise RuntimeError(f"Unexpected header in {path}: {read_header(path)}")
            original = path.with_name(f"original_{path.name}")
            if not original.exists():
                raise RuntimeError(f"Missing original file for {path}")
            counts[filename] += 1

    active_verification = list(output_root.rglob("expanded_verification.csv"))
    active_verification = [path for path in active_verification if not path.name.startswith("original_")]
    if active_verification:
        raise RuntimeError(f"Active expanded_verification.csv files should not exist: {active_verification[:3]}")

    counts["original_expanded_verification.csv"] = sum(
        1 for _ in output_root.rglob("original_expanded_verification.csv")
    )
    return counts


def main() -> None:
    args = parse_args()
    source_root = Path(args.source)
    output_root = Path(args.output)

    if not source_root.exists():
        raise SystemExit(f"Source directory does not exist: {source_root}")
    if args.check_only:
        print("verified_counts=", verify(output_root))
        return

    output_root.mkdir(parents=True, exist_ok=True)
    stats = build(source_root, output_root)
    print("build_stats=", stats)
    print("verified_counts=", verify(output_root))


if __name__ == "__main__":
    main()
