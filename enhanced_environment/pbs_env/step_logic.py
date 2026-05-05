"""
Step handling logic
"""

# [AGENT-ADD] Split from pbs_env/core.py for readability.

import gymnasium as gym
import numpy as np
from datetime import datetime, timedelta, time, date
from typing import List, Dict, Tuple, Optional, Any, Union, Set
import logging
from collections import defaultdict

# 데이터 구조 및 유틸리티
from enhanced_environment.models import (
    EnhancedBlock, EnvironmentState, ActionResult, ConstraintViolation,
    BayType, AssemblyType, PortStarboard, ActionMask, BlockSequence,
    ProcessPhase, SequenceState, ProcessStep
)
from enhanced_environment.common.utils_core import TimeUtils, DataConverter, Logger
from enhanced_environment.constraints import ConstraintConfig

# 제약조건 매니저
from enhanced_environment.constraints.managers import (
    CapacityTracker, PSBlockManager, BayStateTracker, CalendarManager
)
from enhanced_environment.masking import ConstraintChecker

# 분리된 모듈 임포트
from enhanced_environment.bay.assigner import auto_assign_bay, preview_assign_bay
from enhanced_environment.bay.makespan import calculate_makespan
from enhanced_environment.bay.validator import ConstraintValidator

class StepLogicMixin:
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict[str, Any]]:
        """
        PFSP 액션 실행 - 단계별 처리
    
        Args:
            action: 액션 인덱스
                   - SEQUENCE_DECISION: 순서 선택 인덱스
                   - BRANCH_SELECTION: 베이 선택 인덱스 (0=BAY_35A, 1=BAY_36B)
    
        Returns:
            (observation, reward, done, info)
        """
        if self.is_done:
            raise ValueError("환경이 이미 완료됨. reset()을 호출하세요.")
    
        self.current_step += 1
    
        step_info = {
            "step": self.current_step,
            "phase": self.sequence_state.current_phase.value,
            "action": action,
            "violations": [],
            "constraints_checked": [],
            "performance": {}
        }
    
        # [AGENT-ADD] Assembly Decoding 모드 처리
        if self.decoding_mode == "assembly":
            step_info["phase"] = "ASSEMBLY_DECODING"
            result = self._handle_assembly_decision(action, step_info)
        else:
            # 단계별 액션 처리 (PFSP)
            if self.sequence_state.current_phase == ProcessPhase.SEQUENCE_DECISION:
                result = self._handle_sequence_decision(action, step_info)
            elif self.sequence_state.current_phase == ProcessPhase.BRANCH_SELECTION:
                result = self._handle_branch_selection(action, step_info)
            elif self.sequence_state.current_phase == ProcessPhase.PROCESS_EXECUTION:
                result = self._handle_process_execution(step_info)
            else:
                # 완료 상태
                self.is_done = True
                result = (self._get_observation(), 0.0, True, step_info)
    
        observation, reward, done, info = result
    
        # 완료 조건 확인
        if not done and self.decoding_mode != "assembly":
            self._check_completion()
            done = self.is_done
    
        self.logger.debug(f"PFSP Step {self.current_step} 완료: 단계={step_info.get('phase')}, 보상={reward:.2f}, 종료={done}")
    
        return observation, reward, done, info
    

    def _handle_sequence_decision(self, action: int, step_info: Dict) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        Step-by-Step 순서 결정 단계 처리
    
        Args:
            action: 선택할 블록의 인덱스 (현재 선택 가능한 블록들 중에서)
            step_info: 스텝 정보
    
        Returns:
            (observation, reward, done, info)
        """
        # 현재 상태 가져오기
        selected_blocks = getattr(self.sequence_state, 'selected_blocks', [])
        last_assembly_type = getattr(self.sequence_state, 'last_assembly_type', None)
    
        # 현재 선택 가능한 블록들 가져오기
        available_block_ids, violations = self.constraint_checker.get_next_available_blocks(
            self.original_blocks, self.current_time, selected_blocks, last_assembly_type
        )
    
        if not available_block_ids:
            step_info.update({
                "error": "no_available_blocks",
                "violations": violations,
                "selected_count": len(selected_blocks),
                "total_blocks": len(self.original_blocks)
            })
            # 모든 블록이 선택되었으면 완료
            if len(selected_blocks) >= len(self.original_blocks):
                self.sequence_state.sequence_decided = True
                self.sequence_state.current_phase = ProcessPhase.PROCESS_EXECUTION
                return self._get_observation(), 10.0, False, step_info
            else:
                return self._get_observation(), -100.0, True, step_info
    
        # 액션 유효성 확인
        if action >= len(available_block_ids):
            step_info.update({
                "error": "invalid_action",
                "action": action,
                "max_valid": len(available_block_ids) - 1,
                "available_blocks": available_block_ids
            })
            return self._get_observation(), -50.0, False, step_info
    
        # 선택된 블록 ID
        selected_block_id = available_block_ids[action]
        selected_block = self.blocks_dict[selected_block_id]
    
        # 상태 업데이트
        if not hasattr(self.sequence_state, 'selected_blocks'):
            self.sequence_state.selected_blocks = []
        if not hasattr(self.sequence_state, 'block_sequence'):
            self.sequence_state.block_sequence = []
    
        self.sequence_state.selected_blocks.append(selected_block_id)
        self.sequence_state.block_sequence.append(selected_block_id)
        self.sequence_state.last_assembly_type = selected_block.assembly_type
    
        # 순서 결정 보상
        reward = 5.0  # 기본 블록 선택 보상
    
        # Assembly Type 교대 보너스
        if last_assembly_type is not None and last_assembly_type != selected_block.assembly_type:
            reward += 3.0  # Assembly Type 교대 보너스
            print(f"   🎉 Assembly Type 교대 보너스: {last_assembly_type.value} → {selected_block.assembly_type.value}")
    
        # 완료 여부 확인
        sequence_complete = len(self.sequence_state.selected_blocks) >= len(self.original_blocks)
    
        if sequence_complete:
            # 순서 결정 완료
            self.sequence_state.sequence_decided = True
            self.sequence_state.current_phase = ProcessPhase.PROCESS_EXECUTION
            reward += 15.0  # 순서 완료 보너스
            print(f"✅ Step-by-Step 순서 결정 완료: {len(self.sequence_state.block_sequence)}개 블록")
    
        step_info.update({
            "selected_block_id": selected_block_id,
            "selected_block_type": selected_block.assembly_type.value,
            "sequence_progress": f"{len(self.sequence_state.selected_blocks)}/{len(self.original_blocks)}",
            "available_choices": len(available_block_ids),
            "assembly_type_alternated": last_assembly_type is not None and last_assembly_type != selected_block.assembly_type,
            "violations": len(violations),
            "sequence_complete": sequence_complete
        })
    
        return self._get_observation(), reward, False, step_info
    
    # ==== [AGENT-ADD BEGIN: Assembly Decoding step 처리] ====

    def _handle_assembly_decision(self, action: int, step_info: Dict) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        Assembly Decoding 전용 step 처리
        - get_next_available_blocks_assembly 기반 후보 생성
        - 선택 블록 처리 및 제약 검증
        """
        # [AGENT-ADD] 외부에서 미리 계산된 후보 캐시가 있으면 우선 사용
        if self._assembly_last_available_ids:
            available_ids = list(self._assembly_last_available_ids)
            masking_violations = list(self._assembly_last_violations)
            block_analysis = list(self._assembly_last_block_analysis)
        else:
            available_ids, masking_violations, block_analysis = self.get_available_actions_assembly()
    
        # 캐시 초기화 (다음 스텝에 영향 방지)
        self._assembly_last_available_ids = []
        self._assembly_last_violations = []
        self._assembly_last_block_analysis = []
    
        if self.is_done:
            return self._get_observation(), 0.0, True, step_info
    
        if not available_ids:
            step_info.update({
                "error": "no_available_blocks",
                "available_ids": [],
                "violations": masking_violations,
            })
            return self._get_observation(), -100.0, True, step_info
    
        if action >= len(available_ids):
            step_info.update({
                "error": "invalid_action",
                "action": action,
                "max_valid": len(available_ids) - 1,
                "available_ids": available_ids
            })
            return self._get_observation(), -50.0, False, step_info
    
        selected_block_id = available_ids[action]
        selected_block = self.blocks_dict[selected_block_id]
    
        selected_analysis = next(
            (analysis for analysis in block_analysis if analysis.get('block_id') == selected_block_id),
            None
        )
        # [AGENT-EDIT] P6#1,#2,#3 제거 이후 afternoon_guard 추적은 legacy 호환용 빈 상태를 유지한다.
    
        # 베이 자동 할당 (상태 업데이트 포함)
        assigned_bay, bay_analysis = self._auto_assign_bay(selected_block, return_analysis=True)
        self.assembly_current_bay_assignments[selected_block_id] = assigned_bay
        self.assembly_all_bay_assignments[selected_block_id] = assigned_bay
    
        # 선택 상태 업데이트
        self.assembly_selected_blocks.append(selected_block_id)
        self.assembly_total_sequence.append(selected_block_id)
        self.assembly_daily_sequence.append(selected_block_id)
        self.assembly_last_assembly_type = selected_block.assembly_type
        self._assembly_step_index += 1
    
        # [AGENT-ADD] constraint_checker 내부 상태도 동기화
        try:
            self.constraint_checker.mark_block_selected(selected_block_id)
        except Exception as exc:
            # [AGENT-EDIT] 핵심 선택 상태 동기화 실패는 숨기지 않는다.
            raise RuntimeError(
                f"constraint_checker 선택 상태 동기화 실패: block_id={selected_block_id}"
            ) from exc
    
        # sequence_state에도 동기화 (env_state/로그용)
        try:
            self.sequence_state.selected_blocks = self.assembly_selected_blocks
            self.sequence_state.block_sequence = self.assembly_total_sequence
            self.sequence_state.last_assembly_type = selected_block.assembly_type
        except Exception as exc:
            # [AGENT-EDIT] sequence_state 동기화 실패는 이후 관찰/검증을 오염시키므로 즉시 중단한다.
            raise RuntimeError(
                f"sequence_state 동기화 실패: block_id={selected_block_id}"
            ) from exc
    
        # 현재 날짜 makespan 계산
        block_start_time = None
        block_end_time = None
        panel_end_time = None
        makespan_sec = None
        actual_machine_2_start_time = None
        detailed = None
    
        try:
            makespan_sec, detailed = self.calculate_makespan(
                self.assembly_daily_sequence,
                self.assembly_current_bay_assignments,
                self.assembly_previous_machine_state,
                afternoon_guard_blocks=self.assembly_afternoon_guard_blocks_day
            )
    
            bs = next((b for b in detailed['block_schedules'] if b['block_id'] == selected_block_id), None)
            if bs is None:
                raise RuntimeError(f"선택 블록 스케줄을 찾지 못함: block_id={selected_block_id}")
            start_hours = bs['start_seconds'] / 3600.0
            end_hours = bs['end_seconds'] / 3600.0
            if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
                # [AGENT-EDIT] 공장 휴무/중지 반영한 실제 시간 계산
                block_start_time = TimeUtils.add_work_hours_with_calendar(
                    self.assembly_date_start_time, start_hours, self.calendar_manager
                )
                block_end_time = TimeUtils.add_work_hours_with_calendar(
                    self.assembly_date_start_time, end_hours, self.calendar_manager
                )
            else:
                block_start_time = self.assembly_date_start_time + timedelta(seconds=bs['start_seconds'])
                block_end_time = self.assembly_date_start_time + timedelta(seconds=bs['end_seconds'])
    
            ct_common = detailed['ct_tables']['ct_common']
            block_seq_idx = self.assembly_daily_sequence.index(selected_block_id)
            panel_end_seconds = ct_common[block_seq_idx + 1, 1]
            saw_end_seconds = ct_common[block_seq_idx + 1, 2]
            if panel_end_seconds is not None:
                end_hours = panel_end_seconds / 3600.0
                if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
                    panel_end_time = TimeUtils.add_work_hours_with_calendar(
                        self.assembly_date_start_time, end_hours, self.calendar_manager
                    )
                else:
                    panel_end_time = self.assembly_date_start_time + timedelta(seconds=panel_end_seconds)
    
            saw_duration_minutes = selected_block.processing_times[1]
            saw_duration_seconds = saw_duration_minutes * 60
            machine_2_start_seconds = saw_end_seconds - saw_duration_seconds
            start_hours = machine_2_start_seconds / 3600.0
            if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
                actual_machine_2_start_time = TimeUtils.add_work_hours_with_calendar(
                    self.assembly_date_start_time, start_hours, self.calendar_manager
                )
            else:
                actual_machine_2_start_time = self.assembly_date_start_time + timedelta(seconds=machine_2_start_seconds)
        except Exception as exc:
            # [AGENT-EDIT] 핵심 makespan 실패를 단순 합산으로 숨기지 않고 즉시 드러낸다.
            step_info["error"] = "makespan_calculation_failed"
            step_info["error_block_id"] = selected_block_id
            raise RuntimeError(
                f"assembly makespan 계산 실패: block_id={selected_block_id}, date={self.assembly_current_date}"
            ) from exc
    
        # 현재 시간 갱신 (판계 종료 시각 기준)
        if panel_end_time is not None:
            self.assembly_current_datetime = panel_end_time
            self.current_time = panel_end_time
            # [AGENT-EDIT] 공장 중지 시간이면 다음 가동 시각으로 이동
            if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
                if self.calendar_manager.is_closed_time(self.assembly_current_datetime):
                    self.assembly_current_datetime = self.calendar_manager.next_open_time(self.assembly_current_datetime)
                    self.current_time = self.assembly_current_datetime
    
        # 제약 검증 (실제 시작 시간 기반)
        # [AGENT-EDIT] 용량/혹서기 판정은 계획일(assembly_current_date) 기준으로 통일
        capacity_time_override = self.assembly_date_start_time if hasattr(self, "assembly_date_start_time") else None
        block_violations = self._validate_and_commit_constraints_action(
            selected_block,
            assigned_bay,
            block_start_time if block_start_time is not None else self.assembly_current_datetime,
            actual_machine_2_start_time=actual_machine_2_start_time,
            capacity_time_override=capacity_time_override,
            processing_time_seconds=sum(selected_block.processing_times) * 60,
            step_start_time=block_start_time if block_start_time else self.assembly_current_datetime,
            step_end_time=block_end_time if block_end_time else (block_start_time if block_start_time else self.assembly_current_datetime),
            current_in_history=False,
        )

        self.violation_history.extend(block_violations)
    
        # 완료 여부
        if len(self.assembly_selected_blocks) >= len(self.original_blocks):
            self.is_done = True
    
        # 보상 계산
        step_info.update({
            "auto_bay_assignment": True,
            "realtime_validation": True
        })
        reward = self._calculate_reward(step_info, block_violations)
    
        # step_info 구성
        step_info.update({
            "selected_block_id": selected_block_id,
            "assigned_bay": assigned_bay.value if hasattr(assigned_bay, "value") else assigned_bay,
            "block_start_time": block_start_time,
            "block_end_time": block_end_time,
            "panel_end_time": panel_end_time,
            "makespan_sec": makespan_sec,
            "makespan_hours": (makespan_sec / 3600.0) if makespan_sec is not None else None,
            "actual_machine_2_start_time": actual_machine_2_start_time,
            "date_key": self.assembly_current_date.strftime('%Y%m%d'),
            "day_counter": self.assembly_day_counter,
            "available_ids": available_ids,
            "block_analysis": block_analysis,
            "bay_analysis": bay_analysis,
            "masking_violations": masking_violations,
            "constraint_ids": [v.constraint_id for v in block_violations],
            "violation_severity": [v.severity for v in block_violations],
            "violation_details": [v.message for v in block_violations],
            "violations": len(block_violations)
        })
    
        return self._get_observation(), reward, self.is_done, step_info
    # ==== [AGENT-ADD END: Assembly Decoding step 처리] ====
    

    def _handle_process_execution(self, step_info: Dict) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        공정 실행 단계 처리 (자동 진행)
    
        Args:
            step_info: 스텝 정보
    
        Returns:
            (observation, reward, done, info)
        """
        current_block_id = self.sequence_state.get_next_block_to_process()
        if current_block_id is None:
            self.is_done = True
            return self._get_observation(), 0.0, True, step_info
    
        current_block = self.blocks_dict[current_block_id]
        current_process = self.sequence_state.current_process
    
        # 공통 공정 처리 (1-5)
        if current_process in self.sequence_state.common_processes:
            result = self._process_common_step(current_block, current_process, step_info)
        # 분기점
        elif self.sequence_state.is_branch_point():
            # 분기 선택 단계로 전환
            self.sequence_state.current_phase = ProcessPhase.BRANCH_SELECTION
            result = (self._get_observation(), 0.0, False, step_info)
        # 분기 공정 처리 (6-8)
        else:
            # 베이가 이미 결정되어 있어야 함
            if current_block_id not in self.sequence_state.branch_assignments:
                self.is_done = True
                return self._get_observation(), -50.0, True, step_info
    
            assigned_bay = self.sequence_state.branch_assignments[current_block_id]
            result = self._process_branch_step(current_block, current_process, assigned_bay, step_info)
    
        return result
    

    def _handle_branch_selection(self, action: int, step_info: Dict) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        분기 선택 단계 처리 - 환경 자동 베이 할당 방식
    
        변경사항:
        - 기존: 강화학습이 베이 선택 (action_masking 사용)
        - 신규: 환경이 P7 제약조건 기반 자동 베이 할당
    
        Args:
            action: 사용되지 않음 (환경 자동 할당)
            step_info: 단계 정보
    
        Returns:
            (관찰, 보상, 완료여부, 정보)
        """
        current_block_id = self.constraint_checker.get_next_block_to_process()
    
        if not current_block_id or current_block_id not in self.blocks_dict:
            self.logger.error(f"분기 선택 단계에서 유효하지 않은 블록 ID: {current_block_id}")
            return self._get_observation(), -1.0, True, {"error": "invalid_block_id"}
    
        current_block = self.blocks_dict[current_block_id]
    
        # ✅ 환경 자동 베이 할당 방식으로 변경
        return self._process_branch_step_with_auto_bay(
            current_block,
            self.sequence_state.current_process,
            step_info
        )
    

    def _process_common_step(self, block: EnhancedBlock, process_num: int, step_info: Dict) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        Line 1 공정 단계 처리
    
        Args:
            block: 처리할 블록
            process_num: 공정 번호 (1~3)
            step_info: 스텝 정보
    
        Returns:
            (observation, reward, done, info)
        """
        # 현재 블록 순서 인덱스
        block_idx = self.sequence_state.current_block_index
        process_idx = process_num - 1  # 0-based 인덱스
    
        # 처리 시간
        processing_time_minutes = block.processing_times[process_idx]
        processing_time_seconds = processing_time_minutes * 60
    
        # 완료 시간 계산 (calculate_makespan 방식)
        # process 관점: 이전 블록의 같은 공정 완료 시간
        esd_1 = self.completion_tables['COMMON'][block_idx, process_idx + 1]
    
        # job 관점: 같은 블록의 이전 공정 완료 시간  
        esd_2 = self.completion_tables['COMMON'][block_idx + 1, process_idx]
    
        earliest_start = max(esd_1, esd_2)
        completion_time = earliest_start + processing_time_seconds
    
        # 완료 시간 테이블 업데이트
        self.completion_tables['COMMON'][block_idx + 1, process_idx + 1] = completion_time
    
        # 실제 시간 계산
        start_datetime = self.start_time + timedelta(seconds=earliest_start)
        end_datetime = self.start_time + timedelta(seconds=completion_time)
    
        # 환경 시간 업데이트
        self.current_time = end_datetime
    
        # ProcessStep 기록
        process_step = ProcessStep(
            block_id=block.block_id,
            process_num=process_num,
            bay_type=BayType.COMMON,  # 공통 공정은 베이 구분 없음
            start_time=start_datetime,
            end_time=end_datetime,
            processing_time=processing_time_seconds,
            completion_time=completion_time,
            predecessor_job_ct=esd_1,
            predecessor_process_ct=esd_2
        )
        self.completed_steps.append(process_step)
    
        # 다음 단계로 진행
        self.constraint_checker.advance_to_next_step()
        self.sequence_state = self.constraint_checker.sequence_state
    
        # 보상 계산
        reward = 2.0  # 기본 공정 완료 보상
    
        step_info.update({
            "processed_block": block.block_id,
            "process_num": process_num,
            "processing_time": processing_time_minutes,
            "start_time": start_datetime.strftime('%H:%M:%S'),
            "end_time": end_datetime.strftime('%H:%M:%S'),
            "line": "LINE1"
        })
    
        return self._get_observation(), reward, False, step_info
    

    def _process_branch_step(
        self,
        block: EnhancedBlock,
        process_num: int,
        assigned_bay: BayType,
        step_info: Dict
    ) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        Line 2 공정 단계 처리
    
        Args:
            block: 처리할 블록
            process_num: 공정 번호 (4~10)
            assigned_bay: 할당된 베이
            step_info: 스텝 정보
    
        Returns:
            (observation, reward, done, info)
        """
        line_name = f"LINE2{'A' if assigned_bay == BayType.BAY_35A else 'B'}"
        block_idx = self.sequence_state.current_block_index
        process_idx = process_num - 1  # 0-based 인덱스
    
        # 처리 시간
        processing_time_minutes = block.processing_times[process_idx]
        processing_time_seconds = processing_time_minutes * 60
    
        # 첫 번째 분기 공정 (론지취부)
        if process_num == 6:
            common_completion = self.completion_tables['COMMON'][block_idx + 1, 5]
            if assigned_bay == BayType.BAY_35A:
                branch_available = self.completion_tables['BRANCH_A'][block_idx, 0]
                earliest_start = max(common_completion, branch_available)
                completion_time = earliest_start + processing_time_seconds
                self.completion_tables['BRANCH_A'][block_idx + 1, 0] = completion_time
    
                # 사용하지 않는 분기는 이전 상태 유지
                self.completion_tables['BRANCH_B'][block_idx + 1, :] = \
                    self.completion_tables['BRANCH_B'][block_idx, :]
            else:
                branch_available = self.completion_tables['BRANCH_B'][block_idx, 0]
                earliest_start = max(common_completion, branch_available)
                completion_time = earliest_start + processing_time_seconds
                self.completion_tables['BRANCH_B'][block_idx + 1, 0] = completion_time
    
                # 사용하지 않는 분기는 이전 상태 유지
                self.completion_tables['BRANCH_A'][block_idx + 1, :] = \
                    self.completion_tables['BRANCH_A'][block_idx, :]
    
        # 분기 내부 공정 (7번: 론지용접, 8번: 수정)
        elif process_num in (7, 8):
            table_name = 'BRANCH_A' if assigned_bay == BayType.BAY_35A else 'BRANCH_B'
            branch_process_idx = process_num - 6  # 7→1, 8→2
    
            # job 관점과 process 관점의 ESD 계산
            esd_1 = self.completion_tables[table_name][block_idx, branch_process_idx]
            esd_2 = self.completion_tables[table_name][block_idx + 1, branch_process_idx - 1]
            earliest_start = max(esd_1, esd_2)
            completion_time = earliest_start + processing_time_seconds
            self.completion_tables[table_name][block_idx + 1, branch_process_idx] = completion_time
    
        else:
            # 그 외(4~5, 9~10 등)는 공통 공정 처리 로직으로 위임
            return self._process_common_step(block, process_num, step_info)
    
        # 실제 시간 계산
        start_datetime = self.start_time + timedelta(seconds=earliest_start)
        end_datetime   = self.start_time + timedelta(seconds=completion_time)
        self.current_time = end_datetime
    
        # ProcessStep 기록
        process_step = ProcessStep(
            block_id=block.block_id,
            process_num=process_num,
            bay_type=assigned_bay,
            start_time=start_datetime,
            end_time=end_datetime,
            processing_time=processing_time_seconds,
            completion_time=completion_time
        )
        self.completed_steps.append(process_step)
    
        # 마지막 공정(8번)이면 완료 처리
        if process_num == 8:
            violations = []
            is_weekend = TimeUtils.is_weekend(self.current_time)
            is_hot_season = self.calendar_manager.is_hot_season(self.current_time)
            is_holiday_eve = self.calendar_manager.is_holiday_eve(self.current_time)
    
            # ✅ 하이퍼파라미터 기반 용량 추가
            violations.extend(
                self.capacity_tracker.add_block(
                    block, is_weekend, is_hot_season, is_holiday_eve, current_time=self.current_time
                )
            )
    
            violations.extend(self.ps_manager.process_block(block, assigned_bay, self.current_time))
            violations.extend(self.bay_tracker.assign_bay(block, assigned_bay, processing_time_seconds))
    
            if violations:
                self.violation_history.extend(violations)
    
        # 다음 단계 준비
        self.constraint_checker.advance_to_next_step()
        self.sequence_state = self.constraint_checker.sequence_state
    
        # 보상 계산
        reward = 3.0
        if process_num == 8:
            reward += 10.0
    
        step_info.update({
            "processed_block": block.block_id,
            "process_num": process_num,
            "processing_time": processing_time_minutes,
            "start_time": start_datetime.strftime('%H:%M:%S'),
            "end_time": end_datetime.strftime('%H:%M:%S'),
            "line": line_name,
            "assigned_bay": assigned_bay.value
        })
    
        return self._get_observation(), reward, False, step_info
    

    def _process_branch_step_with_auto_bay(
        self,
        block: EnhancedBlock,
        process_num: int,
        step_info: Dict
    ) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        분기 단계 처리 - 환경 자동 베이 할당 방식 + 모든 제약조건 실시간 검증
    
        순서:
        1. 환경이 P7 제약조건 기반으로 베이 자동 할당
        2. 할당된 베이로 블록 처리
        3. 모든 판계 제약조건 실시간 검증 및 기록
        """
        # ✅ 환경이 자동으로 베이 할당
        assigned_bay = self._auto_assign_bay(block)
    
        step_info['assigned_bay'] = assigned_bay.value
        step_info['bay_assignment_method'] = 'AUTO_ENVIRONMENT'
        step_info['processing_time'] = sum(block.processing_times) * 60  # 분 → 초
    
        # ✅ 모든 제약조건 실시간 검증
        all_violations = self.validator.validate_all_constraints_realtime(block, assigned_bay, self.current_time)
    
        # 베이 할당 및 제약조건 위반 기록
        bay_violations = self.bay_tracker.assign_bay(block, assigned_bay, step_info['processing_time'])
        ps_violations = self.ps_manager.process_block(block, assigned_bay, self.current_time)
    
        all_violations.extend(bay_violations)
        all_violations.extend(ps_violations)
        self.violation_history.extend(all_violations)
    
        # ProcessStep 생성 및 기록
        process_step = ProcessStep(
            block_id=block.block_id,
            process_num=process_num,
            bay_type=assigned_bay,
            start_time=self.current_time,
            end_time=self.current_time + timedelta(seconds=step_info['processing_time']),
            processing_time=step_info['processing_time'],
            completion_time=step_info['processing_time']
        )
    
        self.completed_steps.append(process_step)
    
        # 다음 단계로 진행
        self.constraint_checker.advance_to_next_step()
    
        # 완료 여부 확인
        if self.constraint_checker.is_completed():
            self.is_done = True
            step_info['total_makespan'] = self._calculate_final_makespan()
    
        # 보상 계산
        reward = self._calculate_reward(step_info, all_violations)
    
        # step_info 업데이트
        step_info.update({
            'violations': len(all_violations),
            'violation_details': [str(v) for v in all_violations],
            'bay_tracker_status': self.bay_tracker.get_status(),
            'auto_bay_assignment': True,
            'realtime_validation': True
        })
    
        return self._get_observation(), reward, self.is_done, step_info
    
    # `_validate_...` 함수들은 `constraint_validator.py`로 이동했으므로 여기서 호출
