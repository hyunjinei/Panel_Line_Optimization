"""Independent constraint audit for saved scheduling CSV outputs.

# [AGENT-ADD] This audit intentionally separates:
# 1. execution-time violation summaries stored in result CSVs, and
# 2. independent post-hoc auditing of real constraints.
#
# The goal is to guarantee that real constraint checks do not silently disappear
# when masking/bias toggles change during schedule generation.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from enhanced_environment.common.data_converter import DataConverter
from enhanced_environment.common.violation_utils import normalize_constraint_family, summarize_violations
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.models import BayType, ConstraintViolation, ProcessStep
from enhanced_environment.pbs_env.core import EnhancedPanelBlockShop
from runtime_config import build_calendar_overrides, get_audit_constraint_overrides, load_runtime_config, set_runtime_config


# [AGENT-ADD] Independent audit는 "실제 제약"만 강제한다.
# 후보 축소용 편향(workshop-head, slack layers, window filter)은 감사 대상이 아니다.
INDEPENDENT_AUDIT_ATTRS: Dict[str, bool] = {
    "enable_p5_panel_constraints": True,
    "enable_p6_saw_constraints": True,
    "enable_p7_longi_constraints": True,
    "enable_p5_1_delivery_date": False,
    # [AGENT-EDIT] 사용자 요청: canonical audit도 runtime 기본값과 맞춰 P5#3, P5#4를 비활성화한다.
    "enable_p5_3_ps_line_continuous": False,
    "enable_p5_4_ps_fixed_continuous": False,
    "enable_p5_6_line_b_fab_interval": False,
    "enable_p5_8_weekday_capacity": True,
    "enable_p5_9_block_count_check": True,
    "enable_p5_10_weekend_capacity": True,
    # [AGENT-EDIT] P5#11/#12는 현재 연구에서 사용하는 제약 세트 밖으로 둔다.
    # runtime on/off 분리 대상이 아니라, canonical audit에서도 제외한다.
    "enable_p5_11_fixed_line_mixing": False,
    "enable_p5_12_internal_external_mixing": False,
    "enable_p5_13_material_ready": True,
    "enable_p5_15_holiday_shift": True,
    "enable_p5_16_hot_season_capacity": True,
    "enable_p5_17_subassembly_grouping": True,
    # [AGENT-EDIT] 사용자 요청: P6 계열(P6#1~4)을 독립 감사에서도 기본 제외한다.
    "enable_p6_1_draft_afternoon": False,
    "enable_p6_2_cross_seam_afternoon": False,
    "enable_p6_3_dc_block_afternoon": False,
    "enable_p6_4_cross_seam_mixing": False,
    "enable_p7_1_bay_load_balance": True,
    "enable_p7_2_width_21m_bay_b": True,
    "enable_p7_3_ps_same_bay_longi_7": True,
    "enable_p7_4_ps_same_bay_date_1": True,
    "enable_p7_7_bay_a_consecutive": True,
    "enable_p7_8_main_plate_consecutive": True,
    "enable_p7_10_longi_30_bay_a": True,
    "enable_p7_11_block_10_bay_b": False,
    "enable_p7_12_lt_steel_bay_a": True,
    # [AGENT-EDIT] CONSECUTIVE_3BAY는 방법론적 guard로 분류하여 final audit 기본 집계에서 제외한다.
    "enable_consecutive_3bay_prevention": False,
    # [AGENT-EDIT] B베이 2연속은 도메인 위반이 아니므로 audit에서도 제외한다.
    "enable_consecutive_b_bay_prevention": False,
    "enable_line_group_constraint": True,
    "enable_high_longi_split": False,
    "enable_longi_load_balance": True,
    "enable_c_seam_spacing": True,
    "enable_routing_curved_spacing": True,
    "enable_routing_high_seam_spacing": True,
    # [AGENT-EDIT] 작업장 순서 제약은 runtime에서 꺼도 post-hoc audit에서는 계속 센다.
    "enable_routing_workshop_order": True,
    # [AGENT-EDIT] bias는 제약이 아니라 후보 편향이므로 canonical audit에서 비활성화한다.
    "enable_workshop_head_masking": False,
    "enable_assembly_start_leadtime_layers": False,
    "enable_assembly_start_window_filter": False,
}


@dataclass(frozen=True)
class ConstraintCatalogEntry:
    constraint_id: str
    kind: str
    enabled_in_independent_audit: bool
    description: str
    notes: str = ""


CONSTRAINT_CATALOG: List[ConstraintCatalogEntry] = [
    ConstraintCatalogEntry("P5#1", "domain", False, "납기 기반 착수일 제약", "현재 연구 설정에서 비활성"),
    ConstraintCatalogEntry("P5#3", "domain", False, "P 블록이 S 블록보다 먼저 와야 하는 P/S 순서", "현재 연구 제약 세트에서 제외"),
    ConstraintCatalogEntry("P5#4", "domain", False, "고정 조립 P/S 연속성 규칙", "현재 연구 제약 세트에서 제외"),
    ConstraintCatalogEntry("P5#6", "domain", False, "라인 B FAB 간격 규칙", "현재 연구 설정에서 비활성"),
    ConstraintCatalogEntry("P5#8", "domain", True, "평일 심수 용량"),
    ConstraintCatalogEntry("P5#9", "domain", True, "72심 초과 시 17블록 조건"),
    ConstraintCatalogEntry("P5#10", "domain", True, "주말 심수 용량"),
    ConstraintCatalogEntry("P5#11", "domain", False, "조립 타입 혼합/연속 제한", "현재 연구 제약 세트에서 제외"),
    ConstraintCatalogEntry("P5#12", "domain", False, "사내/사외 혼합 제한", "현재 연구 제약 세트에서 제외"),
    ConstraintCatalogEntry("P5#13", "domain", True, "자재 미입고 금지"),
    ConstraintCatalogEntry("P5#15", "domain", True, "명절 전날 제약"),
    ConstraintCatalogEntry("P5#16", "domain", True, "혹서기 용량 제약"),
    ConstraintCatalogEntry("P5#17", "domain", True, "별판 통합 처리", "INFO 성격"),
    ConstraintCatalogEntry("P6#1", "domain", False, "Draft 특수블록 시간 제약", "사용자 요청으로 제거"),
    ConstraintCatalogEntry("P6#2", "domain", False, "Cross seam 특수블록 시간 제약", "사용자 요청으로 제거"),
    ConstraintCatalogEntry("P6#3", "domain", False, "주판 10장 초과 시간 제약", "사용자 요청으로 제거"),
    ConstraintCatalogEntry("P6#4", "domain", False, "Cross seam 혼합 배치 제약", "사용자 요청으로 제거"),
    ConstraintCatalogEntry("P7#1", "domain", True, "론지 A/B 부하 균형", "INFO 성격"),
    ConstraintCatalogEntry("P7#2", "domain", True, "폭 21m 초과 블록은 B베이"),
    ConstraintCatalogEntry("P7#3", "domain", True, "작은 P/S 쌍 동일 베이"),
    ConstraintCatalogEntry("P7#4", "domain", True, "날짜 조건 P/S 동일 베이"),
    ConstraintCatalogEntry("P7#7", "domain", True, "베이 연속 배치 제한"),
    ConstraintCatalogEntry("P7#8", "domain", True, "주판 Only 연속 제한"),
    ConstraintCatalogEntry("P7#10", "domain", True, "론지 30개 이상 A베이 우선"),
    ConstraintCatalogEntry("P7#11", "domain", False, "10번 블록 B베이 패턴", "현재 연구 설정에서 비활성"),
    ConstraintCatalogEntry("P7#12", "domain", True, "LT강재 A베이 우선"),
    ConstraintCatalogEntry("ROUTING_C_SEAM_SPACING", "domain", True, "C/Seam 간격"),
    ConstraintCatalogEntry("ROUTING_CURVED_SPACING", "domain", True, "곡판 간격"),
    ConstraintCatalogEntry("ROUTING_HIGH_SEAM_SPACING", "domain", True, "고심수 간격"),
    ConstraintCatalogEntry("ROUTING_WORKSHOP_ORDER", "domain", True, "같은 실제 작업장 내 조립착수일 순서", "runtime에서 꺼도 post-hoc audit에서는 집계"),
    ConstraintCatalogEntry("LINE_GROUP_CONSTRAINT", "internal_guard", True, "라인 그룹 연속 제한", "구현 가드"),
    ConstraintCatalogEntry("CONSECUTIVE_3BAY", "internal_guard", False, "3베이 패턴 방지", "구현 가드"),
    ConstraintCatalogEntry("CONSECUTIVE_B_BAY", "internal_guard", False, "B베이 2연속 금지", "도메인 규칙과 불일치하여 현재 연구 설정에서 제외"),
    ConstraintCatalogEntry("HIGH_LONGI_SPLIT", "internal_guard", False, "고론지 연속 송선 방지", "현재 기본 비활성"),
    ConstraintCatalogEntry("enable_workshop_head_masking", "bias", False, "작업장 head 후보 축소", "제약이 아니라 후보 편향"),
    ConstraintCatalogEntry("enable_assembly_start_leadtime_layers", "bias", False, "조립착수일 slack 층 분리", "제약이 아니라 후보 편향"),
    ConstraintCatalogEntry("enable_assembly_start_window_filter", "bias", False, "조립착수일 날짜창 필터", "제약이 아니라 후보 편향"),
]


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_list_cell(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text or text == "[]":
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return [text]


def parse_assigned_bay(value: Any) -> BayType:
    text = str(value).strip().upper()
    if text in {"35A", "BAY_35A"}:
        return BayType.BAY_35A
    if text in {"36B", "BAY_36B"}:
        return BayType.BAY_36B
    raise ValueError(f"알 수 없는 베이 값: {value}")


def load_anchor_schedule(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "is_metric_anchor_row" in df.columns:
        anchor_mask = df["is_metric_anchor_row"].fillna(True).astype(bool)
        df = df.loc[anchor_mask].copy()
    # [AGENT-EDIT] CSV 재검증은 실제 실행 순서(절대시간)를 기준으로 정렬해야 한다.
    # start_date / replay는 am_sequence, excel_sequence가 날짜별로 다시 1부터 시작하므로
    # sequence 컬럼을 panel_start_time보다 앞에 두면 일자 간 순서가 섞인다.
    order_columns = [col for col in ("panel_start_time", "final_end_time", "am_sequence", "excel_sequence", "block_id") if col in df.columns]
    if order_columns:
        df = df.sort_values(order_columns, kind="stable").reset_index(drop=True)
    return df


def _normalize_runtime_results_for_audit(schedule_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[AGENT-ADD] 저장된 최종 CSV/메모리 row를 감사용 raw anchor row로 정규화한다.

    최종 CSV는 별판 확장행과 문자열화된 리스트 컬럼을 포함할 수 있으므로,
    감사 비교 전에는 대표 anchor row만 남기고 리스트 셀을 복원해야 한다.
    """
    if not schedule_results:
        return []

    schedule_df = pd.DataFrame(schedule_results)
    if "is_metric_anchor_row" in schedule_df.columns:
        anchor_mask = schedule_df["is_metric_anchor_row"].fillna(True).astype(bool)
        schedule_df = schedule_df.loc[anchor_mask].copy()

    list_like_columns = (
        "constraint_ids",
        "constraint_families",
        "raw_constraint_ids",
        "raw_constraint_families",
        "meta_constraint_ids",
        "info_constraint_ids",
        "violation_details",
        "info_details",
    )
    for column in list_like_columns:
        if column in schedule_df.columns:
            schedule_df[column] = schedule_df[column].apply(_parse_list_cell)

    return schedule_df.reset_index(drop=True).to_dict("records")


