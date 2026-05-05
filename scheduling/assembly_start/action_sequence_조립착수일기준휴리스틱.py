# action_sequence.py

import os  # [AGENT-EDIT] PBS_FORCE_DEBUG 기반 디버그 출력 제어
import copy
import random
import argparse
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional, Set
from collections import defaultdict

from enhanced_environment.common.utils_core import (
    compute_schedule_span_hours,
    compute_sum_daily_max_makespan_hours,
    count_primary_cseam_violations,
    DataConverter,
    count_expanded_block_units,
    expand_rows_with_subassembly,
    resolve_line_group_for_block,
    get_line_group_and_workshop_code,
    sum_result_violation_counts,
)
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.pbs_env import EnhancedPanelBlockShop
from enhanced_environment.models import BayType, AssemblyType, ConstraintViolation, ProcessStep
from utils.csv_save import save_assembly_decoding_schedule_info, save_assembly_decoding_bay_info
from scheduling.common.process_schedule import (
    save_detailed_process_schedule as _save_detailed_process_schedule,
)
from scheduling.common.result_builders import (
    create_block_result as _create_block_result,
)
from scheduling.common.selection_rules import select_block_id  # [AGENT-ADD] 공통 선택 규칙
from scheduling.common.interactive_override import (
    apply_manual_bay_override_if_requested,
    filter_available_ids_by_precedence,
    prime_forced_override_cache,
    resolve_forced_prefix_block,
)
from scheduling.common.defaults import (
    DEFAULT_EXCEL_PATH,
    DEFAULT_ASSEMBLY_DATE_OFFSET,
    DEFAULT_SELECTION_METHODS,
)
from scheduling.common.run_artifacts import (
    maybe_load_runtime_config,
    mirror_to_legacy_path,
    resolve_output_path,
)
# [AGENT-ADD] main.py config.yaml 연동
from runtime_config import get_runtime_config
from scheduling.common.independent_constraint_audit import sync_runtime_results_to_canonical_constraints


def _get_capacity_load(block) -> int:
    """Return SEAM+C/SEAM count used for capacity decisions."""
    return block.seam_count + getattr(block, 'c_seam_count', 0)

# 🔧 디버깅 설정
DEBUG_ASSEMBLY = False  # Assembly Decoding 디버깅 출력 여부
VERBOSE_ASSEMBLY = False  # 상세 진행 상황 출력 여부
SAVE_DETAILED_ANALYSIS = True  # 🆕 상세 분석 CSV 저장 여부 (detailed_assembly_schedule_info_*.csv) - 기본값

# 🔧 기본값 설정 (circular import 방지)
# [AGENT-EDIT] 기본값은 scheduling/common/defaults.py로 통합


def save_detailed_process_schedule(date_key: str, sequence: List[int], bay_assignments: Dict, 
                                 ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime):
    """공정별 상세 스케줄링 CSV 저장 (공통 유틸 위임)."""
    # [AGENT-EDIT] 공통 유틸 함수로 위임
    return _save_detailed_process_schedule(
        date_key=date_key,
        sequence=sequence,
        bay_assignments=bay_assignments,
        ct_tables=ct_tables,
        blocks_dict=blocks_dict,
        date_start_time=date_start_time,
        filename=f'detailed_actionmasking_constraint_schedule_processes_{date_key}.csv',
        verbose=VERBOSE_ASSEMBLY,
        label="공정별 상세 스케줄",
    )


def save_detailed_process_schedule_assembly(date_key: str, sequence: List[int], bay_assignments: Dict, 
                                          ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime):
    """공정별 상세 스케줄링 CSV 저장 (Assembly Decoding 방식)."""
    # [AGENT-EDIT] 공통 유틸 함수로 위임
    return _save_detailed_process_schedule(
        date_key=date_key,
        sequence=sequence,
        bay_assignments=bay_assignments,
        ct_tables=ct_tables,
        blocks_dict=blocks_dict,
        date_start_time=date_start_time,
        filename=f'detailed_assembly_decoding_schedule_processes_{date_key}.csv',
        verbose=VERBOSE_ASSEMBLY,
        label="Assembly Decoding 공정별 상세 스케줄",
    )


def get_test_settings():
    """test.py 설정 가져오기 (circular import 방지)"""
    try:
        import sys
        if 'test' in sys.modules:
            # test.py가 이미 로드된 경우에만 가져오기
            test_module = sys.modules['test']
            excel_path = getattr(test_module, 'EXCEL_PATH', DEFAULT_EXCEL_PATH)
            offset = getattr(test_module, 'ASSEMBLY_DATE_OFFSET', DEFAULT_ASSEMBLY_DATE_OFFSET)
            print(f"✅ test.py 설정 로드: {excel_path}, offset: {offset}일")
            return excel_path, offset
        else:
            raise ImportError("test.py not loaded yet")
    except (ImportError, AttributeError):
        # [AGENT-EDIT] config.yaml data.excel_path가 있으면 기본값 덮어쓰기
        runtime_cfg = get_runtime_config() or {}
        if isinstance(runtime_cfg, dict):
            data_cfg = runtime_cfg.get("data") or {}
            cfg_path = data_cfg.get("excel_path")
            if cfg_path:
                print(f"✅ config.yaml data.excel_path 적용: {cfg_path}")
                return str(cfg_path), DEFAULT_ASSEMBLY_DATE_OFFSET
        print(f"⚠️ test.py 설정 사용 불가, 기본값 사용: {DEFAULT_EXCEL_PATH}, offset: {DEFAULT_ASSEMBLY_DATE_OFFSET}일")
        return DEFAULT_EXCEL_PATH, DEFAULT_ASSEMBLY_DATE_OFFSET



def create_block_result(block, assigned_bay, sequence, bay_analysis, violations, date_str=None, start_time=None, end_time=None, makespan_minutes=None, makespan_hours=None, total_completion_time=None, date_start_time=None, actual_machine_2_start_time=None):
    """블록 결과 생성 (action_sequence_오토베이수정.py와 동일한 형식)"""
    # [AGENT-EDIT] 공통 유틸 함수로 위임
    return _create_block_result(
        block=block,
        assigned_bay=assigned_bay,
        sequence=sequence,
        bay_analysis=bay_analysis,
        violations=violations,
        date_str=date_str,
        start_time=start_time,
        end_time=end_time,
        makespan_minutes=makespan_minutes,
        makespan_hours=makespan_hours,
        total_completion_time=total_completion_time,
        date_start_time=date_start_time,
        actual_machine_2_start_time=actual_machine_2_start_time,
    )


def run_assembly_decoding_sequence(
    excel_path: str,
    decoding_type: str = "assembly",
    selection_method: str = "random",  # 🆕 추가: "priority" 또는 "random"
    max_days: int = 10,
    start_date: str = "2024-05-09",
    date_offset: int = 0,  # 🆕 추가: 조립착수일 기준 오프셋 (일 단위)
    output_csv: str = "assembly_decoding_results.csv",
    save_csv: bool = True,        # 🆕 추가: 결과 CSV 저장 여부
    save_detailed: bool = True    # 🆕 추가: 상세 CSV 저장 여부
) -> Tuple[List[Dict], Dict]:
    """
    Assembly Decoding을 사용한 시퀀싱 실행 - 전체 블록 풀에서 하나씩 선택
    """
    if VERBOSE_ASSEMBLY:
        print(f"🚀 Assembly Decoding 시퀀싱 시작")
    
    # 1. 데이터 로드
    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path)
    constraint_config = ConstraintConfig()
    
    # 핵심 로직 실행
    return _run_assembly_decoding_core(blocks, metadata, constraint_config, decoding_type, 
                                      selection_method, max_days, start_date, date_offset, 
                                      output_csv, save_csv, save_detailed)


