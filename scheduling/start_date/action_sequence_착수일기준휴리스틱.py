# action_sequence.py

import random
import argparse
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional

from enhanced_environment.common.utils_core import (
    DataConverter,
    expand_rows_with_subassembly,
    get_line_group_and_workshop_code,
    summarize_violations,  # [AGENT-ADD] 제약 카운트/상세/완화 통합 요약
)
# [AGENT-ADD] main.py config.yaml 연동
from runtime_config import get_runtime_config
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.pbs_env import EnhancedPanelBlockShop
from enhanced_environment.models import BayType, ProcessStep, ConstraintViolation
from scheduling.common.process_schedule import (
    save_detailed_process_schedule as _save_detailed_process_schedule,
)
from scheduling.common.selection_rules import select_block_id  # [AGENT-ADD] 선택 규칙 공통화


# [AGENT-ADD] dict 기반 위반 로그를 ConstraintViolation로 변환
def _to_violation_objects(violations, block_id: Optional[int] = None) -> List[ConstraintViolation]:
    converted: List[ConstraintViolation] = []
    for v in violations or []:
        converted.append(
            ConstraintViolation(
                constraint_id=v.get('constraint_id', ''),
                message=v.get('message', ''),
                severity=v.get('severity', 'INFO'),
                block_id=block_id,
            )
        )
    return converted


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
        verbose=True,
        label="공정별 상세 스케줄",
    )


