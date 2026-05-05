from pathlib import Path

from scheduling.common.independent_constraint_audit import (
    build_independent_audit_constraint_config,
    count_workshop_order_inversions,
    load_anchor_schedule,
)
from enhanced_environment.constraints import ConstraintConfig


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_independent_audit_forces_real_workshop_order_on():
    cfg = build_independent_audit_constraint_config({"constraints": {"enable_routing_workshop_order": False}})
    assert cfg.enable_routing_workshop_order is True
    assert cfg.enable_p6_1_draft_afternoon is False
    assert cfg.enable_p6_2_cross_seam_afternoon is False
    assert cfg.enable_p6_3_dc_block_afternoon is False


def test_workshop_order_off_csv_has_real_inversions():
    csv_path = REPO_ROOT / "results/fresh_runs/bias_workshop_order_matrix_20260403/runs/wh0_ll0_wf0_rwo0/assembly_lpt.csv"
    schedule_df = load_anchor_schedule(csv_path)
    inversion_count, examples = count_workshop_order_inversions(schedule_df)
    assert inversion_count > 0
    assert examples


def test_bias_false_csv_keeps_workshop_order_zero():
    csv_path = REPO_ROOT / "results/fresh_runs/masking_bias_matrix_20260403/runs/wh0_ll0_wf0/assembly_lpt.csv"
    schedule_df = load_anchor_schedule(csv_path)
    inversion_count, examples = count_workshop_order_inversions(schedule_df)
    assert inversion_count == 0
    assert examples == []


# [AGENT-ADD] runtime enforcement override와 canonical audit count가 분리되어야 함을 고정한다.
def test_independent_audit_keeps_real_constraint_counts_even_when_runtime_enforcement_is_disabled():
    cfg = build_independent_audit_constraint_config({
        "constraints": {
            "enable_p7_7_bay_a_consecutive": False,
            "enable_p5_16_hot_season_capacity": False,
            "enable_routing_workshop_order": False,
        }
    })
    assert cfg.enable_p7_7_bay_a_consecutive is True
    assert cfg.enable_p5_16_hot_season_capacity is True
    assert cfg.enable_routing_workshop_order is True


# [AGENT-ADD] 반대로 runtime ConstraintConfig는 실제 생성 단계에서 false override를 그대로 받아야 한다.
def test_runtime_constraint_config_still_honors_real_constraint_disable_override(monkeypatch):
    monkeypatch.setattr(
        'runtime_config._RUNTIME_CONFIG',
        {'constraints': {
            'enable_p7_7_bay_a_consecutive': False,
            'enable_p5_16_hot_season_capacity': False,
            'enable_routing_workshop_order': False,
        }},
    )
    cfg = ConstraintConfig()
    assert cfg.enable_p7_7_bay_a_consecutive is False
    assert cfg.enable_p5_16_hot_season_capacity is False
    assert cfg.enable_routing_workshop_order is False