def run_assembly_decoding_sequence_with_blocks(
    blocks: List,
    metadata: Dict,
    decoding_type: str = "assembly",
    selection_method: str = "random",
    max_days: int = 10,
    start_date: str = "2024-05-09",
    date_offset: int = 0,
    output_csv: str = "assembly_decoding_results.csv",
    save_csv: bool = True,        # 🆕 추가: 결과 CSV 저장 여부
    save_detailed: bool = True,   # 🆕 추가: 상세 CSV 저장 여부
    forced_sequence: List[int] = None,  # 🆕 추가: 강화학습용 강제 시퀀스
    expand_rows: bool = True,           # [AGENT-ADD] 별판 확장 여부
    forced_prefix_block_ids: Optional[List[int]] = None,  # [AGENT-ADD] user prefix-resume
    allow_forced_prefix_override: bool = False,  # [AGENT-ADD] masking 밖 강제 선택 허용
    precedence_rules: Optional[List[Tuple[int, int]]] = None,  # [AGENT-ADD] interactive precedence
    manual_bay_assignments: Optional[Dict[int, str]] = None,  # [AGENT-ADD] interactive bay override
    return_step_info: bool = False,  # [AGENT-ADD] interactive analysis trace export
) -> Tuple[List[Dict], Dict]:
    """
    Assembly Decoding (메모리 데이터 직접 사용)
    
    Args:
        forced_sequence: 강화학습에서 생성된 시퀀스 (블록 인덱스 리스트)
                        None이면 기존 휴리스틱 방식 사용
    """
    if forced_sequence is not None:
        if DEBUG_ASSEMBLY:
            print(f"🎯 강화학습 시퀀스 강제 적용 모드 (시퀀스 길이: {len(forced_sequence)})")
        # 강화학습 시퀀스 평가 모드
        forced_results = _evaluate_forced_sequence(
            blocks,
            metadata,
            forced_sequence,
            decoding_type,
            max_days,
            start_date,
            date_offset,
            save_csv,
            save_detailed,
        )
        if return_step_info:
            schedule_results, statistics = forced_results
            return schedule_results, statistics, []
        return forced_results
    else:
        if VERBOSE_ASSEMBLY:
            print(f"🚀 Assembly Decoding 시퀀싱 시작 (메모리 데이터)")
        # 기존 휴리스틱 모드
        constraint_config = ConstraintConfig()
        return _run_assembly_decoding_core(
            blocks,
            metadata,
            constraint_config,
            decoding_type,
            selection_method,
            max_days,
            start_date,
            date_offset,
            output_csv,
            save_csv,
            save_detailed,
            expand_rows=expand_rows,
            use_env_step=True,  # [AGENT-ADD] 기본: env.step 기반
            forced_prefix_block_ids=forced_prefix_block_ids,
            allow_forced_prefix_override=allow_forced_prefix_override,
            precedence_rules=precedence_rules,
            manual_bay_assignments=manual_bay_assignments,
            return_step_info=return_step_info,
        )


