# [AGENT-ADD] Routing/spacing validation split from constraint_validator.py.

from typing import List

from enhanced_environment.models import EnhancedBlock, ConstraintViolation


class RoutingValidationMixin:
    #################################################################
    # [AGENT-ADD] Routing / 간격 제약 실시간 검증
    #################################################################
    def _validate_routing_spacing_constraints(
        self, block: EnhancedBlock
    ) -> List[ConstraintViolation]:
        """
        C-Seam 간격, 곡판/고심수 간격, 작업장 순서 등 라우팅 제약을 검증.
        ConstraintChecker의 기존 헬퍼를 재사용하여 중복 구현을 피한다.
        """
        violations: List[ConstraintViolation] = []

        # 환경/체커가 없으면 스킵
        if not self.env or not hasattr(self.env, "constraint_checker"):
            return violations

        checker = getattr(self.env, "constraint_checker")

        # 선택 시퀀스 추출 (기완료 블록 순서만 사용; 현재 블록은 제외)
        selected_blocks = []
        # 우선 외부에서 제공한 리플레이 시퀀스가 있으면 사용
        for step in self.completed_steps:
            if hasattr(step, "block_id"):
                selected_blocks.append(step.block_id)

        # 헬퍼 함수가 존재할 때만 호출 (방어적)
        def _add_violation(ok: bool, reason: str, cid: str):
            if ok:
                return
            violations.append(
                ConstraintViolation(
                    constraint_id=cid,
                    message=reason,
                    severity="ERROR",
                    block_id=block.block_id,
                )
            )

        if hasattr(checker, "_check_c_seam_spacing"):
            ok, reason = checker._check_c_seam_spacing(block, selected_blocks)
            _add_violation(ok, reason, "ROUTING_C_SEAM_SPACING")

        if hasattr(checker, "_check_curved_plate_spacing"):
            ok, reason = checker._check_curved_plate_spacing(block, selected_blocks)
            _add_violation(ok, reason, "ROUTING_CURVED_SPACING")

        if hasattr(checker, "_check_high_seam_spacing"):
            ok, reason = checker._check_high_seam_spacing(block, selected_blocks)
            _add_violation(ok, reason, "ROUTING_HIGH_SEAM_SPACING")

        if hasattr(checker, "_check_workshop_order_constraint"):
            all_blocks = list(self.blocks_dict.values()) if self.blocks_dict else []
            ok, reason = checker._check_workshop_order_constraint(
                block, all_blocks, selected_blocks
            )
            _add_violation(ok, reason, "ROUTING_WORKSHOP_ORDER")

        return violations
