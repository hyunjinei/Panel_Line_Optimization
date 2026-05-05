# [AGENT-ADD] Canonical metric / constraint taxonomy regression tests.

from datetime import datetime
import sys
import types

import pandas as pd
import yaml

# [AGENT-EDIT] 테스트 환경에는 gymnasium이 없을 수 있으므로 최소 stub를 주입한다.
if "gymnasium" not in sys.modules:
    gym_stub = types.ModuleType("gymnasium")

    class _DummyEnv:
        pass

    class _DummyBox:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _DummyDiscrete:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    gym_stub.Env = _DummyEnv
    gym_stub.spaces = types.SimpleNamespace(Box=_DummyBox, Discrete=_DummyDiscrete)
    sys.modules["gymnasium"] = gym_stub

from enhanced_environment.common.violation_utils import summarize_violations
from enhanced_environment.common.utils_core import (
    compute_schedule_span_hours,
    count_expanded_block_units,
)
from enhanced_environment.pbs_env.bay_ops import BayOpsMixin
from enhanced_environment.common.data_converter import DataConverter
from enhanced_environment.constraints.config import ConstraintConfig
from enhanced_environment.masking.ps_mixing import PSMixingMixin
from enhanced_environment.models import BayType, ConstraintViolation, EnhancedBlock
from scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱 import (
    apply_plan_sequence_from_excel,
)
from scheduling.common.run_artifacts import (
    resolve_output_path,
    summarize_result_csv,
)


def _make_block(block_id: int, **overrides) -> EnhancedBlock:
    base = dict(
        block_id=block_id,
        processing_times=[1.0] * 8,
        max_start_date=datetime(2025, 1, 1, 8, 0),
        assembly_start_date=datetime(2025, 1, 1, 8, 0),
    )
    base.update(overrides)
    return EnhancedBlock(**base)


def test_summarize_violations_splits_primary_and_meta() -> None:
    violations = [
        ConstraintViolation("P5#15", "holiday eve violation", "ERROR", 1),
        ConstraintViolation("RELAX_STAGE", "완화 적용", "INFO", 1),
        ConstraintViolation("EMERGENCY_RELEASE", "forced continuation", "WARNING", 1),
    ]

    summary = summarize_violations(violations, keep_info=True, include_guard=False)

    assert summary["violations_primary_count"] == 1
    assert summary["violations_meta_count"] == 2
    assert summary["violations"] == 1
    assert "HOLIDAY_EVE_FAMILY" in summary["constraint_families"]


def test_c_seam_and_cross_seam_use_different_families() -> None:
    violations = [
        ConstraintViolation("ROUTING_C_SEAM_SPACING", "c seam adjacency", "ERROR", 11),
        ConstraintViolation("P6#4", "cross seam mixing", "ERROR", 11),
    ]

    summary = summarize_violations(violations, keep_info=True, include_guard=False)

    assert summary["violations_primary_count"] == 2
    assert "C_SEAM_SPACING_FAMILY" in summary["constraint_families"]
    assert "CROSS_SEAM_MIXING_FAMILY" in summary["constraint_families"]


def test_p6_time_constraints_are_removed_from_block_model() -> None:
    block = _make_block(
        7,
        is_draft=True,
        is_cross_seam=True,
        main_plate_count=99,
    )

    assert block.needs_afternoon_start() is False


def test_unknown_constraint_defaults_to_disabled() -> None:
    config = ConstraintConfig()
    assert config.is_constraint_enabled("UNKNOWN_CONSTRAINT") is False


def test_ps_immediate_follow_force_is_disabled() -> None:
    class _DummyMask(PSMixingMixin):
        def __init__(self) -> None:
            self.constraint_config = ConstraintConfig()

    dummy = _DummyMask()

    assert dummy._apply_ps_pair_masking([], [1], [], []) == []