def _run_assembly_decoding_core(
    blocks: List,
    metadata: Dict,
    constraint_config,
    decoding_type: str = "assembly",
    selection_method: str = "random",
    max_days: int = 10,
    start_date: str = "2024-05-09",
    date_offset: int = 0,
    output_csv: str = "assembly_decoding_results.csv",
    save_csv: bool = True,        # 🆕 추가: 결과 CSV 저장 여부
    save_detailed: bool = True,   # 🆕 추가: 상세 CSV 저장 여부
    use_env_step: bool = True,    # [AGENT-ADD] env.step 기반 실행 여부
    expand_rows: bool = True,     # [AGENT-ADD] 별판 확장 여부
    forced_prefix_block_ids: Optional[List[int]] = None,  # [AGENT-ADD] user prefix-resume
    allow_forced_prefix_override: bool = False,  # [AGENT-ADD] masking 밖 강제 선택 허용
    precedence_rules: Optional[List[Tuple[int, int]]] = None,  # [AGENT-ADD] interactive precedence
    manual_bay_assignments: Optional[Dict[int, str]] = None,  # [AGENT-ADD] interactive bay override
    return_step_info: bool = False,  # [AGENT-ADD] interactive analysis trace export
) -> Tuple[List[Dict], Dict]:
    """
    Assembly Decoding 시퀀싱의 핵심 로직을 분리하여 재사용
    """
    # 🆕 상세 분석 정보 수집용
    assembly_step_info = []
    assembly_bay_analyses_by_date = {}
    
    # 1. 데이터 로드
    # blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path) # 이 부분은 호출하는 함수에서 처리
    # constraint_config = ConstraintConfig() # 이 부분은 호출하는 함수에서 처리
    
    # [AGENT-ADD] independent audit/sync는 스케줄링으로 오염되기 전 pristine 입력을 사용한다.
    audit_blocks = copy.deepcopy(blocks)
    audit_metadata = copy.deepcopy(metadata)

    # 2. 시작 날짜 설정
    start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
    
    # 🆕 조립착수일 기준 오프셋 적용
    if date_offset != 0:
        adjusted_start_datetime = start_datetime + timedelta(days=date_offset)
        if DEBUG_ASSEMBLY:
            print(f"🔧 조립착수일 오프셋 적용: {start_date} + {date_offset}일 = {adjusted_start_datetime.strftime('%Y-%m-%d')}")
        actual_start_time = adjusted_start_datetime.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        actual_start_time = start_datetime.replace(hour=8, minute=0, second=0, microsecond=0)
    
    # 3. 환경 초기화
    env = EnhancedPanelBlockShop(
        blocks=blocks,
        start_time=actual_start_time,
        constraint_config=constraint_config,
        metadata=metadata,
        decoding_mode="assembly",
        assembly_max_days=max_days,
        assembly_capacity_bypass=False,
        assembly_update_state_on_capacity=True,
        assembly_update_state_on_empty=True,
        assembly_keep_bay_assignments_on_capacity=False
    )
    
    # 4. Assembly Decoding 설정
    env.constraint_checker.set_decoding_type("assembly")
    env.constraint_checker.set_assembly_blocks(blocks)
    
    # 🆕 Assembly 확장 모드는 이제 ConstraintConfig에서 설정됨
    # constraint_config에서 assembly_expansion_* 설정을 통해 제어
    
    blocks_dict = {block.block_id: block for block in blocks}
    env.constraint_checker.set_blocks_dict(blocks_dict)
    
    # 5. 전체 블록을 하나씩 선택하는 메인 루프
    schedule_results = []
    selected_blocks = []
    
    # 🔧 수정: 오프셋이 적용된 시작날짜 사용
    if date_offset != 0:
        current_date = adjusted_start_datetime.date()
        current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
    else:
        current_date = start_datetime.date()
        current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
        
    assembly_sequence = 1
    last_assembly_type = None
    day_counter = 1
    
    # 🆕 시간 추적 변수들 (Action Masking 방식과 동일)
    date_start_time = actual_start_time  # 날짜별 시작 시간 (8시)
    final_sequence = []  # 전체 선택 순서
    current_bay_assignments = {}  # 베이 할당 추적

    # ==== [AGENT-ADD] env.step 경로: 일자별 공정 CSV 생성을 위한 누적 버퍼 ====
    daily_sequences_by_date: Dict[str, List[int]] = defaultdict(list)
    daily_bay_assignments_by_date: Dict[str, Dict[int, BayType]] = defaultdict(dict)
    # [AGENT-EDIT] legacy no-op: P6 시간제약 제거 이후에도 calculate_makespan 인터페이스 호환을 위해 유지
    daily_guard_blocks_by_date: Dict[str, Set[int]] = defaultdict(set)
    
    # 🆕 머신 상태 추적 (날짜간 연결용)
    previous_machine_state = {} # 이전 날의 머신 완료 시간 상태
    afternoon_guard_blocks_day: Set[int] = set()  # [AGENT-EDIT] legacy no-op
    afternoon_guard_blocks_all: Set[int] = set()  # [AGENT-EDIT] legacy no-op
    daily_sequence: List[int] = []  # [AGENT-EDIT] 당일 선택 시퀀스
    
    
    # print(f"📅 Day {day_counter}: {current_date.strftime('%Y-%m-%d')}")
    
    # [AGENT-ADD] env.step 기반 루트 (정석)
    legacy_loop_enabled = not use_env_step
    if use_env_step:
        forced_prefix_idx = 0
        while not env.is_done and len(env.assembly_selected_blocks) < len(blocks):
            available_ids, violations, block_analysis = env.get_available_actions_assembly()
            if env.is_done:
                break

            forced_block_id, forced_prefix_idx = resolve_forced_prefix_block(
                forced_prefix_block_ids,
                forced_prefix_idx,
                env.assembly_selected_blocks,
            )
            filtered_available_ids, precedence_blocked_ids, precedence_override_block_id = filter_available_ids_by_precedence(
                available_ids=available_ids,
                selected_block_ids=env.assembly_selected_blocks,
                precedence_rules=precedence_rules,
            )
            forced_override_active = False

            if not filtered_available_ids and forced_block_id is None and precedence_override_block_id is None:
                break

            # 블록 선택 방식
            # [AGENT-EDIT] 공통 선택 규칙 사용
            if forced_block_id is not None and forced_block_id in available_ids:
                selected_block_id = forced_block_id
                selection_reason = "FORCED_PREFIX_AVAILABLE"
                action_idx = available_ids.index(selected_block_id)
            elif forced_block_id is not None and allow_forced_prefix_override:
                selected_block_id = forced_block_id
                selection_reason = "FORCED_PREFIX_OVERRIDE"
                forced_override_active = True
                prime_forced_override_cache(
                    env=env,
                    forced_block_id=forced_block_id,
                    masking_violations=violations,
                    block_analysis=block_analysis,
                    blocks_dict=blocks_dict,
                    note="interactive prefix override",
                )
                action_idx = 0
            elif precedence_override_block_id is not None and allow_forced_prefix_override:
                selected_block_id = precedence_override_block_id
                selection_reason = "PRECEDENCE_OVERRIDE"
                forced_override_active = True
                prime_forced_override_cache(
                    env=env,
                    forced_block_id=precedence_override_block_id,
                    masking_violations=violations,
                    block_analysis=block_analysis,
                    blocks_dict=blocks_dict,
                    note="interactive precedence override",
                )
                action_idx = 0
            else:
                selected_block_id, selection_reason = select_block_id(
                    available_ids=filtered_available_ids,
                    blocks_dict=blocks_dict,
                    selection_method=selection_method,
                )
                action_idx = available_ids.index(selected_block_id)

            manual_bay_override = apply_manual_bay_override_if_requested(
                env=env,
                selected_block_id=selected_block_id,
                manual_bay_assignments=manual_bay_assignments,
                final_reason="INTERACTIVE_USER_FIXED_BAY",
            )

            _, step_reward, done, step_info = env.step(action_idx)

            selected_block = blocks_dict[selected_block_id]
            assigned_bay = step_info.get('assigned_bay')
            bay_analysis = step_info.get('bay_analysis', {})
            block_start_time = step_info.get('block_start_time')
            block_end_time = step_info.get('block_end_time')
            makespan_sec = step_info.get('makespan_sec')
            makespan_minutes = (makespan_sec / 60.0) if makespan_sec is not None else None
            makespan_hours = (makespan_sec / 3600.0) if makespan_sec is not None else None
            total_completion_time = None
            if makespan_sec is not None:
                total_completion_time = env.assembly_date_start_time + timedelta(seconds=makespan_sec)

            # 위반 복원
            block_violations: List[ConstraintViolation] = []
            constraint_ids = step_info.get('constraint_ids', []) or []
            violation_details = step_info.get('violation_details', []) or []
            violation_severity = step_info.get('violation_severity', []) or []
            for cid, msg, sev in zip(constraint_ids, violation_details, violation_severity):
                block_violations.append(
                    ConstraintViolation(
                        constraint_id=cid,
                        message=msg,
                        severity=sev,
                        block_id=selected_block_id
                    )
                )
            for mv in step_info.get('masking_violations', []) or []:
                block_violations.append(mv)

            # 🆕 상세 분석 정보 수집
            selected_analysis = next((analysis for analysis in block_analysis if analysis.get('block_id') == selected_block_id), None)
            step_analysis = {
                'step': len(env.assembly_selected_blocks),
                'selected_block_id': selected_block_id,
                'available_count': len([b for b in block_analysis if b.get('is_available')]),
                'excluded_count': len([b for b in block_analysis if not b.get('is_available')]),
                'block_analysis': block_analysis,
                'current_datetime': env.assembly_current_datetime.strftime('%Y-%m-%d %H:%M'),
                'current_date': env.assembly_current_date.strftime('%Y-%m-%d'),
                'total_selected_so_far': len(env.assembly_selected_blocks)
            }
            step_analysis.update({
                'selected_assembly_date': selected_block.assembly_start_date.strftime('%Y-%m-%d'),
                'selected_assembly_type': selected_block.assembly_type.value,
                'selected_port_starboard': selected_block.port_starboard.value,
                'selection_summary': f"Assembly Decoding 스텝 {len(env.assembly_selected_blocks)} 선택",
                'selection_method': selection_method,
                'selection_reason': selection_reason,
                'forced_override': forced_override_active,
                'precedence_blocked_ids': ','.join(str(block_id) for block_id in precedence_blocked_ids),
                'manual_bay_override': manual_bay_override or '',
                'emergency_mode': any(getattr(v, 'constraint_id', '') in {'EMERGENCY_MODE', 'EMERGENCY_PS_ONLY', 'EMERGENCY_RELEASE', 'MIN_VIOLATION_CHOICE'} for v in block_violations),
                'ps_forced': False,
                'masking_stage_used': selected_analysis.get('masking_stage', '') if selected_analysis else ''
            })

            # 결과 생성
            result = create_block_result(
                selected_block,
                BayType(assigned_bay) if isinstance(assigned_bay, str) else assigned_bay,
                assembly_sequence,
                bay_analysis,
                block_violations,
                env.assembly_current_date.strftime('%Y%m%d'),
                start_time=block_start_time,
                end_time=block_end_time,
                makespan_minutes=makespan_minutes,
                makespan_hours=makespan_hours,
                total_completion_time=total_completion_time,
                date_start_time=env.assembly_date_start_time,
                actual_machine_2_start_time=step_info.get('actual_machine_2_start_time')
            )
            result['line_group'] = getattr(selected_block, 'line_group', '')
            result['interactive_precedence_blocked_ids'] = ','.join(str(block_id) for block_id in precedence_blocked_ids)
            result['interactive_manual_bay_override'] = manual_bay_override or ''

            schedule_results.append(result)
            assembly_sequence += 1
            assembly_step_info.append(step_analysis)

            # ==== [AGENT-ADD] 날짜별 공정 CSV 생성을 위한 누적 ====
            date_key = env.assembly_current_date.strftime('%Y%m%d')
            daily_sequences_by_date[date_key].append(selected_block_id)
            daily_bay_assignments_by_date[date_key][selected_block_id] = (
                BayType(assigned_bay) if isinstance(assigned_bay, str) else assigned_bay
            )
            # [AGENT-EDIT] legacy no-op guard 기록 유지
            try:
                daily_guard_blocks_by_date[date_key] = set(env.assembly_afternoon_guard_blocks_day)
            except Exception as exc:
                # [AGENT-EDIT] legacy guard 상태 동기화 실패도 결과 재현성을 해치므로 즉시 중단한다.
                raise RuntimeError(
                    f"Assembly heuristic legacy guard 추적 실패: date={date_key}"
                ) from exc

            # 베이 선택 상세 분석 수집
            date_key = env.assembly_current_date.strftime('%Y%m%d')
            if date_key not in assembly_bay_analyses_by_date:
                assembly_bay_analyses_by_date[date_key] = []

            bay_analysis_entry = {
                'block_id': selected_block.block_id,
                'assembly_start_date': selected_block.assembly_start_date.strftime('%Y-%m-%d'),
                'width_m': selected_block.width,
                'longi_count': selected_block.longi_count,
                'material_type': selected_block.material_type.value,
                'assigned_bay': assigned_bay if isinstance(assigned_bay, str) else assigned_bay.value,
                'final_reason': bay_analysis.get('final_reason', f"Assembly Decoding - Step {len(env.assembly_selected_blocks)}"),
                'constraint_checks': bay_analysis.get('constraint_checks', {
                    'P7#1_load_balance': {'result': 'PASS'},
                    'P7#2_physical': {'result': 'PASS'},
                    'P7#3_4_ps_pair': {'result': 'PASS'},
                    'P7#7_8_consecutive': {'result': 'PASS'},
                    'P7#10_longi_30plus': {'result': 'PASS'},
                    'P7#11_special_block': {'result': 'PASS'}
                }),
                'bay_35a_worktime_before': bay_analysis.get('bay_35a_worktime_before', 0),
                'bay_36b_worktime_before': bay_analysis.get('bay_36b_worktime_before', 0),
                'bay_35a_worktime_after': bay_analysis.get('bay_35a_worktime_after', 0),
                'bay_36b_worktime_after': bay_analysis.get('bay_36b_worktime_after', 0)
            }
            assembly_bay_analyses_by_date[date_key].append(bay_analysis_entry)

        # env 상태를 꼬리 로직에 전달
        selected_blocks = list(env.assembly_selected_blocks)
        current_date = env.assembly_current_date
        current_datetime = env.assembly_current_datetime
        date_start_time = env.assembly_date_start_time
        final_sequence = list(env.assembly_daily_sequence)
        current_bay_assignments = dict(env.assembly_current_bay_assignments)
        previous_machine_state = dict(env.assembly_previous_machine_state)
        afternoon_guard_blocks_day = set(env.assembly_afternoon_guard_blocks_day)

        # ==== [AGENT-ADD] env.step 경로: 날짜별 공정 CSV 저장 ====
        if save_detailed and daily_sequences_by_date:
            try:
                # 날짜 순서 보장
                prev_state = {}
                for dk in sorted(daily_sequences_by_date.keys()):
                    seq = daily_sequences_by_date.get(dk) or []
                    assigns = daily_bay_assignments_by_date.get(dk) or {}
                    if not seq or not assigns:
                        continue
                    date_obj = datetime.strptime(dk, "%Y%m%d").date()
                    date_start_time_local = datetime.combine(date_obj, datetime.min.time().replace(hour=8))
                    makespan_sec, detailed = env.calculate_makespan(
                        seq,
                        assigns,
                        prev_state,
                        afternoon_guard_blocks=daily_guard_blocks_by_date.get(dk, set())
                    )
                    save_detailed_process_schedule_assembly(
                        date_key=dk,
                        sequence=seq,
                        bay_assignments=assigns,
                        ct_tables=detailed['ct_tables'],
                        blocks_dict=blocks_dict,
                        date_start_time=date_start_time_local
                    )

                    # 다음 날짜로 머신 상태 전달 (legacy 방식과 동일)
                    final_state = detailed.get('final_machine_state', {}) or {}
                    day_duration_seconds = 24 * 3600
                    prev_offset = prev_state.get('day_start_offset', 0) if isinstance(prev_state, dict) else 0
                    new_offset = prev_offset + day_duration_seconds

                    def _shift(lst):
                        return [t + prev_offset for t in lst] if isinstance(lst, list) else []

                    prev_state = {
                        'common_times': _shift(final_state.get('common_times', [])),
                        'branch_a_times': _shift(final_state.get('branch_a_times', [])),
                        'branch_b_times': _shift(final_state.get('branch_b_times', [])),
                        'day_start_offset': new_offset
                    }
            except Exception as process_err:
                if DEBUG_ASSEMBLY:
                    print(f"   ⚠️ Assembly 공정별 상세 스케줄 생성 실패 (env.step 경로): {process_err}")

    # 메인 시퀀싱 루프: 전체 블록을 하나씩 선택 (레거시)
    while legacy_loop_enabled and len(selected_blocks) < len(blocks):
        # ==== [AGENT-EDIT BEGIN: per-step time reset] ====
        panel_end_seconds = None
        block_start_time = None
        block_end_time = None
        total_completion_time = None
        makespan_minutes = None
        makespan_hours = None
        # ==== [AGENT-EDIT END] ====
        if day_counter > max_days:
            break
            
        # 현재 날짜 계산 및 날짜별 시작 시간 기록
        date_key = current_date.strftime('%Y%m%d')
        
        # 🔥 용량 체크를 블록 선택 전에 먼저 수행
        is_weekend = current_datetime.weekday() >= 5
        current_capacity_used = env.capacity_tracker.get_capacity_used(is_weekend)
        
        # 🔥 용량 한계 계산
        is_hot_season = env.calendar_manager.is_hot_season(current_datetime)
        is_holiday_eve = env.calendar_manager.is_holiday_eve(current_datetime)
        actual_capacity_limit = env.capacity_tracker.get_capacity_limits(is_weekend, is_hot_season, is_holiday_eve)
        
        if DEBUG_ASSEMBLY:
            print(f"   📊 용량 상태: {current_capacity_used}/{actual_capacity_limit}심 (혹서기: {is_hot_season}, 주말: {is_weekend}, 명절전날: {is_holiday_eve})")
        
        # 🔥 용량이 이미 한계에 도달했으면 다음날로 이동
        if current_capacity_used >= actual_capacity_limit:
            if DEBUG_ASSEMBLY:
                print(f"   ⚠️ 용량 한계 도달: {current_capacity_used} >= {actual_capacity_limit} (혹서기: {is_hot_season}, 주말: {is_weekend})")
                print(f"   📅 {current_date.strftime('%Y-%m-%d')} 마무리 후 다음날로 이동")
            
            # 현재 날짜의 공정별 상세 스케줄 저장 (블록이 있는 경우만)
            if final_sequence and current_bay_assignments:
                try:
                    current_date_key = current_date.strftime('%Y%m%d')
                    current_date_start_time = datetime.combine(current_date, datetime.min.time().replace(hour=8))
                    
                    # 이전 날의 머신 상태 포함하여 makespan 계산
                    makespan_sec, detailed = env.calculate_makespan(
                        final_sequence, current_bay_assignments, previous_machine_state,
                        afternoon_guard_blocks=afternoon_guard_blocks_day
                    )
                    
                    # 다음 날로 전달할 머신 상태 저장
                    final_state = detailed.get('final_machine_state', {}) or {}
                    day_duration_seconds = 24 * 3600
                    prev_offset = previous_machine_state.get('day_start_offset', 0) if isinstance(previous_machine_state, dict) else 0
                    new_offset = prev_offset + day_duration_seconds
                    # 시간을 절대좌표(기준일 0시)로 누적 저장
                    def _shift(lst):
                        return [t + prev_offset for t in lst] if isinstance(lst, list) else []
                    previous_machine_state = {
                        'common_times': _shift(final_state.get('common_times', [])),
                        'branch_a_times': _shift(final_state.get('branch_a_times', [])),
                        'branch_b_times': _shift(final_state.get('branch_b_times', [])),
                        'day_start_offset': new_offset
                    }
                    
                    if save_detailed:
                        save_detailed_process_schedule_assembly(
                            date_key=current_date_key,
                            sequence=final_sequence,
                            bay_assignments=current_bay_assignments,
                            ct_tables=detailed['ct_tables'],
                            blocks_dict=blocks_dict,
                            date_start_time=current_date_start_time
                        )
                except Exception as process_err:
                    if DEBUG_ASSEMBLY:
                        print(f"   ⚠️ Assembly 공정별 상세 스케줄 생성 실패 ({current_date_key}): {process_err}")
            
        # 선택 가능한 블록 찾기 (용량에 여유가 있을 때만)
        env.constraint_checker.set_sequence(selected_blocks)
        available_ids, violations, block_analysis = env.constraint_checker.get_next_available_blocks_assembly(
            blocks,
            current_datetime,
            selected_blocks,
            last_assembly_type,
            panel_date=current_date,
            previous_machine_state=previous_machine_state,
            current_bay_assignments=current_bay_assignments,
            current_day_selected_blocks=daily_sequence
        )
        masking_violation_map: Dict[Optional[int], List[ConstraintViolation]] = defaultdict(list)
        for violation in violations:
            block_id = getattr(violation, 'block_id', None)
            if block_id is None:
                continue
            masking_violation_map[block_id].append(violation)
        
        if not available_ids:
            # �� 이전 날짜의 공정별 상세 스케줄 저장 (날짜가 바뀌기 전에)
            if final_sequence and current_bay_assignments:
                try:
                    prev_date_key = (current_date - timedelta(days=1)).strftime('%Y%m%d')
                    prev_date_start_time = datetime.combine(current_date - timedelta(days=1), datetime.min.time().replace(hour=8))
                    
                    # 🆕 이전 날의 머신 상태 포함하여 makespan 계산
                    makespan_sec, detailed = env.calculate_makespan(
                        final_sequence, current_bay_assignments, previous_machine_state,
                        afternoon_guard_blocks=afternoon_guard_blocks_day
                    )
                    
                    # 🆕 다음 날로 전달할 머신 상태 저장
                    previous_machine_state = detailed.get('final_machine_state', {})
                    # 다음 날 시작 시간 오프셋 계산 (8시간 = 28800초)
                    day_duration_seconds = 24 * 3600  # 24시간
                    previous_machine_state['day_start_offset'] = day_duration_seconds
                    
                    if save_detailed:
                        save_detailed_process_schedule_assembly(
                            date_key=prev_date_key,
                            sequence=final_sequence,
                            bay_assignments=current_bay_assignments,
                            ct_tables=detailed['ct_tables'],
                            blocks_dict=blocks_dict,
                            date_start_time=prev_date_start_time
                        )
                except Exception as process_err:
                    if DEBUG_ASSEMBLY:
                        print(f"   ⚠️ Assembly 공정별 상세 스케줄 생성 실패 ({prev_date_key}): {process_err}")
            
            # 다음 날로 이동
            current_date += timedelta(days=1)
            current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
            day_counter += 1
            
            # 🆕 공장 연속 가동 고려: 다음날은 항상 08:00부터 시작 (공정별 독립성)
            date_start_time = current_datetime  # 항상 08:00
            
            # 🆕 핵심 수정: 다음날은 새로운 시퀀스로 시작 (공정별 독립성 활용)
            final_sequence = []
            current_bay_assignments = {}
            afternoon_guard_blocks_day = set()
            
            # 다음 날짜에서 다시 후보 평가
            continue
        
        # 🆕 블록 선택 방식에 따른 처리
        # [AGENT-EDIT] 공통 선택 규칙 사용
        selected_block_id, selection_reason = select_block_id(
            available_ids=available_ids,
            blocks_dict=blocks_dict,
            selection_method=selection_method,
        )

        selected_block = blocks_dict[selected_block_id]
        selected_analysis = next((analysis for analysis in block_analysis if analysis.get('block_id') == selected_block_id), None)
        
        # 🔥 추가 안전장치: 선택된 블록이 용량을 초과하는지 재확인
        # (이미 위에서 체크했지만, 혹시 모를 상황을 대비)
        selected_block_load = _get_capacity_load(selected_block)
        if current_capacity_used + selected_block_load > actual_capacity_limit:
            # 🔥 용량 초과 시 현재 날짜 마무리 후 다음날로 이동
            if DEBUG_ASSEMBLY:
                print(f"   ⚠️ 용량 초과: {current_capacity_used} + {selected_block_load} > {actual_capacity_limit}")
                print(f"   📅 {current_date.strftime('%Y-%m-%d')} 마무리 후 다음날로 이동")
            
            # 현재 날짜의 공정별 상세 스케줄 저장 (블록이 있는 경우만)
            if final_sequence and current_bay_assignments:
                try:
                    current_date_key = current_date.strftime('%Y%m%d')
                    current_date_start_time = datetime.combine(current_date, datetime.min.time().replace(hour=8))
                    
                    # 이전 날의 머신 상태 포함하여 makespan 계산
                    makespan_sec, detailed = env.calculate_makespan(
                        final_sequence, current_bay_assignments, previous_machine_state,
                        afternoon_guard_blocks=afternoon_guard_blocks_day
                    )
                    
                    # 다음 날로 전달할 머신 상태 저장
                    previous_machine_state = detailed.get('final_machine_state', {})
                    day_duration_seconds = 24 * 3600
                    previous_machine_state['day_start_offset'] = day_duration_seconds
                    
                    if save_detailed:
                        save_detailed_process_schedule_assembly(
                            date_key=current_date_key,
                            sequence=final_sequence,
                            bay_assignments=current_bay_assignments,
                            ct_tables=detailed['ct_tables'],
                            blocks_dict=blocks_dict,
                            date_start_time=current_date_start_time
                        )
                except Exception as process_err:
                    if DEBUG_ASSEMBLY:
                        print(f"   ⚠️ Assembly 공정별 상세 스케줄 생성 실패 ({current_date_key}): {process_err}")
            
            # 다음 날로 이동
            current_date += timedelta(days=1)
            current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
            day_counter += 1
            
            # 공장 연속 가동 고려: 다음날은 항상 08:00부터 시작
            date_start_time = current_datetime
            
            # 다음날은 새로운 시퀀스로 시작
            final_sequence = []
            current_bay_assignments = {}
            
            # 용량 리셋
            is_weekend = current_datetime.weekday() >= 5
            if is_weekend:
                env.capacity_tracker.reset_weekend()
            else:
                env.capacity_tracker.reset_daily()
            
            # 동적 용량 계산 추가
            is_hot_season = env.calendar_manager.is_hot_season(current_datetime)
            is_holiday_eve = env.calendar_manager.is_holiday_eve(current_datetime)
            
            if day_counter > max_days:
                break
            
            # 🔥 중요: 용량 초과로 다음날로 넘어간 경우, 현재 블록을 처리하지 않고 continue
            # 다음 루프에서 새로운 날짜에서 다시 블록 선택부터 시작
            continue
        
        # 블록 처리
        assigned_bay, bay_analysis = env._auto_assign_bay(selected_block, return_analysis=True)
        current_bay_assignments[selected_block_id] = assigned_bay
        
        # 🆕 베이 선택 상세 분석 수집
        date_key = current_date.strftime('%Y%m%d')
        if date_key not in assembly_bay_analyses_by_date:
            assembly_bay_analyses_by_date[date_key] = []
        
        bay_analysis_entry = {
            'block_id': selected_block.block_id,
            'assembly_start_date': selected_block.assembly_start_date.strftime('%Y-%m-%d'),
            'width_m': selected_block.width,
            'longi_count': selected_block.longi_count,
            'material_type': selected_block.material_type.value,
            'assigned_bay': assigned_bay.value,
            'final_reason': bay_analysis.get('final_reason', f"Assembly Decoding - Step {len(selected_blocks)}"),
            'constraint_checks': bay_analysis.get('constraint_checks', {
                'P7#1_load_balance': {'result': 'PASS'},
                'P7#2_physical': {'result': 'PASS'},
                'P7#3_4_ps_pair': {'result': 'PASS'},
                'P7#7_8_consecutive': {'result': 'PASS'},
                'P7#10_longi_30plus': {'result': 'PASS'},
                'P7#11_special_block': {'result': 'PASS'}
            }),
            'bay_35a_worktime_before': bay_analysis.get('bay_35a_worktime_before', 0),
            'bay_36b_worktime_before': bay_analysis.get('bay_36b_worktime_before', 0),
            'bay_35a_worktime_after': bay_analysis.get('bay_35a_worktime_after', 0),
            'bay_36b_worktime_after': bay_analysis.get('bay_36b_worktime_after', 0)
        }
        assembly_bay_analyses_by_date[date_key].append(bay_analysis_entry)
        
        # 🆕 Action Masking 방식으로 makespan 계산
        final_sequence.append(selected_block_id)
        bs = None
        detailed = None
        try:
            # 🆕 이전 날의 머신 상태 포함하여 makespan 계산
            makespan_sec, detailed = env.calculate_makespan(
                final_sequence, current_bay_assignments, previous_machine_state,
                afternoon_guard_blocks=afternoon_guard_blocks_day
            )
            bs = next((b for b in detailed['block_schedules'] if b['block_id'] == selected_block_id), None)
            if bs is None:
                raise RuntimeError(f"선택 블록 스케줄을 찾지 못함: block_id={selected_block_id}")
            block_start_time = date_start_time + timedelta(seconds=bs['start_seconds'])
            block_end_time = date_start_time + timedelta(seconds=bs['end_seconds'])
            # 🆕 전체 makespan 기준으로 계산
            total_completion_time = date_start_time + timedelta(seconds=makespan_sec)
            makespan_minutes = makespan_sec / 60.0
            makespan_hours = makespan_minutes / 60.0
        except Exception as makespan_err:
            # [AGENT-EDIT] 핵심 makespan 실패를 단순 합산으로 숨기지 않는다.
            raise RuntimeError(
                f"Assembly heuristic makespan 계산 실패: block_id={selected_block_id}, date={current_date}"
            ) from makespan_err
        
        # 🆕 실제 시작 시간으로 제약 검증 (실제 처리된 블록이므로 모든 제약조건 검사)
        # 🆕 실제 머신 2번(전면SAW) 시작 시간 계산 (makespan 계산 결과에서 추출)
        actual_machine_2_start_time = None
        if bs is not None and detailed is not None:
            # CT 테이블에서 머신 2번(전면SAW) 시작 시간 직접 계산
            ct_common = detailed['ct_tables']['ct_common']
            block_seq_idx = final_sequence.index(selected_block_id)
            # 머신 2번(전면SAW) 시작 시간 = 전면SAW 시작 시간 (CT 테이블은 1-based 인덱스)
            # ct_common의 컬럼: [0: 시작, 1: 판계완료, 2: 전면SAW완료, 3: TurnOver완료, 4: 후면SAW완료, 5: NC완료]
            panel_end_seconds = ct_common[block_seq_idx + 1, 1]  # 판계 완료 시간
            saw_end_seconds = ct_common[block_seq_idx + 1, 2]    # 전면SAW 완료 시간
            
            # 전면SAW 시작 시간 = 전면SAW 완료 시간 - 전면SAW 처리 시간
            saw_duration_minutes = selected_block.processing_times[1]  # 전면SAW 처리 시간 (분)
            saw_duration_seconds = saw_duration_minutes * 60
            machine_2_start_seconds = saw_end_seconds - saw_duration_seconds
            
            actual_machine_2_start_time = date_start_time + timedelta(seconds=machine_2_start_seconds)
            
            # 디버깅: 실제 계산된 머신 2번 시작 시간 출력 (필요시)
            if DEBUG_ASSEMBLY:
                print(f"   🔧 BLK_{selected_block.block_id}: 머신2번 시작 시간 = {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M:%S')}")

        block_violations = env._validate_and_commit_constraints_action(
            selected_block,
            assigned_bay,
            block_start_time if block_start_time is not None else current_datetime,
            actual_machine_2_start_time=actual_machine_2_start_time,
            processing_time_seconds=sum(selected_block.processing_times) * 60,
            step_start_time=block_start_time,
            step_end_time=block_end_time,
            current_in_history=False,
        )

        # [AGENT-EDIT] 후공정 착수 순서 제약은 기록 유지 (오탐 방지 로직은 action_masking 쪽에서 보정)

        # 🆕 현재 시간 진행 반영: 판계 완료 시각(공정1 종료) 기준으로 다음 선택 시간 이동
        if panel_end_seconds is not None:
            panel_end_time = date_start_time + timedelta(seconds=panel_end_seconds)
        elif block_start_time is not None:
            panel_end_time = block_start_time + timedelta(minutes=selected_block.processing_times[0])
        else:
            panel_end_time = current_datetime + timedelta(minutes=selected_block.processing_times[0])

        # [AGENT-EDIT] P6 시각 추적: PBS_FORCE_DEBUG가 켜진 경우에만 출력
        debug_force = os.environ.get("PBS_FORCE_DEBUG", "").strip().lower() in {"1", "true", "on", "yes"}
        if debug_force and selected_block.block_id in {12, 39, 50}:
            debug_msg = f"[DEBUG] BLK_{selected_block.block_id} 선택"
            debug_msg += f" | current_datetime={current_datetime.strftime('%Y-%m-%d %H:%M')}"
            debug_msg += f" | panel_end_time={panel_end_time.strftime('%Y-%m-%d %H:%M')}"
            if actual_machine_2_start_time:
                debug_msg += f" | machine2_start={actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M')}"
            print(debug_msg)

        # 당일 시퀀스 업데이트
        daily_sequence.append(selected_block_id)

        current_datetime = panel_end_time
        env.current_time = current_datetime

        # 🔥 용량 상태 정보 수정: 실제 처리 후 상태로 업데이트
        # 제약조건 검사에서 나온 용량 정보가 틀렸을 수 있으므로, 실제 상태로 교체
        corrected_violations = []
        for violation in block_violations:
            if violation.constraint_id in ['P5#8', 'P5#9', 'P5#10', 'P5#16']:
                # 용량 관련 제약조건은 실제 처리 후 상태로 교체
                actual_capacity_after = env.capacity_tracker.get_capacity_used(is_weekend)
                
                # 새로운 메시지 생성 (실제 상태 기반)
                if '초과' in violation.message:
                    # ERROR: 초과 상황 (이미 다음날 이동으로 처리했으므로 제외)
                    continue
                else:
                    # INFO: 정보성 메시지는 실제 상태로 업데이트
                    corrected_message = f"혹서기 심수 제한: {actual_capacity_after}/{actual_capacity_limit}심 (하이퍼파라미터: 6심 절대값 감소)"
                    
                    # 새로운 violation 객체 생성 (실제 상태 반영)
                    corrected_violation = ConstraintViolation(
                        constraint_id=violation.constraint_id,
                        message=corrected_message,
                        severity='INFO',  # 실제 처리된 블록이므로 INFO로
                        block_id=selected_block.block_id
                    )
                    corrected_violations.append(corrected_violation)
            else:
                # 용량 외 제약조건은 그대로 유지
                corrected_violations.append(violation)
        
        # 🆕 혹서기 시즌에는 항상 용량 정보를 INFO로 기록 (일관성 확보)
        if is_hot_season:
            actual_capacity_after = env.capacity_tracker.get_capacity_used(is_weekend)
            
            # 이미 P5#16 violation이 있는지 확인
            has_p5_16 = any(v.constraint_id == 'P5#16' for v in corrected_violations)
            
            if not has_p5_16:
                # P5#16 INFO 메시지 추가 (일관성을 위해)
                info_violation = ConstraintViolation(
                    constraint_id='P5#16',
                    message=f"혹서기 심수 제한: {actual_capacity_after}/{actual_capacity_limit}심 (하이퍼파라미터: 6심 절대값 감소)",
                    severity='INFO',
                    block_id=selected_block.block_id
                )
                corrected_violations.append(info_violation)
        
        block_violations = corrected_violations

        # 마스킹 단계 위반을 결과에 합산(RL과 동일한 기준)
        existing_violation_keys = {(v.constraint_id, v.message) for v in block_violations}
        existing_messages = {v.message for v in block_violations}
        for masking_violation in masking_violation_map.get(selected_block_id, []):
            key = (masking_violation.constraint_id, masking_violation.message)
            if key in existing_violation_keys or masking_violation.message in existing_messages:
                continue
            block_violations.append(masking_violation)
            existing_violation_keys.add(key)
            existing_messages.add(masking_violation.message)

        # ✅ 완화 메시지는 정보성으로 강등 (중복 ERROR 방지)
        for v in block_violations:
            msg = getattr(v, 'message', '')
            if msg.startswith('[RELAX') or msg.startswith('착수일고정-') or '완화' in msg:
                v.severity = 'INFO'

        # L/F 교대 완화 및 비상 모드 위반 기록
        selected_analysis = selected_analysis or next(
            (analysis for analysis in block_analysis if analysis['block_id'] == selected_block_id),
            None
        )
        if selected_analysis:
            inclusion_reason = (selected_analysis.get('inclusion_reason') or '').strip()
            if inclusion_reason:
                if inclusion_reason.startswith('[RELAX]'):
                    block_violations.append(
                        ConstraintViolation(
                            constraint_id='RELAX_STAGE',
                            message=inclusion_reason,
                            severity='INFO',  # [AGENT-EDIT] 완화 사용은 정보용으로만 기록
                            block_id=selected_block.block_id
                        )
                    )
                if '최후 비상 모드' in inclusion_reason:
                    block_violations.append(
                        ConstraintViolation(
                            constraint_id='EMERGENCY_MODE',
                            message=f"비상 모드 적용: {inclusion_reason}",
                            # [AGENT-EDIT] Treat emergency mode as INFO to avoid double counting.
                            severity='INFO',
                            block_id=selected_block.block_id
                        )
                    )
        # [AGENT-EDIT] 완화 로그 일괄 정규화: RELAX_STAGE는 INFO로 강제
        for v in block_violations:
            if getattr(v, "constraint_id", "") == "RELAX_STAGE":
                v.severity = "INFO"

        # 🆕 상세 분석 정보 수집
        step_analysis = {
            'step': len(selected_blocks),
            'selected_block_id': selected_block_id,
            'available_count': len([b for b in block_analysis if b['is_available']]),
            'excluded_count': len([b for b in block_analysis if not b['is_available']]),
            'block_analysis': block_analysis,  # 이 부분이 중요!
            'current_datetime': current_datetime.strftime('%Y-%m-%d %H:%M'),
            'current_date': current_date.strftime('%Y-%m-%d'),  # 🆕 날짜별 분리용
            'total_selected_so_far': len(selected_blocks)
        }
        step_analysis.update({
            'selected_assembly_date': selected_block.assembly_start_date.strftime('%Y-%m-%d'),
            'selected_assembly_type': selected_block.assembly_type.value,
            'selected_port_starboard': selected_block.port_starboard.value,  # .value 추가
            'selection_summary': f"Assembly Decoding 스텝 {len(selected_blocks)} 선택",
            'selection_method': selection_method,  # 🆕 추가
            'selection_reason': selection_reason,  # 🆕 추가
            'emergency_mode': any('비상 모드' in str(v) for v in violations),
            'ps_forced': False,
            'masking_stage_used': next((str(v) for v in violations if '마스킹 해제' in str(v)), '')
        })
        
        # 결과 생성 (🆕 시간 정보 포함)
        result = create_block_result(
            selected_block, assigned_bay, assembly_sequence, bay_analysis, 
            block_violations, current_date.strftime('%Y%m%d'),
            start_time=block_start_time,
            end_time=block_end_time,
            makespan_minutes=makespan_minutes,
            makespan_hours=makespan_hours,
            total_completion_time=total_completion_time,
            date_start_time=date_start_time,
            actual_machine_2_start_time=actual_machine_2_start_time
        )

        result['line_group'] = getattr(selected_block, 'line_group', '')
        
        schedule_results.append(result)
        selected_blocks.append(selected_block_id)
        
        # 상태 업데이트
        env.constraint_checker.mark_block_selected(selected_block_id)
        assembly_sequence += 1
        last_assembly_type = selected_block.assembly_type
        
        # 🆕 상세 분석 정보 수집
        assembly_step_info.append(step_analysis)

    # 🆕 마지막 날짜의 공정별 상세 스케줄 저장
    if final_sequence and current_bay_assignments:
        try:
            final_date_key = current_date.strftime('%Y%m%d')
            final_date_start_time = datetime.combine(current_date, datetime.min.time().replace(hour=8))
            
            # 🆕 이전 날의 머신 상태 포함하여 makespan 계산
            makespan_sec, detailed = env.calculate_makespan(
                final_sequence, current_bay_assignments, previous_machine_state,
                afternoon_guard_blocks=afternoon_guard_blocks_day
            )
            
            if save_detailed:
                save_detailed_process_schedule_assembly(
                    date_key=final_date_key,
                    sequence=final_sequence,
                    bay_assignments=current_bay_assignments,
                    ct_tables=detailed['ct_tables'],
                    blocks_dict=blocks_dict,
                    date_start_time=final_date_start_time
                )
        except Exception as process_err:
            if DEBUG_ASSEMBLY:
                print(f"   ⚠️ Assembly 최종 공정별 상세 스케줄 생성 실패 ({final_date_key}): {process_err}")
    
    # 6. 결과 저장