def create_actionmasking_schedule(
    excel_path: str,
    *,
    limit_days: Optional[int] = None,
    reset_bay_continuity: bool = False,
    reset_worktime_daily: bool = True,
):
    """
    Step-by-Step Action Masking 기반 스케줄 생성

    decoding_type:
    - "random": 현재 선택 가능한 블록들 중에서 랜덤 선택 (균등 확률)
    - "excel": 엑셀 순번에 따라 선택 (가능한 경우)

    Args:
        excel_path: 입력 엑셀 경로
        limit_days: 앞에서부터 처리할 일수 제한 (None이면 전체 처리)
        reset_bay_continuity: True면 날짜마다 베이 연속성까지 완전 리셋
        reset_worktime_daily: True면 하루 시작 시 베이 작업시간을 0으로 리셋

    Returns:
        (all_schedule_data, bay_analysis_by_date, step_info_by_date)
    """
    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path)
    constraint_config = ConstraintConfig()
    constraint_config.enable_emergency_mode = True
    # 🔧 용량 제약조건 비활성화 (디버깅만, 실제 차단 없음)  
    constraint_config.enable_p5_8_weekday_capacity = False
    constraint_config.enable_p5_16_hot_season_capacity = False
    constraint_config.enable_p5_15_holiday_shift = False       # P5#15: 명절 전날 야간 없음 - 구현하되 기본 OFF
    constraint_config.enable_p5_9_block_count_check = False       # P5#9: 72심 초과시 17블록 체크 - 구현하되 기본 OFF
    constraint_config.enable_p5_10_weekend_capacity = False       # P5#10: 주말 심수 용량 (45심) - 구현하되 기본 OFF

    # ✅ 실제 데이터의 첫 번째 날짜를 환경 시작 시간으로 사용
    earliest_date = min(block.max_start_date for block in blocks)
    actual_start_time = earliest_date.replace(hour=8, minute=0, second=0, microsecond=0)

    # ✅ 환경 초기화
    env = EnhancedPanelBlockShop(
        blocks=blocks,
        start_time=actual_start_time,
        constraint_config=constraint_config,
        metadata=metadata
    )
    print(f"✅ 실제 데이터 날짜 기준 환경 초기화: {actual_start_time.strftime('%Y-%m-%d %H:%M')} "
          f"(요일: {['월','화','수','목','금','토','일'][actual_start_time.weekday()]})")
    print(f"✅ 환경 초기화 완료: {len(blocks)}개 블록")

    # ✅ 날짜별 그룹핑
    date_groups = {}
    for block in blocks:
        date_key = block.max_start_date.strftime('%Y%m%d')
        date_groups.setdefault(date_key, []).append(block)

    print(f"📅 처리 기간: {sorted(date_groups.keys())[0]} ~ {sorted(date_groups.keys())[-1]} "
          f"({len(date_groups)}일)")

    all_schedule_data = []
    bay_analysis_by_date = {}
    step_info_by_date = {}

    # ✅ Step-by-Step 처리 (날짜별)
    for date_idx, date_key in enumerate(sorted(date_groups.keys())):
        # ✅ 옵션: 처리 일수 제한
        if limit_days is not None and date_idx >= limit_days:
            print(f"🔍 limit_days={limit_days} 설정으로 {date_key} 이후는 스킵")
            break
            
        blocks_in_date = date_groups[date_key]
        print(f"\n📅 {date_key} 처리 시작 ({len(blocks_in_date)}개 블록):")

        # ✅ 리셋 정책 명시: P/S는 날짜마다 리셋, 베이 연속성은 기본 리셋
        env.ps_manager.reset()

        if reset_bay_continuity:
            env.bay_tracker.reset()  # 연속성·작업시간 모두 리셋
            print("   🔄 베이 연속성까지 완전 리셋 (reset_bay_continuity=True)")
        else:
            # 연속 배치 오탐 방지를 위해 최소한 연속 카운터는 리셋
            env.bay_tracker.reset_consecutive_counters()
            if reset_worktime_daily:
                env.bay_tracker.bay_35a_worktime = 0.0
                env.bay_tracker.bay_36b_worktime = 0.0
            print(
                f"   🔄 베이 작업시간 리셋={reset_worktime_daily}, 연속성 카운터는 일일 리셋"
            )
        
        current_date = datetime(int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8]), 8, 0)
        
        # 🔥 블록 딕셔너리 설정 (패턴 체크를 위해 필요)
        blocks_dict = {block.block_id: block for block in blocks_in_date}
        env.constraint_checker.set_blocks_dict(blocks_dict)
        print(f"   🔍 블록 딕셔너리 설정 완료: {len(blocks_dict)}개 블록")
        
        # 🔥 디버깅: 날짜 및 용량 정보 확인
        is_weekend = env.calendar_manager.is_weekend(current_date)
        weekday_name = ['월', '화', '수', '목', '금', '토', '일'][current_date.weekday()]
        print(f"   📅 날짜 디버깅: {current_date.strftime('%Y-%m-%d')} ({weekday_name})")
        print(f"   🔍 주말 판정: {is_weekend} (weekday={current_date.weekday()})")
        
        # 🔥 베이 연속성 상태 유지 확인
        print(f"   🏭 베이 연속성 상태 (전날로부터 유지):")
        print(f"      A베이 연속: {env.bay_tracker.bay_35a_consecutive_count}개")
        print(f"      B베이 연속: {env.bay_tracker.bay_36b_consecutive_count}개") 
        print(f"      마지막 베이: {env.bay_tracker.last_bay_assignment}")
        print(f"      작업시간: A={env.bay_tracker.bay_35a_worktime/3600:.1f}h, B={env.bay_tracker.bay_36b_worktime/3600:.1f}h (리셋됨)")
        
        if is_weekend:
            env.capacity_tracker.reset_weekend()
            capacity_limit = 45
            print(f"   🏃 주말 모드: 용량 한계 {capacity_limit}심")
        else:
            env.capacity_tracker.reset_daily()
            capacity_limit = 75
            print(f"   💼 평일 모드: 용량 한계 {capacity_limit}심")
        
        # 🔥 삭제: 중복된 worktime 리셋 (reset()에서 이미 처리됨)
        print(f"   ✅ 베이 상태 완전 리셋 완료")

        daily_step_info = []
        daily_bay_analysis = []

        try:
            date_start_time = datetime(int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8]), 8, 0)
            current_time = date_start_time
            selected_blocks = []
            final_sequence = []
            last_assembly_type = None
            current_bay_assignments = {}  # 🔥 베이 할당 추적
            bay_analysis_cache = {}       # 🔥 베이 분석 정보 저장
            masking_violation_map = {}    # [AGENT-ADD] 마스킹 단계 위반 로그

            print(f"   🔍 Step-by-Step Action Masking 시작 (시작 시간: {current_time.strftime('%H:%M')})")

            # [AGENT-ADD] config.yaml heuristic.method를 착수일 경로에도 반영
            runtime_cfg = get_runtime_config() or {}
            heuristic_cfg = runtime_cfg.get("heuristic") or {}
            method_raw = heuristic_cfg.get("method") if isinstance(heuristic_cfg, dict) else None
            selection_method = None
            decoding_type = "random"
            if method_raw:
                if isinstance(method_raw, str):
                    method_key = method_raw.strip().lower()
                elif isinstance(method_raw, list) and method_raw:
                    method_key = str(method_raw[0]).strip().lower()
                else:
                    method_key = str(method_raw).strip().lower()
                if method_key in ("excel", "엑셀", "실적", "performance_replay"):
                    decoding_type = "excel"
                elif method_key in ("random", "rand"):
                    decoding_type = "random"
                elif method_key in ("spt", "lpt", "seam_min", "seammin", "priority"):
                    selection_method = "seam_min" if method_key in ("seammin",) else method_key
                    decoding_type = "selection_rules"

            # ✅ 모든 블록이 선택될 때까지 반복
            while len(selected_blocks) < len(blocks_in_date):
                available_ids, violations, block_analysis = env.constraint_checker.get_next_available_blocks(
                    blocks_in_date, current_time, selected_blocks, last_assembly_type
                )
                if not available_ids:
                    print(f"   ❌ 선택 가능한 블록이 없음 ({len(selected_blocks)}/{len(blocks_in_date)})")
                    break

                # 스텝별 상세 정보 기록용
                step_info = {
                    'step': len(selected_blocks),
                    'current_time': current_time.strftime('%H:%M'),
                    'selected_count': len(selected_blocks),
                    'total_blocks': len(blocks_in_date),
                    'available_block_ids': available_ids.copy(),
                    'available_count': len(available_ids),
                    'excluded_count': len(blocks_in_date) - len(selected_blocks) - len(available_ids),
                    'last_assembly_type': last_assembly_type.value if last_assembly_type else None,
                    'block_analysis': block_analysis.copy(),
                    'violations': [str(v) for v in violations]
                }

                # 블록 선택
                if decoding_type == "selection_rules" and selection_method:
                    selected_id, method = select_block_id(
                        available_ids=available_ids,
                        blocks_dict=blocks_dict,
                        selection_method=selection_method,
                    )
                elif decoding_type == "random":
                    selected_id = random.choice(available_ids)
                    method = f"랜덤 선택 ({len(available_ids)}개 중)"
                elif decoding_type == "excel":
                    avail_blocks = [b for b in blocks_in_date if b.block_id in available_ids]
                    excel_sorted = sorted(avail_blocks, key=lambda b: getattr(b, 'sequence_number', 999))
                    selected_id = excel_sorted[0].block_id
                    method = f"엑셀 순번 기반 ({len(available_ids)}개 중)"
                else:
                    selected_id = available_ids[0]
                    method = "기본 선택"

                selected_block = next(b for b in blocks_in_date if b.block_id == selected_id)
                # [AGENT-ADD] 마스킹 단계 위반을 블록별로 저장 (뒤에서 CSV 집계 시 사용)
                dedup = {}
                for v in violations:
                    cid = getattr(v, 'constraint_id', '')
                    msg = getattr(v, 'message', '')
                    sev = getattr(v, 'severity', 'INFO')
                    # [AGENT-EDIT] 완화 이벤트 집계를 위해 RELAX_STAGE도 보존
                    if cid in ('RELAX_STAGE', 'RELAX_STAGE_DETAIL'):
                        key = (cid, msg)
                    else:
                        key = (cid, sev)
                    if key not in dedup:
                        dedup[key] = {
                            'constraint_id': cid,
                            'message': msg,
                            'severity': sev
                        }
                masking_violation_map[selected_id] = list(dedup.values())

                # 🔥 Step-by-step 베이 할당: 원본과 동일하게 각 블록마다 상태 누적
                # 🔍 디버깅: 베이 할당 전 상태 출력
                # print(f"   📊 Step {len(selected_blocks)+1}: 블록 {selected_id} 할당 전")
                # print(f"      A베이: 시간={env.bay_tracker.bay_35a_worktime/3600:.1f}h, 연속={env.bay_tracker.bay_35a_consecutive_count}")
                # print(f"      B베이: 시간={env.bay_tracker.bay_36b_worktime/3600:.1f}h, 연속={env.bay_tracker.bay_36b_consecutive_count}")
                # print(f"      마지막베이: {env.bay_tracker.last_bay_assignment}")
                
                assigned_bay, bay_analysis = env._auto_assign_bay(selected_block, return_analysis=True)
                current_bay_assignments[selected_id] = assigned_bay
                
                # 🔥 딥 카피로 객체 참조 공유 문제 해결
                import copy
                bay_analysis_copy = copy.deepcopy(bay_analysis)
                bay_analysis_cache[selected_id] = bay_analysis_copy
                
                # 🔥 베이 할당 후 연속성 제약 위반 체크 (물리적 제약 강제 할당 시에도)
                consecutive_violation_msg = None
                if assigned_bay == BayType.BAY_35A and env.bay_tracker.bay_35a_consecutive_count > 1:
                    consecutive_violation_msg = f"⚠️ P7#7 위반: A베이 {env.bay_tracker.bay_35a_consecutive_count}개 연속 (최대 1개까지)"
                elif assigned_bay == BayType.BAY_36B and env.bay_tracker.bay_36b_consecutive_count > 2:
                    consecutive_violation_msg = f"⚠️ P7#7 위반: B베이 {env.bay_tracker.bay_36b_consecutive_count}개 연속 (최대 2개까지)"
                
                # 🔍 디버깅: 베이 할당 후 상태 출력
                # print(f"      ➡️ 할당결과: {assigned_bay.value} 베이")
                # print(f"      A베이: 시간={env.bay_tracker.bay_35a_worktime/3600:.1f}h, 연속={env.bay_tracker.bay_35a_consecutive_count}")
                # print(f"      B베이: 시간={env.bay_tracker.bay_36b_worktime/3600:.1f}h, 연속={env.bay_tracker.bay_36b_consecutive_count}")
                # print(f"      할당근거: {bay_analysis.get('final_reason', 'N/A')}")
                
                # 🚨 연속성 위반 경고 출력 및 저장
                if consecutive_violation_msg:
                    print(f"      {consecutive_violation_msg}")
                    # 🔥 딥 카피된 객체에 위반 정보 추가
                    bay_analysis_copy['consecutive_violation'] = consecutive_violation_msg
                    bay_analysis_copy['final_reason'] = f"{bay_analysis_copy.get('final_reason', '')} + {consecutive_violation_msg}"
                    print(f"      🔍 디버깅: 블록 {selected_id}에 연속성 위반 정보 저장: {consecutive_violation_msg}")
                
                # ✅ 베이 할당이 완료되면 bay_tracker 상태가 자동 업데이트됨
                # - 연속성 카운터 (P7#7)
                # - worktime (P7#1) 
                # - 블록 카운터
                # 이제 다음 블록 처리 시 이 상태가 모두 반영됨

                # 스텝 정보 업데이트
                step_info.update({
                    'selected_block_id': selected_id,
                    'selected_assembly_type': selected_block.assembly_type.value,
                    'selection_method': method,
                    'decoding_type': decoding_type,
                    'assigned_bay': assigned_bay.value
                })
                daily_step_info.append(step_info)

                # ✅ makespan 계산으로 시간 갱신
                try:
                    makespan_sec, detail = env.calculate_makespan(
                        sequence=final_sequence + [selected_id],
                        branch_assignments=current_bay_assignments
                    )
                    bs = next((b for b in detail['block_schedules'] if b['block_id'] == selected_id), None)
                    if bs:
                        current_time = date_start_time + timedelta(seconds=bs['start_seconds'])
                    else:
                        current_time += timedelta(minutes=sum(selected_block.processing_times))
                except Exception as ct_err:
                    print(f"   ⚠️ CT 계산 실패: {ct_err}")
                    current_time += timedelta(minutes=sum(selected_block.processing_times))

                selected_blocks.append(selected_id)
                final_sequence.append(selected_id)
                last_assembly_type = selected_block.assembly_type

            # 🔍 디버깅: 최종 베이 할당 패턴 출력
            print(f"\n   📊 최종 베이 할당 패턴:")
            bay_pattern = []
            for i, bid in enumerate(final_sequence):
                bay = current_bay_assignments[bid].value
                bay_pattern.append(bay)
                print(f"      {i+1:2d}. 블록 {bid} → {bay}")
            print(f"   패턴: {' → '.join(bay_pattern)}")
            
            # A베이 연속 카운트
            consecutive_a_count = 0
            max_consecutive_a = 0
            for bay in bay_pattern:
                if bay == '35A':
                    consecutive_a_count += 1
                    max_consecutive_a = max(max_consecutive_a, consecutive_a_count)
                else:
                    consecutive_a_count = 0
            print(f"   A베이 최대 연속: {max_consecutive_a}개")

            # 스텝별 상세 정보 저장
            step_info_by_date[date_key] = (daily_step_info, blocks_in_date)

            # ✅ 스케줄 데이터 생성 (저장된 베이 할당과 분석 정보 재사용)
            schedule_data = []
            for i, bid in enumerate(final_sequence):
                block = next(b for b in blocks_in_date if b.block_id == bid)
                
                # 🔥 저장된 베이 할당과 분석 정보 재사용 (추가 호출 없음!)
                bay = current_bay_assignments[bid]
                analysis = bay_analysis_cache[bid]
                daily_bay_analysis.append(analysis)

                # 🔥 베이 분석에서 연속성 위반 정보 추가 (이미 검증된 것만)
                consecutive_violation = analysis.get('consecutive_violation')

                line_group, assembly_code = get_line_group_and_workshop_code(block)

                result = {
                    'date': date_key,
                    'am_sequence': i + 1,
                    'block_id': block.block_id,
                    'block_name': block.block_name,
                    'port_starboard': block.port_starboard.value,
                    'assembly_type': block.assembly_type.value,
                    'line_group': line_group,
                    'assembly_workshop_code': assembly_code,
                    'block_assembly_date': block.assembly_start_date.strftime('%Y-%m-%d') if hasattr(block, 'assembly_start_date') and block.assembly_start_date else '',
                    'width_m': round(block.width, 1),
                    'longi_count': block.longi_count,
                    'seam_count': block.seam_count,
                    'c_seam_count': getattr(block, 'c_seam_count', 0),
                    ################################################################################################################################################################################################
                    # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                    ################################################################################################################################################################################################
                    'has_curved_plate': getattr(block, 'has_curved_plate', False),
                    'main_plate_count': block.main_plate_count,
                    'original_bay': bay.value,
                    'assigned_bay': bay.value,
                    'bay_changed': False,
                    'total_time_min': round(sum(block.processing_times), 1),
                    'material_ready': block.material_ready,
                    'is_fab': block.is_fab,
                    'is_draft': block.is_draft,
                    'is_cross_seam': block.is_cross_seam,
                    'material_type': block.material_type.value,
                    'is_subassembly': getattr(block, 'is_subassembly', False),
                    'consecutive_violation': consecutive_violation or '',
                    'method': decoding_type.upper(),
                    'excel_sequence': getattr(block, 'sequence_number', None),
                }
