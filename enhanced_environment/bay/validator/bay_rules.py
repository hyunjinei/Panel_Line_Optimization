# [AGENT-ADD] Bay/P7 constraint validation split from constraint_validator.py.

from typing import List

from enhanced_environment.models import EnhancedBlock, BayType, ConstraintViolation


class BayValidationMixin:
    def validate_p7_constraints_with_existing_functions(
        self, block: EnhancedBlock, assigned_bay: BayType
    ) -> List[ConstraintViolation]:
        """
        기존 BayStateTracker 함수들을 활용한 P7 제약조건 검증

        ✅ 수정: 엑셀 기반 스케줄링을 위한 P7#7 연속성 체크 복원
        - Action Masking: _auto_assign_bay()에서 사전 차단되므로 연속성 위반 없음
        - 엑셀 기반: 환경 개입 없이 원본 베이 사용하므로 사후 검증 필요

        Args:
            block: 처리 중인 블록
            assigned_bay: 할당된 베이

        Returns:
            P7 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # ✅ P7#2: 물리적 제약 (21m 초과 → B베이 권장)
        if block.width > 21.0 and assigned_bay == BayType.BAY_35A:
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#2",
                    message=f"폭 {block.width:.1f}m > 21m인데 A베이 할당됨 (B베이 필수)",
                    severity="ERROR",
                    block_id=block.block_id,
                )
            )

        # ✅ P7#10: 론지 30개 이상 → A베이 우선
        if block.longi_count >= 30 and assigned_bay == BayType.BAY_36B:
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#10",
                    message=f"론지 {block.longi_count}개 ≥ 30개인데 B베이 할당됨 (A베이 권장)",
                    severity="WARNING",
                    block_id=block.block_id,
                )
            )

        # ✅ P7#12: LT강재 → A베이 우선
        if (
            hasattr(block, "material_type")
            and block.material_type.value == "LT"
            and assigned_bay == BayType.BAY_36B
        ):
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#12",
                    message="LT강재인데 B베이 할당됨 (A베이 권장)",
                    severity="WARNING",
                    block_id=block.block_id,
                )
            )

        # ✅ P7#7: 연속성 체크 복원 (엑셀 기반 스케줄링용)
        # BayStateTracker의 현재 상태(현재 블록 제외)를 기반으로 안전하게 연속성 확인
        try:
            total_assigned = (
                self.bay_tracker.bay_35a_block_count
                + self.bay_tracker.bay_36b_block_count
            )
            if total_assigned > 0:
                consecutive_count = (
                    self.bay_tracker._get_consecutive_count_without_current_block(
                        assigned_bay
                    )
                )

                # A베이: 이미 1개 있으면 다음 A는 위반 (1개까지만 연속 가능)
                if assigned_bay == BayType.BAY_35A and consecutive_count >= 1:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P7#7",
                            message="P7#7 위반: A베이 2개 연속 배치 불가 (현재 블록 제외 카운트)",
                            severity="WARNING",
                            block_id=block.block_id,
                        )
                    )

                # B베이: 이미 2개 있으면 다음 B는 위반 (2개까지 연속 가능)
                if assigned_bay == BayType.BAY_36B and consecutive_count >= 2:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P7#7",
                            message="P7#7 위반: B베이 3개 연속 배치 불가 (현재 블록 제외 카운트)",
                            severity="WARNING",
                            block_id=block.block_id,
                        )
                    )
        except Exception:
            # 연속성 체크 실패 시 무시 (Action Masking에서는 정상)
            pass

        # ✅ P7#1: 부하 균형 제약 (정보성 기록)
        current_balance_score = self.bay_tracker.get_load_balance_score()
        if current_balance_score < 0.7:  # 균형 점수가 70% 미만이면 경고
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#1",
                    message=(
                        "부하 균형 불량: 균형 점수 "
                        f"{current_balance_score:.2f} < 0.7 "
                        f"(35A: {self.bay_tracker.bay_35a_worktime/3600:.1f}h, "
                        f"36B: {self.bay_tracker.bay_36b_worktime/3600:.1f}h)"
                    ),
                    severity="INFO",  # WARNING → INFO 변경 (정보성만, 제약조건 위반 아님)
                    block_id=block.block_id,
                )
            )

        return violations

    def validate_p7_constraints_action_masking(
        self, block: EnhancedBlock, assigned_bay: BayType
    ) -> List[ConstraintViolation]:
        """
        Action Masking용 P7 제약조건 검증 (P7#7 중복 해결)

        ✅ 핵심: 현재 블록을 제외하고 연속성 계산하여 가짜 위반 방지

        Args:
            block: 처리 중인 블록
            assigned_bay: 할당된 베이

        Returns:
            P7 제약조건 위반 리스트 (중복 해결됨)
        """
        violations: List[ConstraintViolation] = []

        # ✅ P7#2: 물리적 제약 (21m 초과 → B베이 권장)
        if block.width > 21.0 and assigned_bay == BayType.BAY_35A:
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#2",
                    message=f"폭 {block.width:.1f}m > 21m인데 A베이 할당됨 (B베이 필수)",
                    severity="ERROR",
                    block_id=block.block_id,
                )
            )

        # ✅ P7#10: 론지 30개 이상 → A베이 우선
        if block.longi_count >= 30 and assigned_bay == BayType.BAY_36B:
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#10",
                    message=f"론지 {block.longi_count}개 ≥ 30개인데 B베이 할당됨 (A베이 권장)",
                    severity="WARNING",
                    block_id=block.block_id,
                )
            )

        # ✅ P7#12: LT강재 → A베이 우선
        if (
            hasattr(block, "material_type")
            and block.material_type.value == "LT"
            and assigned_bay == BayType.BAY_36B
        ):
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#12",
                    message="LT강재인데 B베이 할당됨 (A베이 권장)",
                    severity="WARNING",
                    block_id=block.block_id,
                )
            )

        # ✅ P7#7: 연속성 체크 (Action Masking용 - 현재 블록 제외)
        try:
            consecutive_count = (
                self.bay_tracker._get_consecutive_count_without_current_block(
                    assigned_bay
                )
            )

            # A베이: 이미 1개 있으면 다음 A는 위반 (1개까지만 연속 가능)
            if assigned_bay == BayType.BAY_35A and consecutive_count >= 1:
                violations.append(
                    ConstraintViolation(
                        constraint_id="P7#7",
                        message="P7#7 위반: A베이 2개 연속 배치 불가 (현재 블록 제외 카운트)",
                        severity="WARNING",
                        block_id=block.block_id,
                    )
                )

            # B베이: 이미 2개 있으면 다음 B는 위반 (2개까지 연속 가능)
            if assigned_bay == BayType.BAY_36B and consecutive_count >= 2:
                violations.append(
                    ConstraintViolation(
                        constraint_id="P7#7",
                        message="P7#7 위반: B베이 3개 연속 배치 불가 (현재 블록 제외 카운트)",
                        severity="WARNING",
                        block_id=block.block_id,
                    )
                )
        except Exception:
            # 연속성 체크 실패 시 무시
            pass

        # ✅ P7#1: 부하 균형 제약 (정보성 기록)
        current_balance_score = self.bay_tracker.get_load_balance_score()
        if current_balance_score < 0.7:  # 균형 점수가 70% 미만이면 경고
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#1",
                    message=f"부하 균형 불량: 균형 점수 {current_balance_score:.2f} < 0.7",
                    severity="INFO",
                    block_id=block.block_id,
                )
            )

        return violations

    def validate_consecutive_constraints_realtime_action(
        self, block: EnhancedBlock, assigned_bay: BayType
    ) -> List[ConstraintViolation]:
        """P7#7: 연속 배치 제약 검증 (Action Masking용 - 중복 카운팅 방지)"""
        violations: List[ConstraintViolation] = []

        # Action Masking용: 현재 블록을 제외하고 연속 카운트 계산
        consecutive_count = self.bay_tracker._get_consecutive_count_without_current_block(
            assigned_bay
        )

        # A베이: 1개까지만 연속 가능
        if assigned_bay == BayType.BAY_35A and consecutive_count >= 1:
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#7",
                    message=f"A베이 {consecutive_count + 1}개 연속 배치 불가 (현재 블록 제외 카운트)",
                    severity="WARNING",
                    block_id=block.block_id,
                )
            )

        # B베이: 2개까지만 연속 가능
        elif assigned_bay == BayType.BAY_36B and consecutive_count >= 2:
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#7",
                    message=f"B베이 {consecutive_count + 1}개 연속 배치 불가 (현재 블록 제외 카운트)",
                    severity="WARNING",
                    block_id=block.block_id,
                )
            )

        return violations

    def validate_balance_constraints_realtime(
        self, block: EnhancedBlock, assigned_bay: BayType
    ) -> List[ConstraintViolation]:
        """P7#1: 부하 균형 제약 실시간 검증"""
        violations: List[ConstraintViolation] = []

        # 현재 부하 균형 점수 확인
        balance_score = self.bay_tracker.get_load_balance_score()

        if balance_score < 0.7:  # 70% 미만이면 경고
            violations.append(
                ConstraintViolation(
                    constraint_id="P7#1",
                    message=f"부하 균형 불량: 균형 점수 {balance_score:.2f} < 0.7",
                    severity="INFO",
                    block_id=block.block_id,
                )
            )

        return violations