################################################################################################################################################################################################
# fix: 별판 복수 행 확장 (Assembly 결과)
################################################################################################################################################################################################
    raw_schedule_results = list(schedule_results)

    # 7. 🆕 상세 분석 CSV 저장
    if save_detailed:
        if VERBOSE_ASSEMBLY:
            # print(f"\n🔍 Assembly Decoding 상세 분석 CSV 저장 중...")
            pass
    
    # 날짜별로 step_info 분리
    step_info_by_date = {}
    for step_info in assembly_step_info:
        step_date = step_info.get('current_date', start_datetime.strftime('%Y-%m-%d'))
        date_key = datetime.strptime(step_date, '%Y-%m-%d').strftime('%Y%m%d') if isinstance(step_date, str) else step_date.strftime('%Y%m%d')
        
        if date_key not in step_info_by_date:
            step_info_by_date[date_key] = []
        step_info_by_date[date_key].append(step_info)
    
    # 각 날짜별로 스케줄링 상세 분석 저장
    for date_key, daily_step_info in step_info_by_date.items():
        if save_detailed:  # 🔥 파라미터로 제어 (SAVE_DETAILED_ANALYSIS 대신)
            save_assembly_decoding_schedule_info(daily_step_info, date_key, blocks)
        # print(f"📄 Assembly 스케줄링 분석 저장: detailed_assembly_schedule_info_{date_key}.csv ({len(daily_step_info)}개 스텝)")
    
    # 베이 선택 상세 분석 저장 (날짜별로 이미 분리되어 있음)
    if assembly_bay_analyses_by_date and save_detailed:  # 🔥 파라미터로 제어 (SAVE_DETAILED_ANALYSIS 대신)
        for date_key, bay_analyses in assembly_bay_analyses_by_date.items():
            save_assembly_decoding_bay_info(bay_analyses, date_key)
            # print(f"📄 Assembly 베이 분석 저장: detailed_assembly_bayselect_info_{date_key}.csv")
    
    # ==== [AGENT-EDIT BEGIN: canonical runtime replay sync] ====
    runtime_cfg = get_runtime_config() or {}
    raw_schedule_results, audit_stats, _ = sync_runtime_results_to_canonical_constraints(
        schedule_results=raw_schedule_results,
        blocks=audit_blocks,
        metadata=audit_metadata,
        runtime_cfg=runtime_cfg,
        case_label="assembly_runtime",
    )
    if expand_rows:
        schedule_results = expand_rows_with_subassembly(raw_schedule_results)
    else:
        schedule_results = raw_schedule_results
    # ==== [AGENT-EDIT END] ====

    # ==== [AGENT-EDIT BEGIN: save audited runtime rows] ====
    # runtime CSV도 독립 감사와 동일한 행 단위 위반 집계를 저장해야 한다.
    if save_csv:
        df = pd.DataFrame(schedule_results)
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        if VERBOSE_ASSEMBLY:
            pass
    # ==== [AGENT-EDIT END] ====

    # 8. 통계 계산
    total_blocks_processed = len(schedule_results)
    total_blocks_expected = count_expanded_block_units(blocks)
    total_time_hours = sum(r['total_time_min'] for r in schedule_results) / 60.0
    
    # P/S 연속성 분석
    ps_success_count = 0
    ps_pairs_found = 0
    
    # 간단한 P/S 연속성 체크 (연속된 P-S 패턴 찾기)
    for i in range(len(schedule_results) - 1):
        current_block = schedule_results[i]
        next_block = schedule_results[i + 1]
        
        if (current_block['port_starboard'] == 'P' and 
            next_block['port_starboard'] == 'S' and
            current_block['block_id'] + 1 == next_block['block_id']):  # 간단한 P/S 쌍 가정
            ps_pairs_found += 1
            ps_success_count += 1
    
    ps_success_rate = (ps_success_count / ps_pairs_found * 100) if ps_pairs_found > 0 else 0
    
    # 9. 통계 생성
    
    # [AGENT-EDIT] 논문 비교용 canonical makespan은 결과 row의 실제 달력 시간 축으로 계산한다.
    actual_makespan_hours = compute_schedule_span_hours(raw_schedule_results)
    total_primary_violations = int(audit_stats.get('total_violations_primary', 0))
    total_raw_violations = int(audit_stats.get('total_violations_raw', 0))
    total_meta_violations = int(audit_stats.get('total_violations_meta', 0))
    total_info_violations = int(audit_stats.get('total_violations_info', 0))
    total_cseam_violations = count_primary_cseam_violations(raw_schedule_results)

    statistics = {
        'total_blocks_processed': total_blocks_processed,
        'total_blocks_expected': total_blocks_expected,
        'success_rate': (total_blocks_processed / total_blocks_expected) * 100 if total_blocks_expected else 0.0,
        'total_time_hours': total_time_hours,
        'makespan_hours': actual_makespan_hours,
        'sum_daily_max_makespan_hours': compute_sum_daily_max_makespan_hours(raw_schedule_results),
        'total_violations': total_primary_violations,
        'total_violations_primary': total_primary_violations,
        'total_violations_raw': total_raw_violations,
        'total_violations_meta': total_meta_violations,
        'total_violations_info': total_info_violations,
        'total_cseam_violations': total_cseam_violations,
        'ps_success_rate': ps_success_rate,
        'ps_pairs_total': ps_pairs_found,
        'ps_pairs_successful': ps_success_count,
        'daily_analyses_generated': len(assembly_bay_analyses_by_date),
        'step_analyses_generated': len(assembly_step_info),
        'audit_workshop_order_inversions': int(audit_stats.get('audit_workshop_order_inversions', 0)),
        'audit_constraint_ids': list(audit_stats.get('audit_constraint_ids', [])),
        'audit_constraint_families': list(audit_stats.get('audit_constraint_families', [])),
    }
    
    if return_step_info:
        return schedule_results, statistics, assembly_step_info
    return schedule_results, statistics