################################################################################################################################################################################################
# fix: 별판(통합 블록) CSV 확장 정보 저장
################################################################################################################################################################################################
                subassembly_expansion = []
                for original in getattr(block, 'subassembly_original_blocks', [block]):
                    sub_line_group, sub_assembly_code = get_line_group_and_workshop_code(original)
                    subassembly_expansion.append({
                        'block_id': original.block_id,
                        'block_name': getattr(original, 'block_name', f'BLK_{original.block_id}'),
                        'port_starboard': original.port_starboard.value,
                        'assembly_type': original.assembly_type.value,
                        'line_group': sub_line_group,
                        'assembly_workshop_code': sub_assembly_code,
                        'block_assembly_date': original.assembly_start_date.strftime('%Y-%m-%d') if getattr(original, 'assembly_start_date', None) else '',
                        'width_m': round(original.width, 1),
                        'longi_count': original.longi_count,
                        'seam_count': original.seam_count,
                        'c_seam_count': getattr(original, 'c_seam_count', 0),
                        'has_curved_plate': getattr(original, 'has_curved_plate', False),
                        'main_plate_count': original.main_plate_count
                    })
                result['subassembly_expansion'] = subassembly_expansion
                for j, pt in enumerate(block.processing_times):
                    result[f'process_{j+1}_time_min'] = round(pt, 1)
                schedule_data.append(result)

            bay_analysis_by_date[date_key] = daily_bay_analysis

            # ✅ 전체 makespan 및 시간정보 업데이트 (저장된 베이 할당 재사용)
            total_sec, detailed = env.calculate_makespan(final_sequence, current_bay_assignments)
            total_min = total_sec / 60
            total_hr = total_min / 60
            try:
                y, m, d = int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8])
                date_start_time = datetime(y, m, d, 8, 0)
            except:
                date_start_time = env.start_time
            completion_time = date_start_time + timedelta(minutes=total_min)
            
            # 🆕 공정별 상세 스케줄링 CSV 생성
            try:
                save_detailed_process_schedule(
                    date_key=date_key,
                    sequence=final_sequence,
                    bay_assignments=current_bay_assignments,
                    ct_tables=detailed['ct_tables'],
                    blocks_dict=blocks_dict,
                    date_start_time=date_start_time
                )
            except Exception as process_err:
                print(f"   ⚠️ 공정별 상세 스케줄 생성 실패: {process_err}")
            
            # 🔥 정확한 시작 시간으로 제약조건 검증 수행
            for rec in schedule_data:
                bs = next((b for b in detailed['block_schedules'] if b['block_id'] == rec['block_id']), None)
                if bs:
                    st = date_start_time + timedelta(seconds=bs['start_seconds'])
                    et = date_start_time + timedelta(seconds=bs['end_seconds'])
                    
                    # 🔥 실제 시작 시간으로 제약조건 검증
                    block = next(b for b in blocks_in_date if b.block_id == rec['block_id'])
                    bay = current_bay_assignments[rec['block_id']]
                    violations = []
                    # [AGENT-ADD] 마스킹 단계에서 이미 발생한 위반을 반영
                    violations.extend(masking_violation_map.get(rec['block_id'], []))
                    
                    try:
                        # 🆕 실제 머신 2번(전면SAW) 시작 시간 계산 (CT 테이블 기반)
                        actual_machine_2_start_time = None
                        if bs:
                            try:
                                # CT 테이블에서 전면SAW 시작 시간 계산
                                seq_idx = final_sequence.index(rec['block_id'])
                                ct_common = detailed['ct_tables']['ct_common']
                                
                                # 전면SAW 완료 시간 (CT 테이블 1-based 인덱스)
                                saw_end_seconds = ct_common[seq_idx + 1, 2]  # 2번 컬럼: 전면SAW 완료
                                
                                # 전면SAW 시작 시간 = 완료 시간 - 처리 시간
                                saw_duration_seconds = block.processing_times[1] * 60  # 전면SAW 처리 시간
                                machine_2_start_seconds = saw_end_seconds - saw_duration_seconds
                                
                                actual_machine_2_start_time = date_start_time + timedelta(seconds=machine_2_start_seconds)
                                
                                # 디버깅: 계산된 시간 확인
                                print(f"      🔧 착수일기준 BLK_{rec['block_id']}: 계산된 머신2번 시작 = {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                                
                                # 날짜 전환 체크 (22시 이후 → 다음날 08시)
                                if actual_machine_2_start_time.hour >= 22:
                                    next_date = actual_machine_2_start_time.date() + timedelta(days=1)
                                    from datetime import time
                                    actual_machine_2_start_time = datetime.combine(next_date, time(8, 0))
                                    print(f"      📅 날짜 전환: 22시 이후 → {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                                
                            except Exception as calc_err:
                                # 계산 실패 시 기본 시간 사용
                                actual_machine_2_start_time = st
                        
                        # 🔥 수정: 블록이 속한 원래 날짜 기준으로 제약조건 검증
                        # (실제 시작 시간이 다음날이어도 원래 날짜의 용량 한계 적용)
                        original_date = datetime(int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8]), 8, 0)

                        # SAW 제약조건 - 실제 머신 2번 시작 시간 사용 + 시퀀싱 날짜 고정
                        saw = env._validate_saw_constraints_realtime(
                            block,
                            st,
                            actual_machine_2_start_time,
                        )

                        others = env._validate_all_constraints_realtime_action(block, bay, original_date, actual_machine_2_start_time)
                        # P6 상세/3베이 중복 제외 (기존 로직 유지)
                        filtered = [v for v in others if not v.constraint_id.startswith('P6#') and v.constraint_id != 'P7#7']
                        combined = list(saw) + filtered
                        for v in combined:
                            violations.append({
                                'constraint_id': v.constraint_id,
                                'message': v.message,
                                'severity': getattr(v, 'severity', 'INFO')
                            })
                        
                        # 연속성 위반 정보 추가
                        if 'consecutive_violation' in bay_analysis_cache[rec['block_id']]:
                            violations.append({
                                'constraint_id': 'P7#7',
                                'message': bay_analysis_cache[rec['block_id']]['consecutive_violation'],
                                'severity': 'WARNING'
                            })
                        
                        # ✅ 완화 메시지는 정보성으로 강등 (중복 ERROR 방지)
                        for v in violations:
                            msg = v.get('message', '')
                            if msg.startswith('[RELAX') or msg.startswith('착수일고정-') or '완화' in msg:
                                v['severity'] = 'INFO'
                            
                    except Exception as val_err:
                        print(f"      ⚠️ 제약 검증 실패 (블록 {rec['block_id']}): {val_err}")
                    
                    ##################################################################
                    # [AGENT-ADD] 비상 선택(EMERGENCY_RELEASE) 시 어떤 제약을 위반했는지 사후 재검증
                    ##################################################################
                    if any(v['constraint_id'] == 'EMERGENCY_RELEASE' for v in violations):
                        cc = env.constraint_checker
                        # 중복 방지 세트
                        existing_keys = {(v['constraint_id'], v['message']) for v in violations}
                        check_specs = [
                            ('ROUTING_C_SEAM_SPACING', cc._check_c_seam_spacing, [block, final_sequence]),
                            ('ROUTING_CURVED_SPACING', cc._check_curved_plate_spacing, [block, final_sequence]),
                            ('ROUTING_HIGH_SEAM_SPACING', cc._check_high_seam_spacing, [block, final_sequence]),
                            ('ROUTING_WORKSHOP_ORDER', cc._check_workshop_order_constraint, [block, blocks_in_date, final_sequence]),
                            ('P6#4', cc._check_cross_seam_constraint, [block, final_sequence, blocks_in_date]),
                        ]
                        for cid, fn, args in check_specs:
                            try:
                                ok, reason = fn(*args)
                            except Exception as e:
                                ok, reason = False, f"검증 오류: {e}"
                            if ok:
                                continue
                            key = (cid, reason)
                            if key in existing_keys:
                                continue
                            violations.append({
                                'constraint_id': cid,
                                'message': reason,
                                'severity': 'ERROR'
                            })
                            existing_keys.add(key)
                        # SAW 시간 제약 별도 검사
                        try:
                            ok, reason = cc._check_saw_time_constraint(
                                block,
                                st,  # 실제 시작 시간 기준
                                previous_machine_state=None,
                                current_bay_assignments=current_bay_assignments,
                                current_day_selected_blocks=final_sequence
                            )
                        except Exception as e:
                            ok, reason = False, f"검증 오류: {e}"
                        if not ok:
                            key = ("P6#1,2,3", reason)
                            if key not in existing_keys:
                                violations.append({
                                    'constraint_id': "P6#1,2,3",
                                    'message': reason,
                                    'severity': 'ERROR'
                                })
                                existing_keys.add(key)
                    
                    # [AGENT-EDIT] 제약 카운트/상세/완화 요약 통일
                    summary = summarize_violations(
                        _to_violation_objects(violations, block_id=rec['block_id']),
                        keep_info=True,
                        include_guard=False,
                    )
                    rec.update(summary)
                    
                else:
                    st = date_start_time
                    et = date_start_time + timedelta(minutes=rec['total_time_min'])
                    rec.update({
                        'violations': 0,
                        'violation_details': [],
                        'violation_severity': [],
                        'constraint_ids': [],
                        'info_count': 0,
                        'info_details': [],
                        'info_constraint_ids': [],
                        'info_severity': [],
                        'relax_constraint_count': 0,
                        'relax_constraint_ids': [],
                        'relax_event_count': 0,
                    })
                    
                rec.update({
                    'start_time': st.strftime('%Y-%m-%d %H:%M'),
                    'end_time': et.strftime('%Y-%m-%d %H:%M'),
                    'date_start_time': date_start_time.strftime('%Y-%m-%d %H:%M'),
                    'date_completion_time': completion_time.strftime('%Y-%m-%d %H:%M'),
                    'makespan_minutes': round(total_min, 1),
                    'makespan_hours': round(total_hr, 2)
                })

                # ✅ 완료 스텝 기록: 이후 블록 라우팅/연속성 검증용 히스토리 반영
                try:
                    ps = ProcessStep(
                        block_id=rec['block_id'],
                        process_num=1,
                        bay_type=bay,
                        start_time=st,
                        end_time=et,
                        processing_time=sum(block.processing_times) * 60,
                        completion_time=sum(block.processing_times) * 60,
                    )
                    env.completed_steps.append(ps)
                except Exception:
                    pass
            all_schedule_data.extend(schedule_data)

        except Exception as e:
            print(f"   ❌ {date_key} Action Masking 처리 실패: {e}")
            raise

    print(f"\n✅ Step-by-Step Action Masking 스케줄 생성 완료")
    print(f"📄 스케줄 데이터: {len(all_schedule_data)}개 레코드")