def count_workshop_order_inversions(schedule_df: pd.DataFrame) -> Tuple[int, List[Dict[str, Any]]]:
    examples: List[Dict[str, Any]] = []
    inversion_count = 0
    if "assembly_workshop_code" not in schedule_df.columns or "block_assembly_date" not in schedule_df.columns:
        return inversion_count, examples

    grouped: Dict[str, List[Tuple[int, str, str, int]]] = {}
    for idx, row in schedule_df.reset_index(drop=True).iterrows():
        workshop_code = str(row.get("assembly_workshop_code", "") or "").strip()
        date_text = str(row.get("block_assembly_date", "") or "").strip()
        block_name = str(row.get("block_name", "") or "")
        block_id = int(row.get("block_id", 0))
        if not workshop_code or not date_text:
            continue
        grouped.setdefault(workshop_code, []).append((idx, date_text, block_name, block_id))

    for workshop_code, items in grouped.items():
        for i in range(len(items)):
            left_idx, left_date, left_name, left_block_id = items[i]
            for j in range(i + 1, len(items)):
                right_idx, right_date, right_name, right_block_id = items[j]
                if right_date < left_date:
                    inversion_count += 1
                    if len(examples) < 10:
                        examples.append(
                            {
                                "workshop_code": workshop_code,
                                "earlier_sequence_index": left_idx + 1,
                                "earlier_block_id": left_block_id,
                                "earlier_block_name": left_name,
                                "earlier_block_assembly_date": left_date,
                                "later_sequence_index": right_idx + 1,
                                "later_block_id": right_block_id,
                                "later_block_name": right_name,
                                "later_block_assembly_date": right_date,
                            }
                        )
    return inversion_count, examples