def _evaluate_forced_sequence(
    blocks: List,
    metadata: Dict,
    forced_sequence: List[int],
    decoding_type: str = "assembly",
    max_days: int = 10,
    start_date: str = "2025-01-01",
    date_offset: int = 0,
    save_csv: bool = False,
    save_detailed: bool = False
) -> Tuple[List[Dict], Dict]:
    """
    강화학습에서 생성된 시퀀스를 강제로 적용하여 평가
    
    Args:
        forced_sequence: 블록 인덱스 리스트 [0, 1, 2, ..., 49]
    """
    from enhanced_environment.constraints import ConstraintConfig
    from enhanced_environment.pbs_env import EnhancedPanelBlockShop
    from datetime import datetime, timedelta
    
    # 시작 날짜 설정
    start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
    if date_offset != 0:
        adjusted_start_datetime = start_datetime + timedelta(days=date_offset)
        actual_start_time = adjusted_start_datetime.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        actual_start_time = start_datetime.replace(hour=8, minute=0, second=0, microsecond=0)
    
    # 환경 초기화
    constraint_config = ConstraintConfig()
    env = EnhancedPanelBlockShop(
        blocks=blocks,
        start_time=actual_start_time,
        constraint_config=constraint_config,
        metadata=metadata
    )
    
    # Assembly Decoding 설정
    env.constraint_checker.set_decoding_type("assembly")
    env.constraint_checker.set_assembly_blocks(blocks)
    
    # 🆕 Assembly 확장 모드는 이제 ConstraintConfig에서 설정됨
    # constraint_config에서 assembly_expansion_* 설정을 통해 제어
    
    blocks_dict = {block.block_id: block for block in blocks}
    env.constraint_checker.set_blocks_dict(blocks_dict)
    
    # 🎯 강제 시퀀스를 블록 ID로 변환
    if len(forced_sequence) != len(blocks):

        print(f"⚠️ 시퀀스 길이 불일치: {len(forced_sequence)} != {len(blocks)}")
        # 길이 맞추기
        if len(forced_sequence) > len(blocks):
            forced_sequence = forced_sequence[:len(blocks)]
        else:
            # 부족한 부분은 남은 블록들로 채우기
            print(f"⚠️ 시퀀스 길이 부족: {len(forced_sequence)} < {len(blocks)}")
            remaining_indices = [i for i in range(len(blocks)) if i not in forced_sequence]
            forced_sequence.extend(remaining_indices[:len(blocks) - len(forced_sequence)])
    
    # 인덱스를 실제 블록 ID로 변환
    block_id_sequence = []
    for idx in forced_sequence:
        if 0 <= idx < len(blocks):
            block_id_sequence.append(blocks[idx].block_id)
        else:
            print(f"⚠️ 잘못된 인덱스: {idx}, 첫 번째 블록으로 대체")
            block_id_sequence.append(blocks[0].block_id)
    
    if DEBUG_ASSEMBLY:
        print(f"   변환된 블록 ID 시퀀스: {block_id_sequence[:5]}... (총 {len(block_id_sequence)}개)")
    
    # 🎯 강제 시퀀스로 결과 생성
    schedule_results = []
    current_date = start_datetime.date()
    current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
    date_start_time = actual_start_time
    
    # 베이 할당 및 makespan 계산을 위한 준비
    bay_assignments = {}
    final_sequence = []
    # [AGENT-ADD] 강제 시퀀스 평가도 동일한 afternoon guard 인자를 안정적으로 사용한다.
    afternoon_guard_blocks_day: Set[int] = set()
    
    for i, block_id in enumerate(block_id_sequence):
        if block_id not in blocks_dict:
            continue

        block = blocks_dict[block_id]

        # 베이 할당 (간단한 로드밸런싱)
        assigned_bay, bay_analysis = env._auto_assign_bay(block, return_analysis=True)
        bay_assignments[block_id] = assigned_bay
        final_sequence.append(block_id)

        # 간단한 시간 계산 (정확한 계산은 makespan에서)
        block_total_time = sum(block.processing_times)
        previous_total_minutes = sum(
            sum(blocks_dict[bid].processing_times) for bid in final_sequence[:-1]
        )
        block_start_time = date_start_time + timedelta(minutes=previous_total_minutes)
        block_end_time = block_start_time + timedelta(minutes=block_total_time)

        # 결과 생성
        result = create_block_result(
            block, assigned_bay, i + 1, bay_analysis,
            [], current_date.strftime('%Y%m%d'),  # 빈 위반 리스트
            start_time=block_start_time,
            end_time=block_end_time,
            makespan_minutes=block_total_time,
            makespan_hours=block_total_time / 60.0,
            total_completion_time=block_end_time,
            date_start_time=date_start_time,
            actual_machine_2_start_time=None  # 강제 시퀀스에서는 실제 계산 없음
        )

        schedule_results.append(result)

    # 🎯 정확한 makespan 계산
    try:
        makespan_sec, detailed = env.calculate_makespan(
            final_sequence, bay_assignments, {},
            afternoon_guard_blocks=afternoon_guard_blocks_day
        )
        
        # 결과에 정확한 makespan 정보 업데이트
        for i, result in enumerate(schedule_results):
            if i < len(detailed['block_schedules']):
                bs = detailed['block_schedules'][i]
                result['makespan_minutes'] = bs['duration_seconds'] / 60.0
                result['makespan_hours'] = bs['duration_seconds'] / 3600.0
                
                # 시간 정보 업데이트
                result['start_time'] = (date_start_time + timedelta(seconds=bs['start_seconds'])).strftime('%Y-%m-%d %H:%M')
                result['end_time'] = (date_start_time + timedelta(seconds=bs['end_seconds'])).strftime('%Y-%m-%d %H:%M')
        
        if DEBUG_ASSEMBLY:
            print(f"   🎯 강제 시퀀스 평가 완료: makespan = {makespan_sec/3600:.2f}시간")
        
    except Exception as e:
        # [AGENT-EDIT] 강제 시퀀스 평가에서도 makespan 실패를 숨기지 않는다.
        raise RuntimeError("강제 시퀀스 makespan 계산 실패") from e

