# [AGENT-ADD] Split from constraint_config.py to improve readability.
"""Preset helpers for ConstraintConfig."""

from .config import ConstraintConfig


def get_all_enabled_config() -> ConstraintConfig:
    """모든 제약조건 활성화 설정."""
    return ConstraintConfig()


def get_all_disabled_config() -> ConstraintConfig:
    """모든 제약조건 비활성화 설정."""
    config = ConstraintConfig()
    config.disable_all_constraints()
    return config


def get_basic_constraints_only_config() -> ConstraintConfig:
    """기본 제약조건만 활성화 (P5#1, P5#13, P7#2)."""
    config = get_all_disabled_config()
    config.enable_p5_1_delivery_date = True
    config.enable_p5_13_material_ready = True
    config.enable_p7_2_width_21m_bay_b = True
    config.enable_p5_panel_constraints = True
    config.enable_p7_longi_constraints = True
    return config


def get_panel_work_only_config() -> ConstraintConfig:
    """판계 작업 제약조건만 활성화."""
    config = get_all_disabled_config()
    config.enable_p5_panel_constraints = True
    return config


def get_saw_work_only_config() -> ConstraintConfig:
    """SAW 공정 제약조건만 활성화."""
    config = get_all_disabled_config()
    config.enable_p6_saw_constraints = True
    return config


def get_longi_work_only_config() -> ConstraintConfig:
    """론지 취부 제약조건만 활성화."""
    config = get_all_disabled_config()
    config.enable_p7_longi_constraints = True
    return config