################################################################################################################################################################################################
# fix: 반환 전 별판 행 확장
################################################################################################################################################################################################
    all_schedule_data = expand_rows_with_subassembly(all_schedule_data)
    return all_schedule_data, bay_analysis_by_date, step_info_by_date


# 독립 실행 로직
if __name__ == "__main__":
    print("Action Masking 기반 스케줄링 독립 실행")
    print("=" * 60)

    parser = argparse.ArgumentParser(description="Action Masking 휴리스틱 실행")
    parser.add_argument(
        "--excel-path",
        default="environment/판넬 블록 데이터셋_250618_SNU.xlsx",
        help="입력 엑셀 경로",
    )
    parser.add_argument(
        "--limit-days",
        type=int,
        default=None,
        help="앞에서부터 처리할 일수 제한 (기본: 전체 처리)",
    )
    parser.add_argument(
        "--reset-bay-continuity",
        action="store_true",
        help="날짜마다 베이 연속성까지 완전 리셋",
    )
    parser.add_argument(
        "--no-reset-worktime",
        action="store_true",
        help="True이면 하루 시작 시 베이 작업시간 리셋을 건너뜀",
    )

    args = parser.parse_args()

    # 🆕 test.py 설정 가져오기 (circular import 방지, CLI가 우선)
    try:
        import sys
        if 'test' in sys.modules:
            test_module = sys.modules['test']
            test_excel = getattr(test_module, 'EXCEL_PATH', args.excel_path)
            if args.excel_path == parser.get_default("excel_path"):
                args.excel_path = test_excel
            print(f"✅ test.py 설정 로드: {args.excel_path}")
    except Exception:
        if args.excel_path == parser.get_default("excel_path"):
            print(f"⚠️ test.py 설정 사용 불가, 기본값 사용: {args.excel_path}")

    # [AGENT-ADD] config.yaml data.excel_path가 있으면 기본값을 덮어씀 (CLI가 우선)
    if args.excel_path == parser.get_default("excel_path"):
        runtime_cfg = get_runtime_config() or {}
        if isinstance(runtime_cfg, dict):
            data_cfg = runtime_cfg.get("data") or {}
            cfg_path = data_cfg.get("excel_path")
            if cfg_path:
                args.excel_path = str(cfg_path)
                print(f"✅ config.yaml data.excel_path 적용: {args.excel_path}")

    try:
        print(f"\n🎯 Action Masking 기반 스케줄링 실행 중...")
        
        # 1. Action Masking 스케줄링 실행
        am_results, bay_analysis_by_date, step_info_by_date = create_actionmasking_schedule(
            args.excel_path,
            limit_days=args.limit_days,
            reset_bay_continuity=args.reset_bay_continuity,
            reset_worktime_daily=not args.no_reset_worktime,
        )

