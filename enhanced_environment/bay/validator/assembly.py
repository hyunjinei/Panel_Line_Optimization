# [AGENT-ADD] Assembly/mixing/material/P-S validation split from constraint_validator.py.

from datetime import datetime
from typing import List

from enhanced_environment.models import (
    EnhancedBlock,
    ConstraintViolation,
    PortStarboard,
    AssemblyType,
)



class AssemblyValidationMixin:
    # [AGENT-ADD] runtime/post-hoc 정합성을 위해 prefix 기반 canonical 검사 helper를 공유한다.

    def _get_completed_history_block_ids(self) -> List[int]:
        return [
            int(step.block_id)
            for step in getattr(self, "completed_steps", [])
            if int(getattr(step, "block_id", -1)) in getattr(self, "blocks_dict", {})
        ]

    def _evaluate_next_cross_seam_violation(self, current_block: EnhancedBlock, history_block_ids: List[int]) -> List[ConstraintViolation]:
        violations: List[ConstraintViolation] = []
        if not getattr(current_block, "is_cross_seam", False):
            return violations

        cross_seam_consecutive = 0
        max_consecutive = 3
        blocks_dict = getattr(self, "blocks_dict", {})
        for block_id in history_block_ids:
            block = blocks_dict.get(int(block_id))
            if block is None:
                continue
            if getattr(block, "is_cross_seam", False):
                cross_seam_consecutive += 1
            else:
                cross_seam_consecutive = 0

        cross_seam_consecutive += 1
        if cross_seam_consecutive >= max_consecutive:
            violations.append(
                ConstraintViolation(
                    constraint_id="P6#4",
                    message=(
                        "Cross seam 혼합 배치 위반: 특수 블록 "
                        f"{cross_seam_consecutive}개 연속 (최대 {max_consecutive - 1}개)"
                    ),
                    severity="ERROR",
                    block_id=current_block.block_id,
                )
            )
        return violations

    # ==== [AGENT-ADD BEGIN: line-group canonical audit] ====
    def _resolve_line_group_value(self, block: EnhancedBlock) -> str | None:
        line_group = getattr(block, "line_group", None)
        if not line_group or not str(line_group).startswith("L"):
            from enhanced_environment.common.utils_core import get_line_group_and_workshop_code

            line_group, _ = get_line_group_and_workshop_code(block)
        if line_group:
            return str(line_group)
        return None

    def _evaluate_next_line_group_violation(
        self, current_block: EnhancedBlock, history_block_ids: List[int]
    ) -> List[ConstraintViolation]:
        """[AGENT-ADD] runtime 마스킹과 동일한 prefix 기준으로 LINE_GROUP_CONSTRAINT를 재검증한다."""
        violations: List[ConstraintViolation] = []

        try:
            if not self._is_enabled("LINE_GROUP_CONSTRAINT"):
                return violations
        except Exception:
            return violations

        if getattr(current_block, "assembly_type", None) != AssemblyType.LINE:
            return violations

        try:
            constraint_config = getattr(self, "constraint_config", None)
            if constraint_config is None and getattr(self, "env", None) is not None:
                constraint_config = getattr(self.env, "constraint_config", None)
            limit = (
                constraint_config.line_group_soft_limit
                if getattr(constraint_config, "line_group_use_soft_limit", False)
                else getattr(constraint_config, "line_group_strict_limit", 0)
            )
        except Exception:
            limit = 0

        if limit <= 0:
            return violations

        line_group = self._resolve_line_group_value(current_block)
        if not line_group or not line_group.startswith("L"):
            return violations

        blocks_dict = getattr(self, "blocks_dict", {}) or {}
        consecutive = 0
        for block_id in reversed(history_block_ids):
            prev_block = blocks_dict.get(int(block_id))
            if prev_block is None:
                continue
            prev_group = self._resolve_line_group_value(prev_block)
            if prev_group != line_group:
                break
            consecutive += 1

        if consecutive >= limit:
            violations.append(
                ConstraintViolation(
                    constraint_id="LINE_GROUP_CONSTRAINT",
                    message=(
                        f"라인 그룹 연속 제한 위반: {line_group} "
                        f"{consecutive + 1}판 연속 → 최대 {limit}판"
                    ),
                    severity="ERROR",
                    block_id=current_block.block_id,
                )
            )
        return violations

    def validate_line_group_realtime(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        """[AGENT-ADD] LINE_GROUP_CONSTRAINT를 현재 블록 1건 기준으로 실시간 검증한다."""
        history_block_ids = self._get_completed_history_block_ids()
        return self._evaluate_next_line_group_violation(block, history_block_ids)
    # ==== [AGENT-ADD END: line-group canonical audit] ====

    def validate_cross_seam_realtime(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        """
        [AGENT-EDIT] P6#4를 prefix 기반 canonical 규칙으로 실시간 검증한다.
        사후 validate_cross_seam_placement_order와 같은 기준을 현재 블록 1건에만 적용한다.
        """
        history_block_ids = self._get_completed_history_block_ids()
        return self._evaluate_next_cross_seam_violation(block, history_block_ids)

    def validate_subassembly_constraints(
        self, block: EnhancedBlock
    ) -> List[ConstraintViolation]:
        """
        P5#17: 별판 처리 제약 검증

        Args:
            block: 처리 중인 블록

        Returns:
            별판 관련 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # [AGENT-EDIT] runtime on/off와 final audit counting을 동일 기준으로 맞춘다.
        if not self._is_enabled("P5#17"):
            return violations

        if self._is_subassembly(block.block_id):
            subassembly_data = self.subassembly_complete_info[block.block_id]
            violations.append(
                ConstraintViolation(
                    constraint_id="P5#17",
                    message=(
                        "별판 처리: "
                        f"{len(subassembly_data['original_block_ids'])}개 블록을 1개 통합 블록으로 처리"
                    ),
                    severity="INFO",
                    block_id=block.block_id,
                )
            )

        return violations

    def validate_ps_order_realtime(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        """
        P5#3,4: P/S 순서 제약 실시간 검증
        """
        violations: List[ConstraintViolation] = []
        if not (self._is_enabled("P5#3") or self._is_enabled("P5#4")):
            return violations
        if not block.is_p_s_pair():
            return violations
        if block.port_starboard == PortStarboard.STARBOARD:
            pair_p_id = block.pair_block_id
            if pair_p_id and pair_p_id not in self.ps_manager.completed_blocks:
                violations.append(
                    ConstraintViolation(
                        constraint_id="P5#3,4",
                        message=(
                            f"P/S 연속성 위반: S 블록({block.block_id})보다 "
                            f"P 블록({pair_p_id})이 먼저 완료되어야 함"
                        ),
                        severity="ERROR",
                        block_id=block.block_id,
                    )
                )
        return violations

    def _evaluate_next_mixed_assembly_violation(self, current_block: EnhancedBlock, history_block_ids: List[int]) -> List[ConstraintViolation]:
        violations: List[ConstraintViolation] = []
        if not (self._is_enabled("P5#11") or self._is_enabled("P5#12")):
            return violations
        blocks_dict = getattr(self, "blocks_dict", {})
        sequence_blocks: List[EnhancedBlock] = []
        for block_id in history_block_ids:
            hist_block = blocks_dict.get(int(block_id))
            if hist_block is not None:
                sequence_blocks.append(hist_block)
        sequence_blocks.append(current_block)

        if len(sequence_blocks) <= 1:
            return violations

        last_assembly_type = None
        consecutive_same_type = 0
        max_consecutive_limit = 1

        for seq_block in sequence_blocks:
            current_assembly_type = seq_block.assembly_type
            if last_assembly_type is None:
                last_assembly_type = current_assembly_type
                consecutive_same_type = 1
                continue
            if current_assembly_type == last_assembly_type:
                consecutive_same_type += 1
                if consecutive_same_type > max_consecutive_limit and seq_block.block_id == current_block.block_id:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P5#11,12",
                            message=(
                                f"혼합 배정 위반: {current_assembly_type.value} 타입 "
                                f"{consecutive_same_type}개 연속 (최대 {max_consecutive_limit}개)"
                            ),
                            block_id=current_block.block_id,
                            severity="ERROR",
                        )
                    )
                consecutive_same_type = 1
                last_assembly_type = current_assembly_type
            else:
                consecutive_same_type = 1
                last_assembly_type = current_assembly_type
        return violations

    def validate_mixed_assembly_realtime(
        self, block: EnhancedBlock, current_time: datetime
    ) -> List[ConstraintViolation]:
        """
        [AGENT-EDIT] P5#11,12를 prefix 기반 canonical 규칙으로 실시간 검증한다.
        사후 validate_mixed_assembly_order와 같은 기준을 현재 블록 1건에만 적용한다.
        """
        _ = current_time
        history_block_ids = self._get_completed_history_block_ids()
        return self._evaluate_next_mixed_assembly_violation(block, history_block_ids)

    def validate_material_ready_realtime(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        violations: List[ConstraintViolation] = []
        if not self._is_enabled("P5#13"):
            return violations
        if hasattr(block, "material_ready") and not block.material_ready:
            violations.append(
                ConstraintViolation(
                    constraint_id="P5#13",
                    message=f"자재 미입고: 블록 {block.block_id}의 자재가 아직 입고되지 않음",
                    severity="ERROR",
                    block_id=block.block_id,
                )
            )
        return violations

    def validate_mixing_constraints_realtime(
        self, block: EnhancedBlock
    ) -> List[ConstraintViolation]:
        """[AGENT-EDIT] action/runtime 경로도 P5#11,12를 실제로 계산한다."""
        try:
            if self.env and self.env.constraint_config and not (
                self.env.constraint_config.is_constraint_enabled("P5#11")
                or self.env.constraint_config.is_constraint_enabled("P5#12")
            ):
                return []
        except Exception:
            pass
        return self.validate_mixed_assembly_realtime(block, getattr(self.env, "current_time", datetime.now()))

    def validate_material_constraints_realtime(
        self, block: EnhancedBlock
    ) -> List[ConstraintViolation]:
        violations: List[ConstraintViolation] = []
        if not self._is_enabled("P5#13"):
            return violations
        if not block.material_ready:
            violations.append(
                ConstraintViolation(
                    constraint_id="P5#13",
                    message="자재 미입고 상태",
                    severity="ERROR",
                    block_id=block.block_id,
                )
            )
        return violations

    def validate_ps_sequence_order(self, sequence: List[int]) -> List[ConstraintViolation]:
        violations: List[ConstraintViolation] = []
        for i, block_id in enumerate(sequence):
            if block_id not in self.blocks_dict:
                continue
            block = self.blocks_dict[block_id]
            if block.port_starboard == PortStarboard.STARBOARD and block.pair_block_id:
                pair_p_id = block.pair_block_id
                try:
                    p_index = sequence.index(pair_p_id)
                    if p_index >= i:
                        violations.append(
                            ConstraintViolation(
                                constraint_id="P5#3,4",
                                message=(
                                    f"P/S 순서 위반: P{pair_p_id}(위치{p_index}) "
                                    f"→ S{block_id}(위치{i})"
                                ),
                                severity="ERROR",
                                block_id=block.block_id,
                            )
                        )
                except ValueError:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P5#3,4",
                            message=(
                                f"P/S 쌍 누락: S{block_id}의 P{pair_p_id}가 순서에 없음"
                            ),
                            severity="ERROR",
                            block_id=block.block_id,
                        )
                    )
        violations.extend(self.validate_mixed_assembly_order(sequence))
        violations.extend(self.validate_cross_seam_placement_order(sequence))
        violations.extend(self.validate_line_group_order(sequence))
        return violations

    def validate_mixed_assembly_order(self, sequence: List[int]) -> List[ConstraintViolation]:
        """[AGENT-EDIT] runtime과 동일한 prefix helper로 P5#11,12를 사후 검증한다."""
        violations: List[ConstraintViolation] = []
        history_block_ids: List[int] = []
        for block_id in sequence:
            if block_id not in self.blocks_dict:
                continue
            block = self.blocks_dict[block_id]
            violations.extend(self._evaluate_next_mixed_assembly_violation(block, history_block_ids))
            history_block_ids.append(block_id)
        return violations

    def validate_line_group_order(self, sequence: List[int]) -> List[ConstraintViolation]:
        """[AGENT-ADD] runtime과 동일한 prefix helper로 LINE_GROUP_CONSTRAINT를 사후 검증한다."""
        violations: List[ConstraintViolation] = []
        history_block_ids: List[int] = []
        for block_id in sequence:
            if block_id not in self.blocks_dict:
                continue
            block = self.blocks_dict[block_id]
            violations.extend(self._evaluate_next_line_group_violation(block, history_block_ids))
            history_block_ids.append(block_id)
        return violations

    def validate_cross_seam_placement_order(
        self, sequence: List[int]
    ) -> List[ConstraintViolation]:
        """[AGENT-EDIT] runtime과 동일한 prefix helper로 P6#4를 사후 검증한다."""
        violations: List[ConstraintViolation] = []
        history_block_ids: List[int] = []
        for block_id in sequence:
            if block_id not in self.blocks_dict:
                continue
            block = self.blocks_dict[block_id]
            violations.extend(self._evaluate_next_cross_seam_violation(block, history_block_ids))
            history_block_ids.append(block_id)
        return violations