def build_independent_audit_constraint_config(runtime_cfg: Optional[Dict[str, Any]] = None) -> ConstraintConfig:
    cfg = ConstraintConfig()
    runtime_cfg = runtime_cfg or {}
    # ==== [AGENT-EDIT BEGIN: independent audit constraint profile] ====
    # 독립 감사는 실제 제약을 고정 기준으로 다시 센다.
    # 생성 단계에서 특정 하드제약을 풀더라도, 최종 검수/카운트는 여기서 계속 잡혀야 한다.
    for attr_name, attr_value in INDEPENDENT_AUDIT_ATTRS.items():
        setattr(cfg, attr_name, attr_value)

    # [AGENT-ADD] audit-only overrides are applied after the canonical default profile.
    for attr_name, attr_value in get_audit_constraint_overrides(runtime_cfg).items():
        if isinstance(attr_name, str) and attr_name.startswith("enable_") and hasattr(cfg, attr_name):
            setattr(cfg, attr_name, bool(attr_value))

    cfg.enable_debug_violations = False
    cfg.calendar_overrides = build_calendar_overrides(runtime_cfg)
    # ==== [AGENT-EDIT END] ====
    return cfg




def _decode_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def apply_independent_audit_to_runtime_results(
    *,
    schedule_results: List[Dict[str, Any]],
    blocks,
    metadata: Dict[str, Any],
    runtime_cfg: Optional[Dict[str, Any]] = None,
    case_label: str = "runtime",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], pd.DataFrame]:
    """[AGENT-ADD] runtime 결과 row/statistics를 독립 감사 기준으로 동기화한다.

    주의:
    - 이 함수는 raw anchor rows(별판 확장 전 대표 row) 기준으로 동작한다.
    - 실행시 토글과 무관하게 실제 제약 감사 결과를 row/statistics에 덮어쓴다.
    """
    runtime_cfg = runtime_cfg or {}
    if not schedule_results:
        empty_stats = {
            "total_violations": 0,
            "total_violations_primary": 0,
            "total_violations_raw": 0,
            "total_violations_meta": 0,
            "total_violations_info": 0,
            "audit_workshop_order_inversions": 0,
            "audit_constraint_ids": [],
            "audit_constraint_families": [],
        }
        return list(schedule_results), empty_stats, pd.DataFrame()

    schedule_df = pd.DataFrame(schedule_results)
    env = _build_env_for_audit(
        blocks=copy.deepcopy(blocks),
        metadata=copy.deepcopy(metadata),
        runtime_cfg=runtime_cfg,
        schedule_df=schedule_df,
    )
    row_df, breakdown_df, summary_row = audit_schedule_dataframe(
        schedule_df=schedule_df,
        env=env,
        case_label=case_label,
        csv_path=Path(f"{case_label}.csv"),
    )

    updated_results: List[Dict[str, Any]] = []
    row_lookup = {int(row["row_index"]): row for _, row in row_df.iterrows()}
    json_fields = (
        "audit_constraint_ids",
        "audit_constraint_families",
        "audit_raw_constraint_ids",
        "audit_raw_constraint_families",
        "audit_meta_constraint_ids",
        "audit_info_constraint_ids",
        "audit_violation_details",
        "audit_info_details",
    )
    for idx, original_row in enumerate(schedule_results, start=1):
        updated = dict(original_row)
        audit_row = row_lookup.get(idx)
        if audit_row is not None:
            updated["violations"] = int(audit_row.get("audit_primary_count", 0) or 0)
            updated["violations_primary_count"] = int(audit_row.get("audit_primary_count", 0) or 0)
            updated["violations_raw_count"] = int(audit_row.get("audit_raw_count", 0) or 0)
            updated["violations_meta_count"] = int(audit_row.get("audit_meta_count", 0) or 0)
            updated["violations_info_count"] = int(audit_row.get("audit_info_count", 0) or 0)
            updated["constraint_ids"] = _decode_json_list(audit_row.get("audit_constraint_ids"))
            updated["constraint_families"] = _decode_json_list(audit_row.get("audit_constraint_families"))
            updated["raw_constraint_ids"] = _decode_json_list(audit_row.get("audit_raw_constraint_ids"))
            updated["raw_constraint_families"] = _decode_json_list(audit_row.get("audit_raw_constraint_families"))
            updated["meta_constraint_ids"] = _decode_json_list(audit_row.get("audit_meta_constraint_ids"))
            updated["info_constraint_ids"] = _decode_json_list(audit_row.get("audit_info_constraint_ids"))
            updated["violation_details"] = _decode_json_list(audit_row.get("audit_violation_details"))
            updated["info_details"] = _decode_json_list(audit_row.get("audit_info_details"))
            updated["audit_synced"] = True
        updated_results.append(updated)

    audit_stats = {
        "total_violations": int(summary_row.get("audit_primary", 0) or 0),
        "total_violations_primary": int(summary_row.get("audit_primary", 0) or 0),
        "total_violations_raw": int(summary_row.get("audit_raw", 0) or 0),
        "total_violations_meta": int(summary_row.get("audit_meta", 0) or 0),
        "total_violations_info": int(summary_row.get("audit_info", 0) or 0),
        "audit_workshop_order_inversions": int(summary_row.get("audit_workshop_order_inversions", 0) or 0),
        "audit_constraint_ids": _decode_json_list(summary_row.get("audit_constraint_ids")),
        "audit_constraint_families": _decode_json_list(summary_row.get("audit_constraint_families")),
    }
    return updated_results, audit_stats, breakdown_df


