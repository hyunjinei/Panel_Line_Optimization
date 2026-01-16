# [AGENT-ADD] Assembly/mixing/material/P-S validation split from constraint_validator.py.

from datetime import datetime
from typing import List

from enhanced_environment.models import (
    EnhancedBlock,
    ConstraintViolation,
    PortStarboard,
)


class AssemblyValidationMixin:
    def validate_cross_seam_realtime(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        """
        P6#4: Cross seam 블록 혼합 배치 제약 실시간 검증 (환경에서 재검증)

        Args:
            block: 처리 중인 블록

        Returns:
            Cross seam 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # Cross seam 블록인지 확인
        cross_seam_blocks = set(self.cross_seam_mixing_control.get("cross_seam_blocks", []))
        is_special_block = (
            block.block_id in cross_seam_blocks
            or block.is_cross_seam
            or block.is_draft
            or (block.main_plate_count > 10)
        )

        if is_special_block:
            # 완료된 단계들에서 연속 special 블록 개수 확인
            recent_special_count = 0
            max_consecutive_special = self.cross_seam_mixing_control.get(
                "max_consecutive_special", 2
            )

            # 최근 완료된 블록들 확인 (역순)
            for step in reversed(self.completed_steps[-max_consecutive_special:]):
                step_block_id = step.block_id
                if step_block_id in self.blocks_dict:
                    step_block = self.blocks_dict[step_block_id]
                    step_is_special = (
                        step_block_id in cross_seam_blocks
                        or step_block.is_cross_seam
                        or step_block.is_draft
                        or (step_block.main_plate_count > 10)
                    )

                    if step_is_special:
                        recent_special_count += 1
                    else:
                        break

            # 현재 블록을 포함해서 연속 제한 확인
            if recent_special_count >= max_consecutive_special:
                violations.append(
                    ConstraintViolation(
                        constraint_id="P6#4",
                        message=(
                            "Cross seam 혼합 배치 위반: 특수 블록 "
                            f"{recent_special_count + 1}개 연속 (최대 {max_consecutive_special}개)"
                        ),
                        severity="WARNING",  # 경고만 하고 계속 진행
                        block_id=block.block_id,
                    )
                )

                # 특수 블록 유형별 세부 정보 추가
                special_types = []
                if block.is_cross_seam:
                    special_types.append("Cross seam")
                if block.is_draft:
                    special_types.append("Draft")
                if block.main_plate_count > 10:
                    special_types.append(f"주판{block.main_plate_count}장")

                violations.append(
                    ConstraintViolation(
                        constraint_id="P6#4_DETAIL",
                        message=f"특수 블록 연속 배치: {', '.join(special_types)} - {block.block_id}",
                        severity="INFO",
                        block_id=block.block_id,
                    )
                )

        return violations

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

        # 별판인지 확인
        if self._is_subassembly(block.block_id):
            subassembly_data = self.subassembly_complete_info[block.block_id]

            # 별판 정보 기록 (위반은 아니지만 추적용)
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

        Args:
            block: 처리 중인 블록

        Returns:
            P/S 순서 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # P/S 쌍이 아니면 검증하지 않음
        if not block.is_p_s_pair():
            return violations

        # S 블록인 경우, P 블록이 먼저 완료되었는지 확인
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

    def validate_mixed_assembly_realtime(
        self, block: EnhancedBlock, current_time: datetime
    ) -> List[ConstraintViolation]:
        """
        P5#11,12: 혼합 배정 제약 실시간 검증

        Args:
            block: 처리 중인 블록
            current_time: 현재 시간

        Returns:
            혼합 배정 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # ps_manager의 기존 함수 활용
        mixing_violations = self.ps_manager._check_assembly_mixing_constraints(
            block, current_time
        )
        violations.extend(mixing_violations)

        return violations

    def validate_material_ready_realtime(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        """
        P5#13: 자재 미입고 제약 실시간 검증

        Args:
            block: 처리 중인 블록

        Returns:
            자재 미입고 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # 블록에 material_ready 속성이 있는지 확인
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
        """P5#11,12: 혼합 배정 제약 실시간 검증 (단순화)"""
        violations: List[ConstraintViolation] = []
        # [AGENT-EDIT] config 기반 동적 토글 (enabled_constraints 반영)
        try:
            if self.env and self.env.constraint_config:
                if not self.env.constraint_config.is_constraint_enabled("P5#11"):
                    return violations
        except Exception:
            if (
                self.env
                and self.env.constraint_config
                and not self.env.constraint_config.enable_p5_11_fixed_line_mixing
            ):
                return violations
        # 실제 혼합 배정 제약은 Action Masking 단계에서 이미 처리됨
        # 여기서는 기록용으로만 사용
        return violations

    def validate_material_constraints_realtime(
        self, block: EnhancedBlock
    ) -> List[ConstraintViolation]:
        """P5#13: 자재 미입고 제약 실시간 검증"""
        violations: List[ConstraintViolation] = []
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
        """
        P/S 순서 검증 (모든 순서 제약조건 포함)

        Args:
            sequence: 블록 ID 순서 리스트

        Returns:
            위반된 제약조건 리스트
        """
        violations: List[ConstraintViolation] = []

        # P5#3,4: P/S 순서 검증
        for i, block_id in enumerate(sequence):
            if block_id not in self.blocks_dict:
                continue

            block = self.blocks_dict[block_id]

            # S 블록인 경우, 같은 쌍의 P 블록이 앞에 있는지 확인
            if block.port_starboard == PortStarboard.STARBOARD and block.pair_block_id:
                pair_p_id = block.pair_block_id

                # P 블록이 현재 S 블록보다 뒤에 있는지 확인
                try:
                    p_index = sequence.index(pair_p_id)
                    if p_index >= i:  # P가 S보다 뒤에 있음
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
                    # P 블록이 순서에 없음
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

        # P5#11,12: 혼합 배정 제약 검증
        mixed_violations = self.validate_mixed_assembly_order(sequence)
        violations.extend(mixed_violations)

        # P6#4: Cross seam 혼합 배치 제약 검증
        cross_seam_violations = self.validate_cross_seam_placement_order(sequence)
        violations.extend(cross_seam_violations)

        return violations

    def validate_mixed_assembly_order(self, sequence: List[int]) -> List[ConstraintViolation]:
        """
        P5#11,12: 혼합 배정 순서 검증 (원본 엄격한 버전으로 복원)

        Args:
            sequence: 블록 순서

        Returns:
            위반된 제약조건 리스트
        """
        violations: List[ConstraintViolation] = []

        if len(sequence) <= 1:
            return violations

        last_assembly_type = None
        consecutive_same_type = 0
        max_consecutive_limit = 1  # 3 → 1으로 변경 (강제 교체)

        for block_id in sequence:
            if block_id not in self.blocks_dict:
                continue

            block = self.blocks_dict[block_id]
            current_assembly_type = block.assembly_type

            if last_assembly_type is None:
                last_assembly_type = current_assembly_type
                consecutive_same_type = 1
                continue

            if current_assembly_type == last_assembly_type:
                consecutive_same_type += 1

                if consecutive_same_type > max_consecutive_limit:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P5#11,12",
                            message=(
                                f"혼합 배정 위반: {current_assembly_type.value} 타입 "
                                f"{consecutive_same_type}개 연속 (최대 {max_consecutive_limit}개)"
                            ),
                            block_id=block.block_id,
                            severity="ERROR",
                        )
                    )

                consecutive_same_type = 1
                last_assembly_type = current_assembly_type

        return violations

    def validate_cross_seam_placement_order(
        self, sequence: List[int]
    ) -> List[ConstraintViolation]:
        """
        P6#4: Cross seam 혼합 배치 순서 검증 (원본 엄격한 버전으로 복원)

        Args:
            sequence: 블록 순서

        Returns:
            위반된 제약조건 리스트
        """
        violations: List[ConstraintViolation] = []

        if len(sequence) <= 2:  # 3 → 2로 복원
            return violations

        cross_seam_consecutive = 0
        max_consecutive = 3  # 4 → 3으로 복원

        for block_id in sequence:
            if block_id not in self.blocks_dict:
                continue

            block = self.blocks_dict[block_id]

            if block.is_cross_seam or block.is_draft or (block.main_plate_count > 10):
                cross_seam_consecutive += 1

                if cross_seam_consecutive >= max_consecutive:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#4",
                            message=(
                                "Cross seam 혼합 배치 위반: 특수 블록 "
                                f"{cross_seam_consecutive}개 연속 (최대 {max_consecutive - 1}개)"
                            ),
                            severity="ERROR",  # WARNING → ERROR로 변경
                            block_id=block_id,
                        )
                    )
            else:
                cross_seam_consecutive = 0

        return violations
