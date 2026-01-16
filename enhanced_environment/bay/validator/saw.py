# [AGENT-ADD] SAW/afternoon start constraint validation split from constraint_validator.py.

from datetime import datetime, timedelta, time, date
from typing import List

from enhanced_environment.models import EnhancedBlock, ConstraintViolation


class SawValidationMixin:
    def validate_saw_constraints_realtime(
        self,
        block: EnhancedBlock,
        current_time: datetime,
        actual_machine_2_start_time: datetime = None,
    ) -> List[ConstraintViolation]:
        """
        SAW 제약조건 실시간 검증 (실제 머신 스케줄 기반)

        P6#1,2,3: 오후 3시 착수 제약 실시간 검증
        - Draft 블록은 해당 날짜 오후 3시 착수
        - Cross seam 블록은 해당 날짜 오후 3시 착수
        - 주판 10장 초과 블록은 해당 날짜 오후 3시 착수

        Args:
            block: 처리 중인 블록
            current_time: 현재 시간 (머신 1번 기준)
            actual_machine_2_start_time: 실제 머신 2번 시작 시간 (이미 계산된 경우)

        Returns:
            SAW 제약조건 위반 리스트
        """
        violations: List[ConstraintViolation] = []

        # ✅ P6#1,2,3: 오후 3시 착수 제약 실시간 검증 (실제 머신 스케줄 기반)
        if block.needs_afternoon_start():
            # 🆕 실제 머신 2번(전면SAW) 시작 시간 사용 (이미 계산된 경우) 또는 계산
            if actual_machine_2_start_time is None:
                actual_machine_2_start_time = self._calculate_actual_machine_2_start_time(
                    block, current_time
                )

            # 🆕 시퀀싱 날짜 기준 15:00 비교 (날짜 넘어감 고려)
            sequencing_date = current_time.date()  # 시퀀싱된 날짜
            sequencing_date_3pm = datetime.combine(sequencing_date, time(15, 0))

            # 날짜가 넘어갔으면 제약조건 자동 만족
            if actual_machine_2_start_time.date() > sequencing_date:
                # 날짜 넘어감으로 제약조건 만족 (위반 없음)
                pass
            elif actual_machine_2_start_time < sequencing_date_3pm:
                # 실제 머신 2번 시작 시점이 당일 15:00 이전임
                constraint_id = self._get_afternoon_constraint_id(block)

                violations.append(
                    ConstraintViolation(
                        constraint_id=constraint_id,
                        message=(
                            "실제머신2(SAW) 시작 "
                            f"{actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M')} < 15:00 "
                            "(오후 3시 착수 필요) [선점고려]"
                        ),
                        severity="WARNING",  # 경고만 하고 계속 진행
                        block_id=block.block_id,
                    )
                )

                # 제약 유형별 세부 메시지 추가
                if block.is_draft:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#1_DETAIL",
                            message=(
                                "Draft 블록 실제머신2 오후 3시 이전 처리: "
                                f"{actual_machine_2_start_time.strftime('%H:%M')}"
                            ),
                            severity="INFO",
                            block_id=block.block_id,
                        )
                    )
                elif block.is_cross_seam:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#2_DETAIL",
                            message=(
                                "Cross seam 블록 실제머신2 오후 3시 이전 처리: "
                                f"{actual_machine_2_start_time.strftime('%H:%M')}"
                            ),
                            severity="INFO",
                            block_id=block.block_id,
                        )
                    )
                elif block.main_plate_count > 10:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#3_DETAIL",
                            message=(
                                f"주판 {block.main_plate_count}장 (>10장) 블록 실제머신2 "
                                f"오후 3시 이전 처리: {actual_machine_2_start_time.strftime('%H:%M')}"
                            ),
                            severity="INFO",
                            block_id=block.block_id,
                        )
                    )
            else:
                # 🆕 제약조건 만족 시에도 INFO 메시지 추가
                if block.is_draft:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#1_DETAIL",
                            message=(
                                "Draft 블록 실제머신2 시작: "
                                f"{actual_machine_2_start_time.strftime('%H:%M')} (15:00 제약 만족)"
                            ),
                            severity="INFO",
                            block_id=block.block_id,
                        )
                    )
                elif block.is_cross_seam:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#2_DETAIL",
                            message=(
                                "Cross seam 블록 실제머신2 시작: "
                                f"{actual_machine_2_start_time.strftime('%H:%M')} (15:00 제약 만족)"
                            ),
                            severity="INFO",
                            block_id=block.block_id,
                        )
                    )
                elif block.main_plate_count > 10:
                    violations.append(
                        ConstraintViolation(
                            constraint_id="P6#3_DETAIL",
                            message=(
                                f"주판 {block.main_plate_count}장 (>10장) 블록 실제머신2 시작: "
                                f"{actual_machine_2_start_time.strftime('%H:%M')} (15:00 제약 만족)"
                            ),
                            severity="INFO",
                            block_id=block.block_id,
                        )
                    )

        return violations

    def _calculate_actual_machine_2_start_time(
        self, block: EnhancedBlock, current_time: datetime
    ) -> datetime:
        """
        실제 머신 2번 시작 시간 계산 (CT 테이블 기반)
        """
        try:
            # 🎯 현재까지 완료된 블록들의 시퀀스 가져오기
            current_sequence = []
            for step in self.completed_steps:
                if hasattr(step, "block_id"):
                    current_sequence.append(step.block_id)

            # 현재 블록을 임시로 추가한 시퀀스 생성
            test_sequence = current_sequence + [block.block_id]

            # 베이 할당 정보 생성
            test_bay_assignments = {}
            for block_id in test_sequence:
                if block_id in self.blocks_dict:
                    test_block = self.blocks_dict[block_id]
                    # 환경의 베이 할당 로직 사용
                    if self.env is not None and hasattr(self.env, "_auto_assign_bay"):
                        assigned_bay, _ = self.env._auto_assign_bay(
                            test_block, return_analysis=False
                        )
                        test_bay_assignments[block_id] = assigned_bay
                    else:
                        from enhanced_environment.models import BayType

                        test_bay_assignments[block_id] = BayType.BAY_35A

            # 🎯 makespan_calculator를 활용한 실제 시간 계산
            from enhanced_environment.bay.makespan import calculate_makespan

            # 현재 날짜 시작 시간 계산
            current_date = current_time.date()
            date_start_time = datetime.combine(
                current_date, datetime.min.time().replace(hour=8)
            )

            # makespan 계산으로 CT 테이블 얻기
            _, detailed = calculate_makespan(
                self.blocks_dict,
                test_sequence,
                test_bay_assignments,
                {},  # 당일 기준
            )

            # 현재 블록의 판계 공정 완료 시간 = 머신 2번 시작 시간
            ct_common = detailed["ct_tables"]["ct_common"]
            block_seq_idx = test_sequence.index(block.block_id)
            panel_end_seconds = ct_common[block_seq_idx + 1, 1]  # 1번 공정 완료 시간

            # 실제 머신 2번 시작 시간
            machine_2_start_time = date_start_time + timedelta(
                seconds=panel_end_seconds
            )

            return machine_2_start_time

        except Exception:
            # Fallback: 기존 단순 계산 방식
            return self._fallback_machine_2_time_calculation(block, current_time)

    def _fallback_machine_2_time_calculation(
        self, block: EnhancedBlock, current_time: datetime
    ) -> datetime:
        """Fallback 머신 2번 시간 계산"""
        simulated_time = current_time
        print("fallback_constraint_validator")
        for step in self.completed_steps:
            if hasattr(step, "block_id") and step.block_id in self.blocks_dict:
                prev_block = self.blocks_dict[step.block_id]
                machine_1_processing_time = prev_block.processing_times[0]
                old_time = simulated_time
                simulated_time = simulated_time + timedelta(
                    minutes=machine_1_processing_time
                )

                if simulated_time.date() > old_time.date() or simulated_time.hour >= 22:
                    next_date = simulated_time.date()
                    if simulated_time.hour >= 22 and simulated_time.date() == old_time.date():
                        next_date = old_time.date() + timedelta(days=1)
                    simulated_time = datetime.combine(
                        next_date, datetime.min.time().replace(hour=8)
                    )

        machine_1_processing_time = block.processing_times[0]
        old_time = simulated_time
        machine_2_start_time = simulated_time + timedelta(
            minutes=machine_1_processing_time
        )

        if machine_2_start_time.date() > old_time.date() or machine_2_start_time.hour >= 22:
            next_date = machine_2_start_time.date()
            if (
                machine_2_start_time.hour >= 22
                and machine_2_start_time.date() == old_time.date()
            ):
                next_date = old_time.date() + timedelta(days=1)
            machine_2_start_time = datetime.combine(
                next_date, datetime.min.time().replace(hour=8)
            )

        return machine_2_start_time

    def _get_afternoon_constraint_id(self, block: EnhancedBlock) -> str:
        """
        블록에 해당하는 오후 3시 제약조건 ID 반환

        Args:
            block: 확인할 블록

        Returns:
            해당하는 제약조건 ID
        """
        if block.is_draft:
            return "P6#1"
        if block.is_cross_seam:
            return "P6#2"
        if block.main_plate_count > 10:
            return "P6#3"
        return "P6#1"  # 기본값