def _collect_current_summary(schedule_df: pd.DataFrame) -> Dict[str, Any]:
    summary = {
        "current_primary": 0,
        "current_raw": 0,
        "current_meta": 0,
        "current_info": 0,
        "current_constraint_ids": [],
    }
    for _, row in schedule_df.iterrows():
        summary["current_primary"] += int(row.get("violations_primary_count", row.get("violations", 0)) or 0)
        summary["current_raw"] += int(row.get("violations_raw_count", 0) or 0)
        summary["current_meta"] += int(row.get("violations_meta_count", 0) or 0)
        summary["current_info"] += int(row.get("violations_info_count", 0) or 0)
        summary["current_constraint_ids"].extend(_parse_list_cell(row.get("constraint_ids")))
    summary["current_constraint_ids"] = sorted(set(summary["current_constraint_ids"]))
    return summary




def verify_runtime_results_against_independent_audit(
    *,
    schedule_results: List[Dict[str, Any]],
    blocks,
    metadata: Dict[str, Any],
    runtime_cfg: Optional[Dict[str, Any]] = None,
    case_label: str = "runtime",
) -> Dict[str, Any]:
    """[AGENT-ADD] runtime 결과와 독립 감사 결과의 실제 제약 정합성을 검증한다.

    meta/relax 이벤트는 실행 제어 로그이므로 비교 대상에서 제외하고,
    실제 제약(primary/info)과 그 상세 ID가 같은지만 본다.
    """
    runtime_cfg = runtime_cfg or {}
    normalized_results = _normalize_runtime_results_for_audit(schedule_results)
    schedule_df = pd.DataFrame(normalized_results)
    env = _build_env_for_audit(
        blocks=copy.deepcopy(blocks),
        metadata=copy.deepcopy(metadata),
        runtime_cfg=runtime_cfg,
        schedule_df=schedule_df,
    )
    row_df, breakdown_df, summary_row = audit_schedule_dataframe(
        schedule_df=schedule_df,
        env=env,
        case_label=case_label,
        csv_path=Path(f"{case_label}.csv"),
    )
    mismatches: List[str] = []
    for _, audit_row in row_df.iterrows():
        row_idx = int(audit_row['row_index']) - 1
        runtime_row = normalized_results[row_idx]
        runtime_primary = int(runtime_row.get('violations_primary_count', runtime_row.get('violations', 0)) or 0)
        runtime_info = int(runtime_row.get('violations_info_count', 0) or 0)
        audit_primary = int(audit_row.get('audit_primary_count', 0) or 0)
        audit_info = int(audit_row.get('audit_info_count', 0) or 0)
        runtime_ids = sorted(str(x) for x in _parse_list_cell(runtime_row.get('constraint_ids')))
        audit_ids = sorted(str(x) for x in _decode_json_list(audit_row.get('audit_constraint_ids')))
        runtime_info_ids = sorted(str(x) for x in _parse_list_cell(runtime_row.get('info_constraint_ids')))
        audit_info_ids = sorted(str(x) for x in _decode_json_list(audit_row.get('audit_info_constraint_ids')))
        if runtime_primary != audit_primary or runtime_info != audit_info or runtime_ids != audit_ids or runtime_info_ids != audit_info_ids:
            mismatches.append(
                f"row={row_idx+1}, block_id={runtime_row.get('block_id')}, runtime_primary={runtime_primary}, audit_primary={audit_primary}, runtime_ids={runtime_ids}, audit_ids={audit_ids}, runtime_info={runtime_info}, audit_info={audit_info}"
            )
    return {
        'passed': len(mismatches) == 0,
        'mismatches': mismatches,
        'audit_stats': {
            'total_violations_primary': int(summary_row.get('audit_primary', 0) or 0),
            'total_violations_raw': int(summary_row.get('audit_raw', 0) or 0),
            'total_violations_meta': int(summary_row.get('audit_meta', 0) or 0),
            'total_violations_info': int(summary_row.get('audit_info', 0) or 0),
            'audit_workshop_order_inversions': int(summary_row.get('audit_workshop_order_inversions', 0) or 0),
            'audit_constraint_ids': _decode_json_list(summary_row.get('audit_constraint_ids')),
            'audit_constraint_families': _decode_json_list(summary_row.get('audit_constraint_families')),
        },
        'breakdown_df': breakdown_df,
    }