def test_expanded_block_unit_count_uses_subassembly_originals() -> None:
    rep = _make_block(100)
    part_a = _make_block(101)
    part_b = _make_block(102)
    rep.subassembly_original_blocks = [part_a, part_b]

    single = _make_block(103)
    single.subassembly_original_blocks = [single]

    assert count_expanded_block_units([rep, single]) == 3


def test_replay_accepts_builtin_sequence_sheet(tmp_path) -> None:
    excel_path = tmp_path / "builtin_sequence.xlsx"
    df = pd.DataFrame(
        [
            {
                "블록번호": "A",
                "소조번호": "",
                "순번": 2,
                "론지 작업장": 1,
                "착수일": 20250609,
            },
            {
                "블록번호": "B",
                "소조번호": "",
                "순번": 1,
                "론지 작업장": 2,
                "착수일": 20250609,
            },
        ]
    )
    df.to_excel(excel_path, index=False, sheet_name="Sheet1")

    block_a = _make_block(201)
    block_b = _make_block(202)
    block_a.block_name = "A"
    block_b.block_name = "B"
    block_a.sub_assembly_number = ""
    block_b.sub_assembly_number = ""

    applied = apply_plan_sequence_from_excel([block_a, block_b], str(excel_path))

    assert applied == ("Sheet1",)
    assert block_a.sequence_number == 2
    assert block_b.sequence_number == 1