################################################################################################################################################################################################
# fix: 별판 복수 행으로 확장 (CSV 기록 전에 적용)
################################################################################################################################################################################################
        am_results = expand_rows_with_subassembly(am_results)

        # 2. 메인 결과 CSV 저장 (test.py와 동일한 방식)
        import pandas as pd
        df = pd.DataFrame(am_results)
        csv_filename = 'action_masking_standalone_results.csv'
        df.to_csv(csv_filename, index=False, encoding='utf-8-sig')
        
        # 3. 상세 분석 CSV 저장 (test.py와 동일)
        from utils.csv_save import save_detailed_masking_info, save_detailed_bay_selection_info
        
        print(f"\n🔍 상세 분석 CSV 저장 중...")
        
        # 베이 선택 상세 분석 저장
        save_detailed_bay_selection_info(bay_analysis_by_date)
        
        # 스텝별 상세 분석 저장 (날짜별)
        for date_key, (daily_step_info, blocks_in_date) in step_info_by_date.items():
            if daily_step_info:  # 스텝 정보가 있는 경우만
                save_detailed_masking_info(date_key, daily_step_info, blocks_in_date)
                print(f"📄 Action Masking 스텝 분석: detailed_actionmasking_schedule_info_{date_key}.csv")
        
        # 4. 통계 출력
        print(f"\n📊 Action Masking 방식 결과:")
        print(f"   처리된 블록: {len(am_results)}개")
        
        # Makespan 계산 (날짜별 최대 makespan 합산)
        makespan_by_date = {}
        for result in am_results:
            date = result.get('date', 'N/A')
            makespan = result.get('makespan_hours', 0)
            if date not in makespan_by_date or makespan > makespan_by_date[date]:
                makespan_by_date[date] = makespan
        
        total_makespan = sum(makespan_by_date.values())
        total_violations = sum(r.get('violations', 0) for r in am_results)
        
        print(f"   총 makespan: {total_makespan:.2f}시간")
        print(f"   총 위반: {total_violations}개")
        
        # 데이터 로딩해서 성공률 계산
        from enhanced_environment.common.utils_core import DataConverter
        blocks, metadata = DataConverter.excel_to_blocks_with_metadata(args.excel_path)
        print(f"   성공률: {len(am_results) / len(blocks) * 100:.1f}%")
        
        # 베이 분포
        bay_stats = {}
        for result in am_results:
            bay = result.get('assigned_bay', 'N/A')
            bay_stats[bay] = bay_stats.get(bay, 0) + 1
        
        print(f"   베이 분포:")
        for bay, count in bay_stats.items():
            percentage = count / len(am_results) * 100 if am_results else 0
            print(f"     {bay}: {count}개 ({percentage:.1f}%)")
        
        print(f"\n📄 메인 결과: {csv_filename}")
        print(f"📄 베이 분석: detailed_actionmasking_bayselect_info_YYYYMMDD.csv")
        print(f"✅ Action Masking 기반 스케줄링 완료!")
        
    except Exception as e:
        print(f"❌ Action Masking 기반 스케줄링 실패: {e}")
        import traceback
        traceback.print_exc()
