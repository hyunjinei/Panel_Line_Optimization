# [AGENT-ADD] Split from constraint_config.py to improve readability.
"""
Constraint configuration model.

Keep all fields identical to the legacy constraint_config.py to preserve behavior.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any

from runtime_config import get_constraint_overrides, build_calendar_overrides  # [AGENT-ADD] 런타임 설정 반영


@dataclass
class ConstraintConfig:
    """
    제약조건 설정 클래스

    그룹별 설정과 개별 설정을 모두 지원
    그룹이 비활성화되면 해당 그룹의 모든 개별 제약조건도 비활성화
    """

    # ========================================
    # 그룹별 활성화/비활성화 설정
    # ========================================
    enable_p5_panel_constraints: bool = True
    enable_p6_saw_constraints: bool = True
    enable_p7_longi_constraints: bool = True

    # ========================================
    # P5: 판계 작업 제약조건 (개별 설정)
    # ========================================
    enable_p5_1_delivery_date: bool = False
    enable_p5_3_ps_line_continuous: bool = True
    enable_p5_4_ps_fixed_continuous: bool = False
    enable_p5_6_line_b_fab_interval: bool = False
    enable_p5_8_weekday_capacity: bool = True
    enable_p5_9_block_count_check: bool = True
    enable_p5_10_weekend_capacity: bool = True
    enable_p5_11_fixed_line_mixing: bool = False
    enable_p5_12_internal_external_mixing: bool = False
    enable_p5_13_material_ready: bool = True
    enable_p5_15_holiday_shift: bool = True
    enable_p5_16_hot_season_capacity: bool = True
    enable_p5_17_subassembly_grouping: bool = True

    # ========================================
    # P6: 전/후면 SAW 제약조건 (개별 설정)
    # ========================================
    enable_p6_1_draft_afternoon: bool = True
    enable_p6_2_cross_seam_afternoon: bool = True
    enable_p6_3_dc_block_afternoon: bool = True
    enable_p6_4_cross_seam_mixing: bool = True

    # ========================================
    # P7: 론지 취부 제약조건 (개별 설정)
    # ========================================
    enable_p7_1_bay_load_balance: bool = True
    enable_p7_2_width_21m_bay_b: bool = True
    enable_p7_3_ps_same_bay_longi_7: bool = True
    enable_p7_4_ps_same_bay_date_1: bool = True
    enable_p7_7_bay_a_consecutive: bool = True
    enable_p7_8_main_plate_consecutive: bool = True
    enable_p7_10_longi_30_bay_a: bool = True
    enable_p7_11_block_10_bay_b: bool = False
    enable_p7_12_lt_steel_bay_a: bool = True

    # ========================================
    # Assembly/Relaxation 제어 플래그
    # ========================================
    allow_capacity_relaxation: bool = False

    # ========================================
    # 연속 3판 B베이 방지 제약조건
    # ========================================
    enable_consecutive_3bay_prevention: bool = True
    enable_consecutive_b_bay_prevention: bool = True

    # 작업장 우선순위 확장 파라미터
    workshop_priority_initial_window_days: int = 3
    workshop_priority_max_window_days: int = 9
    assembly_expansion_startday_mode: int = 1
    assembly_expansion_days: int = 3

    # 리드타임/착수일 완화 설정
    minimum_panel_lead_days: int = 2
    pre_relax_window_extra_days: int = 5
    pre_relax_min_lead_days: int = 2
    relax_min_lead_days: int = 2
    deadline_force_buffer_days: int = 0
    allow_deadline_lead_override: bool = False
    enable_deadline_forced_selection: bool = False
    leadtime_guard_respect_p6: bool = True

    # 라인 그룹/고심수 론지 제약 설정
    enable_line_group_constraint: bool = True
    line_group_strict_limit: int = 2
    line_group_soft_limit: int = 3
    line_group_use_soft_limit: bool = False
    enable_high_longi_split: bool = False
    treat_longi30_as_block10: bool = False
    high_longi_threshold: int = 24

    enable_longi_load_balance: bool = True

    enable_c_seam_spacing: bool = True
    c_seam_spacing_gap: int = 2

    # Routing flags
    enable_routing_curved_spacing: bool = True
    enable_routing_high_seam_spacing: bool = True
    enable_routing_workshop_order: bool = True

    # Assembly mixing advanced settings
    assembly_mixing_use_category: bool = True
    assembly_mixing_max_consecutive: int = 2
    assembly_mixing_allow_exhaustion_relief: bool = False

    # Emergency mode
    enable_emergency_mode: bool = False

    # ========================================
    # 용량 관리 하이퍼파라미터 (P5#8,9,10,15,16)
    # ========================================
    weekday_seam_limit: int = 75
    weekend_seam_limit: int = 45

    holiday_eve_seam_reduction_enabled: bool = True
    holiday_eve_seam_limit: int = 35

    hot_season_reduction_type: str = "absolute"
    hot_season_seam_reduction: int = 6
    hot_season_percentage_reduction: int = 15

    # ========================================
    # 추가 설정
    # ========================================
    enable_strict_mode: bool = False
    enable_debug_violations: bool = True

    # ========================================
    # 런타임 오버라이드 (main.py 설정)
    # ========================================
    relax_order: List[str] = field(default_factory=list)
    relax_order_start_date: List[str] = field(default_factory=list)
    relax_order_assembly: List[str] = field(default_factory=list)
    strict_rules: List[str] = field(default_factory=list)
    enabled_constraints_start_date: List[str] = field(default_factory=list)
    enabled_constraints_assembly: List[str] = field(default_factory=list)
    hard_constraints_start_date: List[str] = field(default_factory=list)
    hard_constraints_assembly: List[str] = field(default_factory=list)
    daily_block_cap_overrides: Dict[str, int] = field(default_factory=dict)
    daily_seam_cap_overrides: Dict[str, int] = field(default_factory=dict)
    daily_seam_cap_scales: Dict[str, float] = field(default_factory=dict)
    calendar_overrides: Dict[str, Any] = field(default_factory=dict)
    constraint_scope: str = ""

    def __post_init__(self) -> None:
        """런타임 설정 파일 기반 오버라이드 적용."""
        # [AGENT-ADD] runtime_config에서 제약/캘린더 오버라이드 반영
        overrides = get_constraint_overrides()
        if overrides:
            if not self.relax_order and overrides.get("relax_order"):
                self.relax_order = list(overrides.get("relax_order") or [])
            if not self.relax_order_start_date and overrides.get("relax_order_start_date"):
                self.relax_order_start_date = list(overrides.get("relax_order_start_date") or [])
            if not self.relax_order_assembly and overrides.get("relax_order_assembly"):
                self.relax_order_assembly = list(overrides.get("relax_order_assembly") or [])
            if not self.strict_rules and overrides.get("strict_rules"):
                self.strict_rules = list(overrides.get("strict_rules") or [])
            if not self.enabled_constraints_start_date and overrides.get("enabled_constraints_start_date"):
                self.enabled_constraints_start_date = list(overrides.get("enabled_constraints_start_date") or [])
            if not self.enabled_constraints_assembly and overrides.get("enabled_constraints_assembly"):
                self.enabled_constraints_assembly = list(overrides.get("enabled_constraints_assembly") or [])
            if not self.hard_constraints_start_date and overrides.get("hard_constraints_start_date"):
                self.hard_constraints_start_date = list(overrides.get("hard_constraints_start_date") or [])
            if not self.hard_constraints_assembly and overrides.get("hard_constraints_assembly"):
                self.hard_constraints_assembly = list(overrides.get("hard_constraints_assembly") or [])
            if not self.daily_block_cap_overrides and overrides.get("daily_block_cap_overrides"):
                self.daily_block_cap_overrides = dict(overrides.get("daily_block_cap_overrides") or {})
            if not self.daily_seam_cap_overrides and overrides.get("daily_seam_cap_overrides"):
                self.daily_seam_cap_overrides = dict(overrides.get("daily_seam_cap_overrides") or {})
            if not self.daily_seam_cap_scales and overrides.get("daily_seam_cap_scales"):
                self.daily_seam_cap_scales = dict(overrides.get("daily_seam_cap_scales") or {})

        # [AGENT-ADD] 공통 relax_order를 start/assembly에 보정 적용
        if self.relax_order:
            if not self.relax_order_start_date:
                self.relax_order_start_date = list(self.relax_order)
            if not self.relax_order_assembly:
                self.relax_order_assembly = list(self.relax_order)

        if not self.calendar_overrides:
            self.calendar_overrides = build_calendar_overrides()

        # [AGENT-ADD] 한글/영문 혼용 입력을 constraint id 리스트로 정규화
        self.enabled_constraints_start_date = self._normalize_constraint_keys(self.enabled_constraints_start_date)
        self.enabled_constraints_assembly = self._normalize_constraint_keys(self.enabled_constraints_assembly)
        self.hard_constraints_start_date = self._normalize_constraint_keys(self.hard_constraints_start_date)
        self.hard_constraints_assembly = self._normalize_constraint_keys(self.hard_constraints_assembly)
        self.strict_rules = self._normalize_constraint_keys(self.strict_rules)

    def is_constraint_enabled(self, constraint_id: str) -> bool:
        """특정 제약조건 활성 여부 확인."""
        # [AGENT-ADD] scope별 enabled 리스트가 있으면 필터로 적용
        if self.constraint_scope == "start_date" and self.enabled_constraints_start_date:
            if constraint_id not in self.enabled_constraints_start_date:
                return False
        if self.constraint_scope == "assembly" and self.enabled_constraints_assembly:
            if constraint_id not in self.enabled_constraints_assembly:
                return False

        if constraint_id.startswith("P5#"):
            if not self.enable_p5_panel_constraints:
                return False
        elif constraint_id.startswith("P6#"):
            if not self.enable_p6_saw_constraints:
                return False
        elif constraint_id.startswith("P7#"):
            if not self.enable_p7_longi_constraints:
                return False

        constraint_mapping = {
            "P5#1": self.enable_p5_1_delivery_date,
            "P5#3": self.enable_p5_3_ps_line_continuous,
            "P5#4": self.enable_p5_4_ps_fixed_continuous,
            "P5#6": self.enable_p5_6_line_b_fab_interval,
            "P5#8": self.enable_p5_8_weekday_capacity,
            "P5#9": self.enable_p5_9_block_count_check,
            "P5#10": self.enable_p5_10_weekend_capacity,
            "P5#11": self.enable_p5_11_fixed_line_mixing,
            "P5#12": self.enable_p5_12_internal_external_mixing,
            "P5#13": self.enable_p5_13_material_ready,
            "P5#15": self.enable_p5_15_holiday_shift,
            "P5#16": self.enable_p5_16_hot_season_capacity,
            "P5#17": self.enable_p5_17_subassembly_grouping,

            "P6#1": self.enable_p6_1_draft_afternoon,
            "P6#2": self.enable_p6_2_cross_seam_afternoon,
            "P6#3": self.enable_p6_3_dc_block_afternoon,
            "P6#4": self.enable_p6_4_cross_seam_mixing,

            "P7#1": self.enable_p7_1_bay_load_balance,
            "P7#2": self.enable_p7_2_width_21m_bay_b,
            "P7#3": self.enable_p7_3_ps_same_bay_longi_7,
            "P7#4": self.enable_p7_4_ps_same_bay_date_1,
            "P7#7": self.enable_p7_7_bay_a_consecutive,
            "P7#8": self.enable_p7_8_main_plate_consecutive,
            "P7#10": self.enable_p7_10_longi_30_bay_a,
            "P7#11": self.enable_p7_11_block_10_bay_b,
            "P7#12": self.enable_p7_12_lt_steel_bay_a,

            "CONSECUTIVE_3BAY": self.enable_consecutive_3bay_prevention,
            "CONSECUTIVE_B_BAY": self.enable_consecutive_b_bay_prevention,
            # [AGENT-EDIT] 고론지 연속 송선 금지 (동적 토글)
            "HIGH_LONGI_SPLIT": self.enable_high_longi_split,
            "C_SEAM_SPACING": self.enable_c_seam_spacing,
            "ROUTING_CURVED_SPACING": self.enable_routing_curved_spacing,
            "ROUTING_HIGH_SEAM_SPACING": self.enable_routing_high_seam_spacing,
            "ROUTING_WORKSHOP_ORDER": self.enable_routing_workshop_order,
            "ROUTING_C_SEAM_SPACING": self.enable_c_seam_spacing,
            "LINE_GROUP_CONSTRAINT": self.enable_line_group_constraint,
        }

        return constraint_mapping.get(constraint_id, True)

    def set_constraint_scope(self, scope: str) -> None:
        """현재 제약 스코프 설정 (start_date/assembly)."""
        self.constraint_scope = scope or ""

    def _normalize_constraint_keys(self, raw_items: List[str]) -> List[str]:
        """한글/영문 혼용 제약 키를 constraint id 리스트로 변환."""
        if not raw_items:
            return []

        def _normalize(text: str) -> str:
            return "".join(ch for ch in text.strip().lower() if ch.isalnum() or "가" <= ch <= "힣")

        mapping = {
            "라인고정간격": ["LINE_GROUP_CONSTRAINT"],
            "라인그룹": ["LINE_GROUP_CONSTRAINT"],
            "라인연속": ["LINE_GROUP_CONSTRAINT"],
            "라인혼합": ["P5#11", "P5#12"],
            "혼합": ["P5#11", "P5#12"],
            "용량": ["P5#8", "P5#9", "P5#10", "P5#16"],
            "cseam": ["ROUTING_C_SEAM_SPACING"],
            "cseam완화": ["ROUTING_C_SEAM_SPACING"],
            "cseam간격": ["ROUTING_C_SEAM_SPACING"],
            "곡판": ["ROUTING_CURVED_SPACING"],
            "곡판간격": ["ROUTING_CURVED_SPACING"],
            "고심수": ["ROUTING_HIGH_SEAM_SPACING"],
            "고심수간격": ["ROUTING_HIGH_SEAM_SPACING"],
            "p6": ["P6#1", "P6#2", "P6#3"],
            "p6시간": ["P6#1", "P6#2", "P6#3"],
            "p64": ["P6#4"],
            "p615": ["P5#15"],
            "p515": ["P5#15"],
            "3bay": ["CONSECUTIVE_3BAY"],
            "3bay완화": ["CONSECUTIVE_3BAY"],
            "3베이": ["CONSECUTIVE_3BAY"],
            "작업장순서": ["ROUTING_WORKSHOP_ORDER"],
            "ps연속": ["P5#3", "P5#4"],
            "ps연속라인": ["P5#3"],
            "ps연속고정": ["P5#4"],
            # [AGENT-EDIT] 고론지 연속 송선 금지 키
            "고론지연속금지": ["HIGH_LONGI_SPLIT"],
            "론지연속금지": ["HIGH_LONGI_SPLIT"],
        }

        normalized: List[str] = []
        for raw in raw_items:
            if raw is None:
                continue
            text = str(raw).strip()
            if not text:
                continue
            if "#" in text or text.isupper():
                normalized.append(text)
                continue
            key = _normalize(text)
            if not key:
                continue
            if key in mapping:
                normalized.extend(mapping[key])
            else:
                normalized.append(text)

        deduped: List[str] = []
        seen = set()
        for item in normalized:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def disable_all_constraints(self) -> None:
        """모든 제약조건 비활성화."""
        self.enable_p5_panel_constraints = False
        self.enable_p6_saw_constraints = False
        self.enable_p7_longi_constraints = False
        self.enable_consecutive_3bay_prevention = False
        self.enable_consecutive_b_bay_prevention = False
        self.enable_c_seam_spacing = False
        self.enable_routing_curved_spacing = False
        self.enable_routing_high_seam_spacing = False
        self.enable_routing_workshop_order = False

    def enable_all_constraints(self) -> None:
        """모든 제약조건 활성화."""
        self.enable_p5_panel_constraints = True
        self.enable_p6_saw_constraints = True
        self.enable_p7_longi_constraints = True
        self.enable_consecutive_3bay_prevention = True
        self.enable_consecutive_b_bay_prevention = True
        self.enable_c_seam_spacing = True
        self.enable_routing_curved_spacing = True
        self.enable_routing_high_seam_spacing = True
        self.enable_routing_workshop_order = True

    def disable_group(self, group: str) -> None:
        """특정 그룹 제약조건 비활성화."""
        if group.upper() == "P5":
            self.enable_p5_panel_constraints = False
        elif group.upper() == "P6":
            self.enable_p6_saw_constraints = False
        elif group.upper() == "P7":
            self.enable_p7_longi_constraints = False

    def enable_group(self, group: str) -> None:
        """특정 그룹 제약조건 활성화."""
        if group.upper() == "P5":
            self.enable_p5_panel_constraints = True
        elif group.upper() == "P6":
            self.enable_p6_saw_constraints = True
        elif group.upper() == "P7":
            self.enable_p7_longi_constraints = True

    def get_enabled_constraints(self) -> List[str]:
        """현재 활성화된 제약조건 목록."""
        enabled: List[str] = []
        all_constraints = [
            "P5#1", "P5#3", "P5#4", "P5#6", "P5#8", "P5#9", "P5#10",
            "P5#11", "P5#12", "P5#13", "P5#15", "P5#16", "P5#17",
            "P6#1", "P6#2", "P6#3", "P6#4",
            "P7#1", "P7#2", "P7#3", "P7#4", "P7#7", "P7#8",
            "P7#10", "P7#11", "P7#12",
            "CONSECUTIVE_3BAY",
            "CONSECUTIVE_B_BAY",
            "C_SEAM_SPACING",
            "ROUTING_C_SEAM_SPACING",
            "ROUTING_CURVED_SPACING",
            "ROUTING_HIGH_SEAM_SPACING",
            "ROUTING_WORKSHOP_ORDER",
            "LINE_GROUP_CONSTRAINT",
        ]
        for constraint_id in all_constraints:
            if self.is_constraint_enabled(constraint_id):
                enabled.append(constraint_id)
        return enabled

    def get_capacity_hyperparams(self) -> Dict[str, any]:
        """용량 관리 하이퍼파라미터 반환."""
        return {
            "weekday_seam_limit": self.weekday_seam_limit,
            "weekend_seam_limit": self.weekend_seam_limit,
            "holiday_eve_seam_reduction_enabled": self.holiday_eve_seam_reduction_enabled,
            "holiday_eve_seam_limit": self.holiday_eve_seam_limit,
            "hot_season_reduction_type": self.hot_season_reduction_type,
            "hot_season_seam_reduction": self.hot_season_seam_reduction,
            "hot_season_percentage_reduction": self.hot_season_percentage_reduction,
        }

    def get_summary(self) -> Dict[str, any]:
        """설정 상태 요약."""
        return {
            "groups": {
                "P5 (판계 작업)": self.enable_p5_panel_constraints,
                "P6 (SAW 공정)": self.enable_p6_saw_constraints,
                "P7 (론지 취부)": self.enable_p7_longi_constraints,
            },
            "enabled_count": len(self.get_enabled_constraints()),
            "strict_mode": self.enable_strict_mode,
            "debug_violations": self.enable_debug_violations,
            "capacity_hyperparams": {
                "weekday_seam_limit": self.weekday_seam_limit,
                "weekend_seam_limit": self.weekend_seam_limit,
                "holiday_eve_enabled": self.holiday_eve_seam_reduction_enabled,
                "holiday_eve_limit": self.holiday_eve_seam_limit,
                "hot_season_type": self.hot_season_reduction_type,
                "hot_season_reduction": (
                    self.hot_season_seam_reduction
                    if self.hot_season_reduction_type == "absolute"
                    else self.hot_season_percentage_reduction
                ),
            },
            "assembly_expansion_config": {
                "startday_mode": self.assembly_expansion_startday_mode,
                "expansion_days": self.assembly_expansion_days,
                "mode_description": (
                    "당일만 시작" if self.assembly_expansion_startday_mode == 2 else "처음부터 확장"
                ),
                "expansion_pattern": f"{self.assembly_expansion_days}일씩 확장",
            },
        }
