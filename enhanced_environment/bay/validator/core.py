# [AGENT-ADD] Core ConstraintValidator orchestration split from constraint_validator.py.

from datetime import datetime
from typing import List, Optional

from enhanced_environment.models import EnhancedBlock, BayType, ConstraintViolation
from enhanced_environment.constraints.managers import CapacityTracker, CalendarManager, BayStateTracker, PSBlockManager


class ConstraintValidatorCore:
    def __init__(
        self,
        capacity_tracker: CapacityTracker,
        calendar_manager: CalendarManager,
        bay_tracker: BayStateTracker,
        ps_manager: PSBlockManager,
        subassembly_complete_info: dict,
        cross_seam_mixing_control: dict,
        completed_steps: list,
        blocks_dict: dict,
    ):
        self.capacity_tracker = capacity_tracker
        self.calendar_manager = calendar_manager
        self.bay_tracker = bay_tracker
        self.ps_manager = ps_manager
        self.subassembly_complete_info = subassembly_complete_info
        self.cross_seam_mixing_control = cross_seam_mixing_control
        self.completed_steps = completed_steps
        self.blocks_dict = blocks_dict
        self.env = None

    def set_environment(self, env):
        """환경 참조 주입"""
        self.env = env

    def _is_subassembly(self, block_id: int) -> bool:
        """블록이 별판인지 확인 (메타데이터 기반)"""
        return block_id in self.subassembly_complete_info

    def validate_all_constraints_realtime(
        self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime
    ) -> List[ConstraintViolation]:
        """
        모든 판계 제약조건 실시간 검증 (완전한 모든 제약조건 포함)

        Args:
            block: 처리 중인 블록
            assigned_bay: 할당된 베이
            current_time: 현재 시간

        Returns:
            모든 제약조건 위반 리스트
        """
        all_violations: List[ConstraintViolation] = []

        # ✅ SAW 제약조건 실시간 검증 (최우선)
        saw_violations = self.validate_saw_constraints_realtime(block, current_time)
        all_violations.extend(saw_violations)

        # ✅ P5#3,4: P/S 순서 제약 검증 (실시간 추가)
        ps_order_violations = self.validate_ps_order_realtime(block)
        all_violations.extend(ps_order_violations)

        # ✅ P5#8,9,10,15,16: 용량 관리 제약 검증 (하이퍼파라미터 기반)
        is_weekend = self.calendar_manager.is_weekend(current_time)
        is_hot_season = self.calendar_manager.is_hot_season(current_time)
        is_holiday_eve = self.calendar_manager.is_holiday_eve(current_time)
        capacity_violations = self.capacity_tracker.add_block(
            block, is_weekend, is_hot_season, is_holiday_eve, current_time=current_time
        )
        all_violations.extend(capacity_violations)

        # ✅ P5#11,12: 혼합 배정 제약 검증 (실시간 추가)
        mixing_violations = self.validate_mixed_assembly_realtime(block, current_time)
        all_violations.extend(mixing_violations)

        # [AGENT-ADD] LINE_GROUP_CONSTRAINT는 runtime 마스킹과 독립적으로 final/audit에서도 반드시 다시 센다.
        line_group_violations = self.validate_line_group_realtime(block)
        all_violations.extend(line_group_violations)

        # ✅ P5#13: 자재 미입고 제약 검증 (실시간 추가)
        material_violations = self.validate_material_ready_realtime(block)
        all_violations.extend(material_violations)

        # ✅ P5#15,16: 달력 제약 검증
        calendar_violations = self.capacity_tracker.check_calendar_constraints(
            block, current_time, self.calendar_manager
        )
        all_violations.extend(calendar_violations)

        # ✅ P5#17: 별판 처리 검증
        subassembly_violations = self.validate_subassembly_constraints(block)
        all_violations.extend(subassembly_violations)

        # ✅ P6#4: Cross seam 블록 혼합 배치 제약 검증 (환경에서도 재검증)
        cross_seam_violations = self.validate_cross_seam_realtime(block)
        all_violations.extend(cross_seam_violations)

        # ✅ Routing/간격 제약 (C-Seam, 곡판, 고심수, 작업장 순서)
        routing_violations = self._validate_routing_spacing_constraints(block)
        all_violations.extend(routing_violations)

        # ✅ P7 제약조건: 기존 BayStateTracker 함수 활용
        p7_violations = self.validate_p7_constraints_with_existing_functions(
            block, assigned_bay
        )
        all_violations.extend(p7_violations)

        return all_violations

    def validate_all_constraints_realtime_action(
        self,
        block: EnhancedBlock,
        assigned_bay: BayType,
        current_time: datetime,
        actual_machine_2_start_time: datetime = None,
        capacity_time_override: Optional[datetime] = None,
        current_in_history: bool = False,
    ) -> List[ConstraintViolation]:
        """
        Action Masking용 모든 제약조건 실시간 검증 (P7#7 중복 해결)

        기존 _validate_all_constraints_realtime()과 동일하지만 P7#7 중복 문제 해결

        Args:
            actual_machine_2_start_time: 실제 머신 2번 시작 시간 (이미 계산된 경우)
        """
        violations: List[ConstraintViolation] = []

        # ✅ SAW 제약조건 실시간 검증 (최우선) - 실제 머신 2번 시작 시간 전달
        saw_violations = self.validate_saw_constraints_realtime(
            block, current_time, actual_machine_2_start_time
        )
        violations.extend(saw_violations)

        # [AGENT-EDIT] runtime canonical 검사에서는 P/S 순서도 실시간으로 계속 검수한다.
        ps_order_violations = self.validate_ps_order_realtime(block)
        violations.extend(ps_order_violations)


        # ✅ P5#8,9,10,16: 용량 관리 제약 검증 (추가!)
        # [AGENT-EDIT] 계획일 기준으로 용량/혹서기 판단을 통일하기 위해 시간 오버라이드를 지원
        capacity_time = (
            capacity_time_override
            if capacity_time_override is not None
            else current_time
        )
        is_weekend = self.calendar_manager.is_weekend(capacity_time)
        is_hot_season = self.calendar_manager.is_hot_season(capacity_time)
        is_holiday_eve = self.calendar_manager.is_holiday_eve(capacity_time)
        capacity_violations = self.capacity_tracker.add_block(
            block, is_weekend, is_hot_season, is_holiday_eve, current_time=capacity_time
        )
        violations.extend(capacity_violations)

        # ✅ P5#11,12: 혼합 배정 제약 검증 (유지)
        mixing_violations = self.validate_mixing_constraints_realtime(block)
        violations.extend(mixing_violations)

        # [AGENT-ADD] LINE_GROUP_CONSTRAINT canonical audit/runtime counting
        line_group_violations = self.validate_line_group_realtime(block)
        violations.extend(line_group_violations)

        # ✅ P5#13: 자재 미입고 제약 검증 (유지)
        material_violations = self.validate_material_constraints_realtime(block)
        violations.extend(material_violations)

        # [AGENT-EDIT] P5#15,16 달력 제약은 add_block에서 이미 체크됨
        # 중복 검증으로 인해 혹서기(P5#16) ERROR가 과대 발생하는 이슈가 있어 action 경로에서는 생략

        # ✅ P5#17: 별판 처리 검증 (추가!) - 이것 때문에 별판 INFO가 안 나왔음!
        subassembly_violations = self.validate_subassembly_constraints(block)
        violations.extend(subassembly_violations)

        # ✅ P6#4: Cross seam 블록 혼합 배치 제약 검증 (추가!) - 이것 때문에 Cross seam INFO가 안 나왔음!
        cross_seam_violations = self.validate_cross_seam_realtime(block)
        violations.extend(cross_seam_violations)

        # ✅ Routing/간격 제약 (C-Seam, 곡판, 고심수, 작업장 순서)
        routing_violations = self._validate_routing_spacing_constraints(block)
        violations.extend(routing_violations)

        # ✅ P7#7: 연속 배치 제약 검증 (Action Masking용 - 카운트 증가 없이)
        consecutive_violations = self.validate_consecutive_constraints_realtime_action(
            block, assigned_bay, current_in_history=current_in_history
        )
        violations.extend(consecutive_violations)

        # ✅ P7#1: 부하 균형 제약 (유지)
        balance_violations = self.validate_balance_constraints_realtime(
            block, assigned_bay
        )
        violations.extend(balance_violations)

        return violations