def sync_runtime_results_to_canonical_constraints(
    *,
    schedule_results: List[Dict[str, Any]],
    blocks,
    metadata: Dict[str, Any],
    runtime_cfg: Optional[Dict[str, Any]] = None,
    case_label: str = "runtime",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], pd.DataFrame]:
    """[AGENT-ADD] 최종 저장/평가용 runtime 결과를 canonical 제약 기준으로 동기화한다.

    스케줄 생성 중 env 상태 오염을 최종 집계에 남기지 않기 위해,
    clean replay로 다시 계산한 row/statistics를 최종 결과로 사용한다.
    이후 같은 replay를 한 번 더 수행해 post-hoc 정합성도 바로 확인한다.
    """
    synced_results, synced_stats, breakdown_df = apply_independent_audit_to_runtime_results(
        schedule_results=schedule_results,
        blocks=blocks,
        metadata=metadata,
        runtime_cfg=runtime_cfg,
        case_label=case_label,
    )
    verification = verify_runtime_results_against_independent_audit(
        schedule_results=synced_results,
        blocks=blocks,
        metadata=metadata,
        runtime_cfg=runtime_cfg,
        case_label=case_label,
    )
    if not verification.get("passed", False):
        raise RuntimeError(
            "canonical runtime/post-hoc mismatch: "
            + " | ".join(verification.get("mismatches", [])[:5])
        )
    return synced_results, synced_stats, breakdown_df