################################################################################################################################################################################################
# fix: 별판 복수 행 확장 (강제 시퀀스 평가 결과)
################################################################################################################################################################################################
    schedule_results = expand_rows_with_subassembly(schedule_results)

    # 통계 생성
    statistics = {
        'total_blocks_processed': len(schedule_results),
        'total_blocks_expected': count_expanded_block_units(blocks),
        'success_rate': (len(schedule_results) / count_expanded_block_units(blocks)) * 100 if count_expanded_block_units(blocks) else 0.0,
        'total_time_hours': makespan_sec / 3600.0,
        'makespan_hours': makespan_sec / 3600.0,
        'total_violations': 0,  # 강제 시퀀스에서는 위반 체크 안함
        'total_cseam_violations': 0,  # [AGENT-EDIT] 강제 시퀀스는 cseam도 미집계
        'ps_success_rate': 0,   # 간단화
        'ps_pairs_total': 0,
        'ps_pairs_successful': 0,
        'daily_analyses_generated': 0,
        'step_analyses_generated': 0,
        'forced_sequence_mode': True  # 🆕 강제 모드 표시
    }
    
    if return_step_info:
        return schedule_results, statistics, assembly_step_info
    return schedule_results, statistics


def save_assembly_results(results: List[Dict], statistics: Dict, output_path: str):
    """
    Assembly Decoding 결과를 CSV로 저장
    """
    if not results:
        if DEBUG_ASSEMBLY:
            print(f"⚠️ 저장할 결과가 없습니다.")
        return
    
    try:
        
        # DataFrame 생성
        df = pd.DataFrame(results)
        
        # CSV 저장
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        if VERBOSE_ASSEMBLY:
            print(f"�� 결과 저장 완료: {output_path}")
        
        # 통계 요약을 별도 텍스트 파일로 저장
        txt_path = output_path.replace('.csv', '.txt')
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("🎉 Assembly Decoding 결과 요약\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"📋 총 처리 블록: {statistics['processed_blocks']}/{statistics['total_blocks']}개\n")
            f.write(f"📊 성공률: {statistics['processed_blocks'] / statistics['total_blocks'] * 100:.1f}%\n")
            f.write(f"🏁 사용 일수: {statistics['days_used']}일\n")
            f.write(f"🚫 총 위반: {statistics['total_violations']}개\n")
            f.write(f"🔧 완화 적용: {statistics['relaxation_applied']}회\n\n")
            
            f.write("📅 일별 요약:\n")
            f.write("-" * 30 + "\n")
            for date, summary in statistics['daily_summary'].items():
                f.write(f"{date}: {summary['blocks_processed']}개 블록, ")
                f.write(f"{summary['violations']}개 위반, ")
                f.write(f"{summary['capacity_used']}심 사용, ")
                f.write(f"{summary['deferred_to_next']}개 연기\n")
        
        if VERBOSE_ASSEMBLY:
            print(f"📄 통계 요약 저장 완료: {txt_path}")
        
    except Exception as e:
        if DEBUG_ASSEMBLY:
            print(f"❌ 결과 저장 실패: {e}")


# 사용 예시
if __name__ == "__main__":
    print("Assembly Decoding Standalone Execution")

    parser = argparse.ArgumentParser(description="Assembly start heuristic standalone 실행")
    parser.add_argument("--config", default="config.yaml", help="standalone 실행 시 불러올 설정 파일 경로")
    parser.add_argument("--excel-path", default=None, help="입력 엑셀 경로")
    parser.add_argument("--method", default=None, help="실행할 heuristic method (comma-separated 가능)")
    parser.add_argument("--run-tag", default=None, help="결과 스냅샷을 저장할 run tag")
    parser.add_argument("--output-dir", default=None, help="run tag 결과를 저장할 디렉터리")
    parser.add_argument("--output-csv", default=None, help="단일 method 실행 시 메인 결과 CSV 저장 경로")
    parser.add_argument("--seed", type=int, default=42, help="random heuristic 재현용 시드")
    args = parser.parse_args()

    loaded_config = maybe_load_runtime_config(args.config)
    if loaded_config:
        print(f"✅ standalone config 로드: {loaded_config}")
    random.seed(args.seed)
    
    # 🆕 동적으로 조립착수일 계산
    excel_path, offset = get_test_settings()
    if args.excel_path:
        excel_path = args.excel_path
    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path)
    min_assembly_date = min(block.max_start_date for block in blocks)
    start_datetime = min_assembly_date + timedelta(days=offset)
    print(f"📅 동적 계산된 시작 날짜: {start_datetime.strftime('%Y-%m-%d')} (최소 조립착수일 + {offset}일)")
    
    # 🆕 선택 방식 설정 (config.yaml heuristic.method 우선)
    selection_methods = DEFAULT_SELECTION_METHODS
    try:
        runtime_cfg = get_runtime_config() or {}
        heuristic_cfg = runtime_cfg.get("heuristic") or {}
        method_raw = heuristic_cfg.get("method") if isinstance(heuristic_cfg, dict) else None
        if args.method:
            method_raw = args.method
        if method_raw:
            if isinstance(method_raw, str):
                requested = [m.strip().lower() for m in method_raw.split(",") if m.strip()]
            else:
                requested = [str(method_raw).strip().lower()]
            valid = {"priority", "random", "spt", "lpt", "seam_min"}
            requested = [m for m in requested if m in valid]
            if requested:
                selection_methods = requested
                print(f"✅ config.yaml heuristic.method 적용: {selection_methods}")
    except Exception:
        pass
    
    for method in selection_methods:
        print(f"\n{'='*60}")
        print(f"🔍 Selection Method: {method.upper()}")
        print(f"{'='*60}")

        output_csv_path = resolve_output_path(
            default_filename=f"assembly_decoding_{method}_results.csv",
            mode="assembly_start_heuristic",
            output_csv=args.output_csv if len(selection_methods) == 1 else None,
            output_dir=args.output_dir,
            run_tag=args.run_tag,
        )
        
        # Assembly Decoding 실행
        results, stats = run_assembly_decoding_sequence(
            excel_path=excel_path,  # 🔧 SNU 데이터셋으로 통일
            decoding_type="assembly",
            selection_method=method,
            max_days=10,
            start_date=start_datetime.strftime("%Y-%m-%d"), # 🔧 SNU 데이터셋 날짜로 수정
            date_offset=offset, # 오프셋 0으로 설정
            output_csv=str(output_csv_path),
            save_csv=True,
            save_detailed=True
        )
        legacy_path = mirror_to_legacy_path(output_csv_path, f"assembly_decoding_{method}_results.csv")
        
        print(f"📊 {method.upper()} 방식 결과:")
        print(f"   처리된 블록: {len(results)}개")
        print(f"   성공률: {stats['success_rate']:.1f}%")
        print(f"   총 primary 위반: {stats['total_violations']}개")
        print(f"   총 raw 이벤트: {stats.get('total_violations_raw', 0)}개")
        print(f"   메타/완화 이벤트: {stats.get('total_violations_meta', 0)}개")
        print(f"   INFO 이벤트: {stats.get('total_violations_info', 0)}개")
        print(f"   P/S 성공률: {stats['ps_success_rate']:.1f}%")
        print(f"   총 makespan: {stats['makespan_hours']:.2f}시간")
            
        print(f"   결과 파일: {output_csv_path}")
        if output_csv_path.resolve() != legacy_path.resolve():
            print(f"   레거시 복사본: {legacy_path}")
    
    print(f"\n✅ 선택 방식 실행 완료!")
    for method in selection_methods:
        print(f"   📄 {method.upper()} 방식: assembly_decoding_{method}_results.csv")