def test_config_heuristic_method_is_not_overwritten() -> None:
    with open("config.yaml", "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    heuristic_cfg = cfg.get("heuristic") or {}
    assert heuristic_cfg.get("entry") == "assembly_start"
    assert heuristic_cfg.get("method") == "lpt"


def test_compute_schedule_span_hours_uses_wall_clock_window() -> None:
    results = [
        {
            "panel_start_time": "2025-06-09 08:00",
            "final_end_time": "2025-06-09 18:00",
        },
        {
            "panel_start_time": "2025-06-10 08:00",
            "final_end_time": "2025-06-13 01:25",
        },
    ]

    assert round(compute_schedule_span_hours(results), 2) == 89.42


def test_expand_rows_with_subassembly_keeps_metrics_on_anchor_only() -> None:
    rows = [
        {
            "block_id": 900,
            "block_name": "REP",
            "violations": 2,
            "violations_raw_count": 5,
            "violations_primary_count": 2,
            "violations_meta_count": 2,
            "violations_info_count": 1,
            "constraint_ids": ["ROUTING_C_SEAM_SPACING", "ROUTING_WORKSHOP_ORDER"],
            "raw_constraint_ids": ["RELAX_STAGE", "ROUTING_C_SEAM_SPACING"],
            "meta_constraint_ids": ["RELAX_STAGE"],
            "info_constraint_ids": ["P5#17"],
            "subassembly_expansion": [
                {"block_id": 901, "block_name": "SUB-A"},
                {"block_id": 902, "block_name": "SUB-B"},
            ],
        }
    ]

    expanded = DataConverter.expand_rows_with_subassembly(rows)

    assert len(expanded) == 2
    anchor_rows = [row for row in expanded if row["is_metric_anchor_row"]]
    member_rows = [row for row in expanded if not row["is_metric_anchor_row"]]
    assert len(anchor_rows) == 1
    assert len(member_rows) == 1
    assert anchor_rows[0]["violations_raw_count"] == 5
    assert member_rows[0]["violations_raw_count"] == 0
    assert member_rows[0]["violations_primary_count"] == 0
    assert member_rows[0]["constraint_ids"] == []


def test_resolve_output_path_uses_tagged_directory(tmp_path) -> None:
    path = resolve_output_path(
        default_filename="result.csv",
        mode="start_date",
        output_dir=str(tmp_path),
        run_tag="fresh run 01",
    )

    assert path.parent.name == "start_date"
    assert path.name == "result.csv"
    assert "fresh_run_01" in str(path.parent.parent)


def test_summarize_result_csv_uses_wall_clock_span(tmp_path) -> None:
    csv_path = tmp_path / "result.csv"
    pd.DataFrame(
        [
            {
                "block_id": 1,
                "is_metric_anchor_row": 1,
                "panel_start_time": "2025-06-09 08:00",
                "final_end_time": "2025-06-09 18:00",
                "violations_primary_count": 1,
                "violations_raw_count": 2,
                "violations_meta_count": 1,
                "violations_info_count": 0,
                "constraint_ids": "[\"ROUTING_C_SEAM_SPACING\"]",
            },
            {
                "block_id": 2,
                "is_metric_anchor_row": 1,
                "panel_start_time": "2025-06-10 08:00",
                "final_end_time": "2025-06-13 01:25",
                "violations_primary_count": 0,
                "violations_raw_count": 1,
                "violations_meta_count": 1,
                "violations_info_count": 1,
                "constraint_ids": "[]",
            },
        ]
    ).to_csv(csv_path, index=False, encoding="utf-8-sig")

    summary = summarize_result_csv(csv_path, mode="test")

    assert summary["canonical_makespan_hours"] == 89.42
    assert summary["primary_count"] == 1
    assert summary["raw_count"] == 3
    assert summary["meta_count"] == 2
    assert summary["info_count"] == 1


def test_manual_bay_override_keeps_original_auto_assign_path(monkeypatch) -> None:
    # [AGENT-ADD] manual override가 없으면 원본 auto-assign 경로를 그대로 통과해야 한다.
    class _DummyEnv(BayOpsMixin):
        def __init__(self) -> None:
            self.constraint_config = ConstraintConfig()
            self.bay_tracker = object()
            self.ps_manager = object()
            self.blocks_dict = {}
            self.logger = object()
            self._manual_bay_override = None

    env = _DummyEnv()
    block = _make_block(301)
    call_flags = {"auto": 0, "fixed": 0}

    def _fake_auto_assign(*args, **kwargs):
        call_flags["auto"] += 1
        return BayType.BAY_35A

    def _fake_fixed_assign(*args, **kwargs):
        call_flags["fixed"] += 1
        return BayType.BAY_36B

    monkeypatch.setattr("enhanced_environment.pbs_env.bay_ops.auto_assign_bay", _fake_auto_assign)
    monkeypatch.setattr("enhanced_environment.pbs_env.bay_ops.assign_fixed_bay", _fake_fixed_assign)

    assigned = env._auto_assign_bay(block)

    assert assigned == BayType.BAY_35A
    assert call_flags["auto"] == 1
    assert call_flags["fixed"] == 0


def test_manual_bay_override_is_one_shot(monkeypatch) -> None:
    # [AGENT-EDIT] interactive/manual override는 지정한 블록 한 번에만 적용되고 즉시 해제되어야 한다.
    class _DummyEnv(BayOpsMixin):
        def __init__(self) -> None:
            self.constraint_config = ConstraintConfig()
            self.bay_tracker = object()
            self.ps_manager = object()
            self.blocks_dict = {}
            self.logger = object()
            self._manual_bay_override = None

    env = _DummyEnv()
    block = _make_block(302)
    call_flags = {"auto": 0, "fixed": 0}

    def _fake_auto_assign(*args, **kwargs):
        call_flags["auto"] += 1
        return BayType.BAY_35A

    def _fake_fixed_assign(*args, **kwargs):
        call_flags["fixed"] += 1
        return BayType.BAY_36B

    monkeypatch.setattr("enhanced_environment.pbs_env.bay_ops.auto_assign_bay", _fake_auto_assign)
    monkeypatch.setattr("enhanced_environment.pbs_env.bay_ops.assign_fixed_bay", _fake_fixed_assign)

    env._set_manual_bay_override(block.block_id, BayType.BAY_36B, {"final_reason": "TEST_OVERRIDE"})
    assigned_first = env._auto_assign_bay(block)
    assigned_second = env._auto_assign_bay(block)

    assert assigned_first == BayType.BAY_36B
    assert assigned_second == BayType.BAY_35A
    assert call_flags["fixed"] == 1
    assert call_flags["auto"] == 1
    assert env._manual_bay_override is None