def _build_env_for_audit(*, blocks, metadata, runtime_cfg: Dict[str, Any], schedule_df: pd.DataFrame) -> EnhancedPanelBlockShop:
    start_candidates = [
        dt
        for dt in (_parse_datetime(value) for value in schedule_df.get("panel_start_time", []))
        if dt is not None
    ]
    start_time = min(start_candidates) if start_candidates else None
    audit_cfg = build_independent_audit_constraint_config(runtime_cfg)
    env = EnhancedPanelBlockShop(
        blocks=blocks,
        start_time=start_time,
        constraint_config=audit_cfg,
        metadata=metadata,
        decoding_mode="assembly",
    )
    return env


def audit_schedule_dataframe(*, schedule_df: pd.DataFrame, env: EnhancedPanelBlockShop, case_label: str, csv_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    # [AGENT-ADD] 독립 감사는 실행 당시 toggle과 무관하게 전체 실제 제약을 다시 센다.
    sequence_ids: List[int] = [int(block_id) for block_id in schedule_df["block_id"].tolist()]
    env.ps_manager.set_block_sequence(sequence_ids)
    env.constraint_checker.set_sequence(sequence_ids)

    row_records: List[Dict[str, Any]] = []
    row_violation_buckets: List[List[ConstraintViolation]] = []
    audit_all_violations: List[ConstraintViolation] = []
    blocks_dict = env.blocks_dict

    previous_panel_day: Optional[datetime.date] = None
    for row_idx, row in schedule_df.reset_index(drop=True).iterrows():
        block_id = int(row["block_id"])
        block = blocks_dict[block_id]
        assigned_bay = parse_assigned_bay(row["assigned_bay"])
        panel_start_time = _parse_datetime(row.get("panel_start_time")) or env.start_time
        final_end_time = _parse_datetime(row.get("final_end_time")) or panel_start_time
        processing_time_seconds = max(0.0, (final_end_time - panel_start_time).total_seconds())

        # ==== [AGENT-EDIT BEGIN: reset daily capacity per panel day in independent audit] ====
        # 실행 경로와 동일하게 날짜가 바뀌면 해당 일자의 용량 카운터를 리셋한다.
        current_panel_day = panel_start_time.date()
        if previous_panel_day is None or current_panel_day != previous_panel_day:
            if env.calendar_manager.is_weekend(panel_start_time):
                env.capacity_tracker.reset_weekend()
            else:
                env.capacity_tracker.reset_daily()
            previous_panel_day = current_panel_day
        # ==== [AGENT-EDIT END] ====

        # ==== [AGENT-EDIT BEGIN: use action-time validator to avoid double-counting P5#15/P5#16] ====
        # validate_all_constraints_realtime()는 add_block 이후 check_calendar_constraints()를 다시 불러
        # 혹서기/명절전날 용량을 현재 블록 기준으로 중복 계산한다.
        # 독립 감사는 실행 최종 집계와 맞춰야 하므로 action 경로 validator를 기준으로 사용한다.
        step_violations = env._validate_and_commit_constraints_action(
            block,
            assigned_bay,
            panel_start_time,
            actual_machine_2_start_time=panel_start_time,
            capacity_time_override=panel_start_time,
            processing_time_seconds=processing_time_seconds,
            step_start_time=panel_start_time,
            step_end_time=final_end_time,
            current_in_history=False,
        )
        # ==== [AGENT-ADD END] ====

        row_records.append(
            {
                "case_label": case_label,
                "csv_path": str(csv_path),
                "row_index": row_idx + 1,
                "block_id": block.block_id,
                "block_name": getattr(block, "block_name", f"BLK_{block.block_id}"),
                "assigned_bay": assigned_bay.value,
                "panel_start_time": panel_start_time.strftime("%Y-%m-%d %H:%M"),
                "final_end_time": final_end_time.strftime("%Y-%m-%d %H:%M"),
            }
        )
        row_violation_buckets.append(list(step_violations))
        audit_all_violations.extend(step_violations)
    for row_record, row_bucket in zip(row_records, row_violation_buckets):
        step_summary = summarize_violations(row_bucket, keep_info=True, include_guard=False)
        row_record.update(
            {
                "audit_raw_count": step_summary["violations_raw_count"],
                "audit_primary_count": step_summary["violations_primary_count"],
                "audit_meta_count": step_summary["violations_meta_count"],
                "audit_info_count": step_summary["violations_info_count"],
                "audit_constraint_ids": json.dumps(step_summary["constraint_ids"], ensure_ascii=False),
                "audit_constraint_families": json.dumps(step_summary["constraint_families"], ensure_ascii=False),
                "audit_raw_constraint_ids": json.dumps(step_summary["raw_constraint_ids"], ensure_ascii=False),
                "audit_raw_constraint_families": json.dumps(step_summary["raw_constraint_families"], ensure_ascii=False),
                "audit_meta_constraint_ids": json.dumps(step_summary["meta_constraint_ids"], ensure_ascii=False),
                "audit_info_constraint_ids": json.dumps(step_summary["info_constraint_ids"], ensure_ascii=False),
                "audit_violation_details": json.dumps(step_summary["violation_details"], ensure_ascii=False),
                "audit_info_details": json.dumps(step_summary["info_details"], ensure_ascii=False),
            }
        )

    overall_summary = summarize_violations(audit_all_violations, keep_info=True, include_guard=False)

    breakdown_rows: List[Dict[str, Any]] = []
    for violation in audit_all_violations:
        breakdown_rows.append(
            {
                "case_label": case_label,
                "constraint_id": violation.constraint_id,
                "constraint_family": normalize_constraint_family(violation.constraint_id or ""),
                "severity": (violation.severity or "INFO").upper(),
                "block_id": violation.block_id,
                "message": violation.message,
            }
        )
    breakdown_df = pd.DataFrame(breakdown_rows)
    if not breakdown_df.empty:
        breakdown_df = (
            breakdown_df.groupby(["case_label", "constraint_id", "constraint_family", "severity"], dropna=False)
            .size()
            .reset_index(name="count")
            .sort_values(["constraint_family", "constraint_id", "severity"], kind="stable")
            .reset_index(drop=True)
        )

    current_summary = _collect_current_summary(schedule_df)
    inversion_count, inversion_examples = count_workshop_order_inversions(schedule_df)

    panel_start_candidates = [_parse_datetime(value) for value in schedule_df.get("panel_start_time", [])]
    panel_start_candidates = [value for value in panel_start_candidates if value is not None]
    final_end_candidates = [_parse_datetime(value) for value in schedule_df.get("final_end_time", [])]
    final_end_candidates = [value for value in final_end_candidates if value is not None]
    makespan_hours = 0.0
    if panel_start_candidates and final_end_candidates:
        makespan_hours = round((max(final_end_candidates) - min(panel_start_candidates)).total_seconds() / 3600.0, 2)

    summary_row = {
        "case_label": case_label,
        "csv_path": str(csv_path),
        "row_count": len(schedule_df),
        "current_primary": current_summary["current_primary"],
        "current_raw": current_summary["current_raw"],
        "current_meta": current_summary["current_meta"],
        "current_info": current_summary["current_info"],
        "current_constraint_ids": json.dumps(current_summary["current_constraint_ids"], ensure_ascii=False),
        "audit_primary": overall_summary["violations_primary_count"],
        "audit_raw": overall_summary["violations_raw_count"],
        "audit_meta": overall_summary["violations_meta_count"],
        "audit_info": overall_summary["violations_info_count"],
        "audit_constraint_ids": json.dumps(overall_summary["constraint_ids"], ensure_ascii=False),
        "audit_constraint_families": json.dumps(overall_summary["constraint_families"], ensure_ascii=False),
        "audit_raw_constraint_ids": json.dumps(overall_summary["raw_constraint_ids"], ensure_ascii=False),
        "audit_workshop_order_inversions": inversion_count,
        "audit_workshop_order_examples": json.dumps(inversion_examples, ensure_ascii=False),
        "audit_makespan_hours": makespan_hours,
    }

    return pd.DataFrame(row_records), breakdown_df, summary_row


def _parse_case(case_text: str) -> Tuple[str, Path]:
    if "=" not in case_text:
        raise ValueError(f"--case 형식이 잘못되었습니다: {case_text}")
    label, path_text = case_text.split("=", 1)
    label = label.strip()
    path = Path(path_text.strip())
    if not label:
        raise ValueError(f"--case 라벨이 비어 있습니다: {case_text}")
    return label, path


def _case_uses_plan_sequence_loader(case_label: str, csv_path: Path) -> bool:
    """[AGENT-ADD] replay 결과는 순번 시트가 반영된 로더로 다시 읽어야 한다.

    일반 DataConverter 로더로 replay CSV를 재검증하면,
    Sheet1/계획 시트의 순번 정보가 빠진 블록 순서로 비교되어 false mismatch가 난다.
    """
    label = (case_label or "").strip().lower()
    name = csv_path.name.lower()
    return "replay" in label or name == "excel_sequence_standalone_results.csv"


def _load_blocks_and_metadata_for_case(
    *,
    excel_path: Path,
    runtime_cfg: Dict[str, Any],
    case_label: str,
    csv_path: Path,
):
    """[AGENT-ADD] 케이스별로 올바른 블록 로더를 선택한다."""
    if _case_uses_plan_sequence_loader(case_label, csv_path):
        from scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱 import load_excel_blocks_with_plan

        blocks, metadata, _ = load_excel_blocks_with_plan(str(excel_path))
        return blocks, metadata
    data_cfg = (runtime_cfg or {}).get("data", {}) or {}
    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(str(excel_path), data_cfg.get("sheet"))
    return blocks, metadata


def run_independent_audit(*, config_path: Path, cases: Sequence[Tuple[str, Path]], output_dir: Path) -> Dict[str, Path]:
    runtime_cfg = load_runtime_config(str(config_path))
    set_runtime_config(runtime_cfg)

    data_cfg = (runtime_cfg or {}).get("data", {}) or {}
    excel_path = Path(data_cfg.get("excel_path", "environment/판넬 블록 데이터셋_250618_SNU.xlsx"))

    output_dir.mkdir(parents=True, exist_ok=True)
    row_results: List[pd.DataFrame] = []
    breakdown_results: List[pd.DataFrame] = []
    summary_rows: List[Dict[str, Any]] = []

    for case_label, csv_path in cases:
        blocks, metadata = _load_blocks_and_metadata_for_case(
            excel_path=excel_path,
            runtime_cfg=runtime_cfg,
            case_label=case_label,
            csv_path=csv_path,
        )
        schedule_df = load_anchor_schedule(csv_path)
        env = _build_env_for_audit(
            blocks=copy.deepcopy(blocks),
            metadata=copy.deepcopy(metadata),
            runtime_cfg=runtime_cfg,
            schedule_df=schedule_df,
        )
        row_df, breakdown_df, summary_row = audit_schedule_dataframe(
            schedule_df=schedule_df,
            env=env,
            case_label=case_label,
            csv_path=csv_path,
        )
        row_results.append(row_df)
        if not breakdown_df.empty:
            breakdown_results.append(breakdown_df)
        summary_rows.append(summary_row)

    summary_df = pd.DataFrame(summary_rows)
    row_df_all = pd.concat(row_results, ignore_index=True) if row_results else pd.DataFrame()
    breakdown_df_all = pd.concat(breakdown_results, ignore_index=True) if breakdown_results else pd.DataFrame()

    summary_csv = output_dir / "independent_constraint_audit_summary.csv"
    rows_csv = output_dir / "independent_constraint_audit_rows.csv"
    breakdown_csv = output_dir / "independent_constraint_audit_breakdown.csv"
    catalog_csv = output_dir / "independent_constraint_catalog.csv"

    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    row_df_all.to_csv(rows_csv, index=False, encoding="utf-8-sig")
    breakdown_df_all.to_csv(breakdown_csv, index=False, encoding="utf-8-sig")
    pd.DataFrame([entry.__dict__ for entry in CONSTRAINT_CATALOG]).to_csv(catalog_csv, index=False, encoding="utf-8-sig")

    return {
        "summary_csv": summary_csv,
        "rows_csv": rows_csv,
        "breakdown_csv": breakdown_csv,
        "catalog_csv": catalog_csv,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Independent audit for saved PBS schedule CSVs")
    parser.add_argument("--config", default="config.yaml", help="runtime config yaml/json path")
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="case label and csv path in the form LABEL=PATH",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="directory where independent audit csv outputs will be saved",
    )
    args = parser.parse_args(argv)

    if not args.case:
        raise SystemExit("--case 가 최소 1개 필요합니다.")

    cases = [_parse_case(case_text) for case_text in args.case]
    outputs = run_independent_audit(
        config_path=Path(args.config),
        cases=cases,
        output_dir=Path(args.output_dir),
    )

    print("[IndependentAudit] saved:")
    for key, path in outputs.items():
        print(f"  - {key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
