"""Shared process schedule CSV helpers."""

# [AGENT-ADD] Extracted from scheduling scripts to reduce duplication.

from datetime import datetime, timedelta
from typing import Dict, List

from enhanced_environment.models import BayType


def _build_process_records(
    date_key: str,
    sequence: List[int],
    bay_assignments: Dict,
    ct_tables: Dict,
    blocks_dict: Dict,
    date_start_time: datetime,
) -> List[Dict]:
    """Build per-process schedule records (shared logic)."""
    process_records: List[Dict] = []
    process_names = [
        '판계', '전면SAW', 'TurnOver', '후면SAW', 'NC',
        '론지취부', '론지용접', '수정'
    ]

    ct_common = ct_tables['ct_common']
    ct_branch_a = ct_tables['ct_branch_a']
    ct_branch_b = ct_tables['ct_branch_b']

    for seq_idx, block_id in enumerate(sequence):
        if block_id not in blocks_dict:
            continue

        block = blocks_dict[block_id]
        assigned_bay = bay_assignments.get(block_id, BayType.BAY_35A)

        # 공통 공정 (1~5)
        for proc_idx in range(5):
            end_seconds = ct_common[seq_idx + 1, proc_idx + 1]
            processing_time_seconds = block.processing_times[proc_idx] * 60
            start_seconds = end_seconds - processing_time_seconds
            duration_min = processing_time_seconds / 60.0

            start_time = date_start_time + timedelta(seconds=start_seconds)
            end_time = date_start_time + timedelta(seconds=end_seconds)

            process_records.append({
                'date': date_key,
                'block_id': block_id,
                'block_name': getattr(block, 'block_name', f'BLK_{block_id}'),
                'sequence': seq_idx + 1,
                'process_name': process_names[proc_idx],
                'process_index': proc_idx + 1,
                'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_min': round(duration_min, 1),
                'assigned_bay': '',
                'machine_type': '공통'
            })

        # 분기 공정 (6~8)
        branch_table = ct_branch_a if assigned_bay == BayType.BAY_35A else ct_branch_b
        bay_name = '35A' if assigned_bay == BayType.BAY_35A else '36B'

        for proc_idx in range(3):
            end_seconds = branch_table[seq_idx + 1, proc_idx]
            processing_time_seconds = block.processing_times[proc_idx + 5] * 60
            start_seconds = end_seconds - processing_time_seconds
            duration_min = processing_time_seconds / 60.0

            start_time = date_start_time + timedelta(seconds=start_seconds)
            end_time = date_start_time + timedelta(seconds=end_seconds)

            process_records.append({
                'date': date_key,
                'block_id': block_id,
                'block_name': getattr(block, 'block_name', f'BLK_{block_id}'),
                'sequence': seq_idx + 1,
                'process_name': f'베이{bay_name} {process_names[proc_idx + 5]}',
                'process_index': proc_idx + 6,
                'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_min': round(duration_min, 1),
                'assigned_bay': bay_name,
                'machine_type': f'베이{bay_name}'
            })

    return process_records


def save_detailed_process_schedule(
    *,
    date_key: str,
    sequence: List[int],
    bay_assignments: Dict,
    ct_tables: Dict,
    blocks_dict: Dict,
    date_start_time: datetime,
    filename: str,
    verbose: bool = False,
    label: str = "공정별 상세 스케줄",
) -> None:
    """Save per-process schedule CSV with common formatting."""
    import pandas as pd

    process_records = _build_process_records(
        date_key=date_key,
        sequence=sequence,
        bay_assignments=bay_assignments,
        ct_tables=ct_tables,
        blocks_dict=blocks_dict,
        date_start_time=date_start_time,
    )

    if process_records:
        df = pd.DataFrame(process_records)
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        if verbose:
            print(f"📄 {label}: {filename} ({len(process_records)}개 공정)")
