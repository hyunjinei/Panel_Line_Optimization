# excel_sequence.py

import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd

from enhanced_environment.models import BayType, ConstraintViolation, AssemblyType, ProcessStep
from enhanced_environment.constraints import get_basic_constraints_only_config
from enhanced_environment.pbs_env import EnhancedPanelBlockShop
from enhanced_environment.common.utils_core import (
    DataConverter,
    get_line_group_and_workshop_code,
    expand_rows_with_subassembly,
    summarize_violations,  # [AGENT-ADD] 제약 카운트/상세/완화 통합 요약
)
from scheduling.common.defaults import DEFAULT_EXCEL_PATH  # [AGENT-ADD] 기본 경로 통합
# [AGENT-ADD] main.py config.yaml 연동
from runtime_config import get_runtime_config

# ✅ 순번 시트 우선순위 (환경 변수 EXCEL_PLAN_SHEET_PRIORITY로 재정의 가능)
_PLAN_PRIORITY_ENV = os.getenv("EXCEL_PLAN_SHEET_PRIORITY")
if _PLAN_PRIORITY_ENV:
    PLAN_SHEET_PRIORITY: Tuple[str, ...] = tuple(
        part.strip() for part in _PLAN_PRIORITY_ENV.split(",") if part.strip()
    ) or ("수기계획", "CP")
else:
    PLAN_SHEET_PRIORITY = ("수기계획", "CP")


def _normalise_plan_column(text: object) -> str:
    """간단한 문자열 정규화 (양 끝 공백 제거, NaN → 빈 문자열)."""
    if text is None:
        return ""
    if isinstance(text, float) and pd.isna(text):
        return ""
    return str(text).strip()


#################################################################################################################################################
###############                                            완화 모드                                                               ###############  
#################################################################################################################################################
def _parse_plan_date(value: object) -> Optional[datetime]:
    """수기계획/CP 시트의 착수일 값을 datetime으로 변환."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    try:
        # EnhancedEnvironment DataConverter 유틸 활용 (YYYYMMDD, Excel serial 등 대응)
        return DataConverter._parse_date_format(value)
    except Exception:
        pass

    # 문자열 → datetime 시도
    text = str(value).strip()
    if not text:
        return None

    # 8자리 숫자 문자열로 들어오는 경우 (예: 20250901)
    if text.isdigit() and len(text) == 8:
        try:
            year = int(text[:4])
            month = int(text[4:6])
            day = int(text[6:8])
            return datetime(year, month, day, 8, 0, 0)
        except Exception:
            return None

    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except Exception:
        return None


def apply_plan_sequence_from_excel(
    blocks: List,
    excel_path: str,
    plan_sheet: Optional[str] = None,
    plan_priority: Optional[Tuple[str, ...]] = None,
    suppress_missing_message: bool = False,
) -> Optional[Tuple[str, ...]]:
    """
    외부 순번 시트를 읽어 블록 객체의 sequence_number 및 관련 정보를 업데이트.

    Args:
        blocks: EnhancedBlock 리스트
        excel_path: 참조할 엑셀 파일 경로
        plan_sheet: 명시적으로 사용할 시트명 (우선 적용)
        plan_priority: 우선순위 시트 리스트 (기본값 PLAN_SHEET_PRIORITY)
        suppress_missing_message: 적용 가능한 시트가 없을 때 안내 메시지를 숨길지 여부 (기본 False)

    Returns:
        사용된 시트명 목록 (없으면 None)
    """
    if not blocks:
        return None

    priority: List[str] = []
    if plan_sheet:
        priority.append(plan_sheet)

    for candidate in (plan_priority or PLAN_SHEET_PRIORITY):
        if candidate and candidate not in priority:
            priority.append(candidate)

    try:
        workbook = pd.ExcelFile(excel_path)
    except Exception as exc:  # pragma: no cover - 파일 문제는 런타임에서만 발생
        print(f"⚠️ 순번 시트를 읽지 못했습니다: {exc}")
        return None

    plan_mapping: Dict[Tuple[str, ...], List[Dict]] = defaultdict(list)
    sheet_stats: Dict[str, Dict[str, int]] = {}
    max_sequence_seen = 0

    for sheet_index, sheet_name in enumerate(priority):
        if sheet_name not in workbook.sheet_names:
            continue

        temp_df = workbook.parse(sheet_name)
        temp_df.columns = [str(col).strip() for col in temp_df.columns]

        if "블록번호" not in temp_df.columns:
            continue

        # 순번 컬럼이 없으면 행 순서를 그대로 순번으로 사용
        if "순번" not in temp_df.columns:
            temp_df["순번"] = range(1, len(temp_df) + 1)

        if temp_df.empty:
            continue

        sheet_stats.setdefault(sheet_name, {"total_rows": 0, "used_rows": 0})

        for idx, row in temp_df.iterrows():
            block_name = _normalise_plan_column(row.get("블록번호"))
            if not block_name:
                continue

            sub_number = _normalise_plan_column(row.get("소조번호"))
            key = (block_name, sub_number) if sub_number else (block_name,)

            seq_raw = row.get("순번")
            seq_numeric = pd.to_numeric(seq_raw, errors="coerce")
            sequence_value = int(seq_numeric) if not pd.isna(seq_numeric) else None
            if sequence_value is not None:
                max_sequence_seen = max(max_sequence_seen, sequence_value)

            longi_raw = row.get("론지 작업장")
            longi_numeric = pd.to_numeric(longi_raw, errors="coerce")
            longi_value = int(longi_numeric) if not pd.isna(longi_numeric) else None

            start_value = _parse_plan_date(row.get("착수일"))

            plan_mapping[key].append(
                {
                    "sequence": sequence_value,
                    "longi_workshop": longi_value,
                    "start_date": start_value,
                    "row_index": idx,
                    "sheet_name": sheet_name,
                    "sheet_priority": sheet_index,
                }
            )
            sheet_stats[sheet_name]["total_rows"] += 1

    if not plan_mapping:
        if not suppress_missing_message:
            print("ℹ️ 적용 가능한 수기계획/CP 순번 시트를 찾지 못했습니다. 기존 순번을 사용합니다.")
        return None

    fallback_sequence = max_sequence_seen + 1 if max_sequence_seen > 0 else 1
    unmatched_blocks = []
    matched_count = 0
    used_sheets: List[str] = []

    for block in blocks:
        block_name = _normalise_plan_column(getattr(block, "block_name", f"BLK_{block.block_id}"))
        sub_number = _normalise_plan_column(getattr(block, "sub_assembly_number", ""))

        candidate_keys = []
        if sub_number:
            candidate_keys.append((block_name, sub_number))
        candidate_keys.append((block_name,))

        matched = False
        for key in candidate_keys:
            entries = plan_mapping.get(key)
            if entries:
                info = entries.pop(0)
                seq_val = info["sequence"]
                if seq_val is None:
                    seq_val = fallback_sequence
                    fallback_sequence += 1
                else:
                    fallback_sequence = max(fallback_sequence, seq_val + 1)

                setattr(block, "sequence_number", int(seq_val))

                longi = info.get("longi_workshop")
                if longi in (1, 2):
                    try:
                        block.assigned_bay = BayType.BAY_35A if int(longi) == 1 else BayType.BAY_36B
                    except Exception:
                        pass

                start_date = info.get("start_date")
                if isinstance(start_date, datetime):
                    try:
                        setattr(block, "max_start_date", start_date)
                        setattr(block, "assembly_start_date", start_date)
                    except Exception:
                        pass

                sheet_name = info.get("sheet_name")
                if sheet_name:
                    if sheet_name not in used_sheets:
                        used_sheets.append(sheet_name)
                    if sheet_name in sheet_stats:
                        sheet_stats[sheet_name]["used_rows"] += 1

                matched = True
                matched_count += 1
                break

        if not matched:
            # 기존 sequence_number 유지, 없으면 fallback 부여
            current_seq = getattr(block, "sequence_number", None)
            if current_seq is None or current_seq == 0:
                setattr(block, "sequence_number", fallback_sequence)
                fallback_sequence += 1
            unmatched_blocks.append(block_name)

    remaining_entries = sum(len(entries) for entries in plan_mapping.values())

    if used_sheets:
        sheet_label = ", ".join(used_sheets)
    else:
        sheet_label = "수기계획/CP"

    unused_summary_parts = []
    for sheet_name, stats in sheet_stats.items():
        unused = stats["total_rows"] - stats["used_rows"]
        if unused > 0:
            unused_summary_parts.append(f"{sheet_name} {unused}개")
    unused_summary = ""
    if unused_summary_parts:
        unused_summary = " (미사용: " + ", ".join(unused_summary_parts) + ")"

    print(
        f"✅ '{sheet_label}' 시트 기반 순번 적용: {matched_count}개 블록 매칭"
        + (f", {remaining_entries}개 행 미사용" if remaining_entries else "")
        + unused_summary
    )
    if unmatched_blocks:
        sample = ", ".join(unmatched_blocks[:5])
        suffix = " ..." if len(unmatched_blocks) > 5 else ""
        print(f"⚠️ 순번 시트에 없는 블록 {len(unmatched_blocks)}개: {sample}{suffix}")

    return tuple(used_sheets) if used_sheets else None


def load_excel_blocks_with_plan(
    excel_path: str,
    plan_sheet: Optional[str] = None,
    plan_priority: Optional[Tuple[str, ...]] = None,
):
    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path)
    applied_sheets = apply_plan_sequence_from_excel(
        blocks,
        excel_path,
        plan_sheet=plan_sheet,
        plan_priority=plan_priority,
    )

    if applied_sheets and isinstance(metadata, dict):
        plan_info = metadata.setdefault("plan_sequence", {})
        if isinstance(plan_info, dict):
            if isinstance(applied_sheets, tuple):
                plan_info["sheet_name"] = list(applied_sheets)
            else:
                plan_info["sheet_name"] = applied_sheets

    return blocks, metadata, applied_sheets


def save_detailed_process_schedule_excel(date_key: str, sequence: List[int], bay_assignments: Dict, 
                                        ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime):
    """
    공정별 상세 스케줄링 CSV 저장 (엑셀 방식)
    
    Args:
        date_key: 날짜 키 (YYYYMMDD)
        sequence: 블록 순서
        bay_assignments: 베이 할당 정보
        ct_tables: CT 테이블 (makespan_calculator에서 생성)
        blocks_dict: 블록 정보 딕셔너리
        date_start_time: 날짜별 시작 시간
    """
    process_records = []
    process_names = [
        '판계', '전면SAW', 'TurnOver', '후면SAW', 'NC', 
        '론지취부', '론지용접', '수정'
    ]
    
    ct_common = ct_tables['ct_common']
    ct_branch_a = ct_tables['ct_branch_a'] 
    ct_branch_b = ct_tables['ct_branch_b']
    
    for seq_idx, block_id in enumerate(sequence):
        if block_id not in blocks_dict:
            continue
            
        block = blocks_dict[block_id]
        assigned_bay = bay_assignments.get(block_id, BayType.BAY_35A)
        
        # 공통 공정 (1~5): makespan_calculator 로직과 정확히 일치
        for proc_idx in range(5):
            # 완료 시간 (CT 테이블에서 직접 가져옴)
            end_seconds = ct_common[seq_idx + 1, proc_idx + 1]
            
            # 시작 시간 = 완료 시간 - 처리 시간
            processing_time_seconds = block.processing_times[proc_idx] * 60
            start_seconds = end_seconds - processing_time_seconds
            
            duration_min = processing_time_seconds / 60.0
            
            start_time = date_start_time + timedelta(seconds=start_seconds)
            end_time = date_start_time + timedelta(seconds=end_seconds)
            
            process_records.append({
                'date': date_key,
                'block_id': block_id,
                'block_name': getattr(block, 'block_name', f'BLK_{block_id}'),
                'sequence': seq_idx + 1,
                'process_name': process_names[proc_idx],
                'process_index': proc_idx + 1,
                'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_min': round(duration_min, 1),
                'assigned_bay': '',  # 공통 공정은 베이 없음
                'machine_type': '공통'
            })
        
        # 분기 공정 (6~8): makespan_calculator 로직과 정확히 일치
        branch_table = ct_branch_a if assigned_bay == BayType.BAY_35A else ct_branch_b
        bay_name = '35A' if assigned_bay == BayType.BAY_35A else '36B'
        
        for proc_idx in range(3):  # 분기 3개 공정
            # 완료 시간 (CT 테이블에서 직접 가져옴)
            end_seconds = branch_table[seq_idx + 1, proc_idx]
            
            # 시작 시간 = 완료 시간 - 처리 시간
            processing_time_seconds = block.processing_times[proc_idx + 5] * 60
            start_seconds = end_seconds - processing_time_seconds
            
            duration_min = processing_time_seconds / 60.0
            
            start_time = date_start_time + timedelta(seconds=start_seconds)
            end_time = date_start_time + timedelta(seconds=end_seconds)
            
            process_records.append({
                'date': date_key,
                'block_id': block_id,
                'block_name': getattr(block, 'block_name', f'BLK_{block_id}'),
                'sequence': seq_idx + 1,
                'process_name': f'베이{bay_name} {process_names[proc_idx + 5]}',
                'process_index': proc_idx + 6,
                'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_min': round(duration_min, 1),
                'assigned_bay': bay_name,
                'machine_type': f'베이{bay_name}'
            })
    
    # CSV 저장
    if process_records:
        df = pd.DataFrame(process_records)
        filename = f'detailed_excel_constraint_schedule_processes_{date_key}.csv'
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"📄 엑셀 공정별 상세 스케줄: {filename} ({len(process_records)}개 공정)")


#################################################################################################################################################
###############                                            완화 모드                                                               ###############  
#################################################################################################################################################
def save_detailed_schedule_info_excel(date_key: str, rows: List[Dict]):
    """엑셀 순번 방식의 블록별 일정 요약 CSV 저장"""
    if not rows:
        return

    df = pd.DataFrame(rows)
    filename = f'detailed_excel_schedule_info_{date_key}.csv'
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"📄 엑셀 일정 요약 저장: {filename} ({len(rows)}개 블록)")


def save_detailed_bay_info_excel(date_key: str, rows: List[Dict]):
    """엑셀 순번 방식의 베이 배정 상세 CSV 저장"""
    if not rows:
        return

    df = pd.DataFrame(rows)
    filename = f'detailed_excel_bayselect_info_{date_key}.csv'
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"📄 엑셀 베이 상세 저장: {filename} ({len(rows)}개 블록)")


def create_makespan_schedule(blocks, metadata):
    """실제 makespan 계산 및 CSV 생성 - 날짜별 상세 분석"""
    print("\n⏱️ 날짜별 상세 makespan 계산 및 제약조건 분석")
    print("=" * 60)
    
    # 날짜별 블록 그룹핑
    date_groups = {}
    for block in blocks:
        date_key = block.max_start_date.strftime('%Y%m%d')
        date_groups.setdefault(date_key, []).append(block)

    sequence_results = []
    total_violations = []
    
    # 환경 초기화 (makespan 계산용)
    constraint_config = get_basic_constraints_only_config()
    # 🔧 용량 제약조건 비활성화 (디버깅만, 실제 차단 없음)
    constraint_config.enable_p5_8_weekday_capacity = False
    constraint_config.enable_p5_16_hot_season_capacity = False
    constraint_config.enable_p5_15_holiday_shift = False       # P5#15: 명절 전날 야간 없음 - 구현하되 기본 OFF
    constraint_config.enable_p5_9_block_count_check = False       # P5#9: 72심 초과시 17블록 체크 - 구현하되 기본 OFF
    constraint_config.enable_p5_10_weekend_capacity = False       # P5#10: 주말 심수 용량 (45심) - 구현하되 기본 OFF


    earliest_start_date = min(block.max_start_date for block in blocks)
    start_time = earliest_start_date.replace(hour=8, minute=0, second=0, microsecond=0)
    
    env = EnhancedPanelBlockShop(
        blocks=blocks,
        start_time=start_time,
        max_steps=5000,
        constraint_config=constraint_config,
        metadata=metadata
    )
    
    print(f"✅ 환경 초기화 완료: {len(blocks)}개 블록")
    print(f"📅 처리 기간: {sorted(date_groups.keys())[0]} ~ {sorted(date_groups.keys())[-1]} ({len(date_groups)}일)")
    
    for date_key in sorted(date_groups.keys()):
        blocks_in_date = date_groups[date_key]

        # 엑셀 순번 순서대로 정렬
        blocks_sorted = sorted(blocks_in_date, key=lambda b: getattr(b, 'sequence_number', 0))

        print(f"\n📅 {date_key} 처리 시작 ({len(blocks_in_date)}개 블록):")

        # ✅ 날짜별 환경 상태 리셋 (베이 연속성 추적을 위해)
        env.bay_tracker.reset()
        env.ps_manager.reset()
        
        # 🔥 핵심 수정: 날짜별 주말/평일 구분하여 적절한 심수 리셋
        current_date = datetime(int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8]), 8, 0)
        is_weekend = env.calendar_manager.is_weekend(current_date)
        
        if is_weekend:
            env.capacity_tracker.reset_weekend()  # 주말 심수 리셋
        else:
            env.capacity_tracker.reset_daily()   # 평일 심수 리셋

        # 날짜별 통계 초기화
        date_violations = []
        bay_assignments = {'35A': 0, '36B': 0}
        ps_distribution = {'P': 0, 'S': 0, 'C': 0}
        assembly_distribution = {'line': 0, 'fixed': 0}
        width_stats = []
        longi_stats = []
        #################################################################################################################################################
        ###############                                            완화 모드                                                               ###############  
        #################################################################################################################################################
        schedule_info_rows: List[Dict] = []
        bay_info_rows: List[Dict] = []

        last_assembly_type = None
        fixed_streak = 0
        last_line_group = None
        line_group_streak = 0
        line_group_limit = 0
        if constraint_config.enable_line_group_constraint:
            line_group_limit = (
                constraint_config.line_group_soft_limit
                if constraint_config.line_group_use_soft_limit
                else constraint_config.line_group_strict_limit
            )

        for i, block in enumerate(blocks_sorted, 1):
            excel_sequence = block.sequence_number

            # ✅ 엑셀 방식: 엑셀 데이터 그대로 사용 (환경 개입 없음)
            assigned_bay = block.assigned_bay  # 엑셀 원본 베이 그대로 사용
            actual_block = block  # 기본값 (필요 시 업데이트)

            processing_time_seconds = sum(block.processing_times) * 60

            # 통계 수집
            bay_value = assigned_bay.value
            bay_assignments[bay_value] = bay_assignments.get(bay_value, 0) + 1

            ps_value = block.port_starboard.value  
            ps_distribution[ps_value] = ps_distribution.get(ps_value, 0) + 1

            # assembly_type 안전하게 처리
            assembly_type = block.assembly_type.value
            assembly_distribution[assembly_type] = assembly_distribution.get(assembly_type, 0) + 1
            width_stats.append(block.width)
            longi_stats.append(block.longi_count)

            # 라인 그룹/작업장 코드 정규화
            resolved_line_group = getattr(block, 'line_group', None)
            resolved_workshop_code = getattr(block, 'assembly_workshop_code', None)
            normalized_line_group, normalized_workshop_code = get_line_group_and_workshop_code(block)
            if not resolved_line_group:
                resolved_line_group = normalized_line_group
            if not resolved_workshop_code:
                resolved_workshop_code = normalized_workshop_code

            # ✅ 엑셀 방식: 완성된 스케줄에 대한 사후 제약조건 검증만 수행
            violations = []
            
            # ✅ 통합 실시간 검증 호출 (Action Masking과 동일한 전체 제약 세트)
            try:
                all_violations = env._validate_all_constraints_realtime_action(
                    block, assigned_bay, current_date
                )
                violations.extend(all_violations)
            except Exception as validation_error:
                print(f"      ⚠️ 블록 {block.block_id} 제약조건 검증 실패: {validation_error}")
                print("      ❌ 알고리즘을 종료합니다.")
                raise Exception(f"제약조건 검증 실패: {validation_error}")

            # ✅ 엑셀 방식: 환경 개입 없이 상태 업데이트만 (makespan 계산용)
            env.bay_tracker.assign_bay(block, assigned_bay, processing_time_seconds)
            env.ps_manager.process_block(block, assigned_bay, env.current_time)

            # ✅ 완료 스텝 기록: 라우팅/워크숍 순서·SAW 계산용 히스토리 유지
            try:
                step = ProcessStep(
                    block_id=block.block_id,
                    process_num=1,
                    bay_type=assigned_bay,
                    start_time=current_date,
                    end_time=current_date + timedelta(seconds=processing_time_seconds),
                    processing_time=processing_time_seconds,
                    completion_time=processing_time_seconds,
                )
                env.completed_steps.append(step)
            except Exception:
                pass

            # [AGENT-EDIT] 제약 카운트/상세/완화 요약 통일
            violation_summary = summarize_violations(
                violations,
                keep_info=True,
                include_guard=False,
            )

            # 결과 데이터 생성 (임시 데이터, 사후 검증에서 업데이트됨)
            result = {
                'date': date_key,
                'excel_sequence': excel_sequence,
                'block_id': block.block_id,
                'block_name': block.block_name,
                'port_starboard': block.port_starboard.value,
                'assembly_type': block.assembly_type.value,
                'block_assembly_date': block.assembly_start_date.strftime('%Y-%m-%d') if hasattr(block, 'assembly_start_date') and block.assembly_start_date else '',
                'line_group': resolved_line_group or '',
                'assembly_workshop_code': resolved_workshop_code or '',
                'width_m': round(block.width, 1),
                'longi_count': block.longi_count,
                'seam_count': block.seam_count,
                'c_seam_count': getattr(block, 'c_seam_count', 0),
                ################################################################################################################################################################################################
                # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                ################################################################################################################################################################################################
                'has_curved_plate': getattr(block, 'has_curved_plate', False),
                'main_plate_count': block.main_plate_count,
                'original_bay': assigned_bay.value,  # 엑셀 원본 베이
                'assigned_bay': assigned_bay.value,  # 환경 할당 베이
                'bay_changed': False,  # 베이 변경 여부
                'total_time_min': round(sum(block.processing_times), 1),
                'material_ready': block.material_ready,
                'is_fab': block.is_fab,
                'is_draft': block.is_draft,
                'is_cross_seam': block.is_cross_seam,
                'material_type': block.material_type.value,
                **violation_summary,
                'method': 'EXCEL'  # 비교용 방식 표시
            }

            # 별판 확장 정보 기록 (후속 CSV 확장용)
            subassembly_expansion = []
            original_blocks = getattr(block, 'subassembly_original_blocks', [block])
            subassembly_group_size = len(original_blocks)
            for original in original_blocks:
                sub_line_group, sub_assembly_code = get_line_group_and_workshop_code(original)
                subassembly_expansion.append({
                    'block_id': getattr(original, 'block_id', block.block_id),
                    'block_name': getattr(original, 'block_name', block.block_name),
                    'port_starboard': original.port_starboard.value,
                    'assembly_type': original.assembly_type.value,
                    'line_group': sub_line_group,
                    'assembly_workshop_code': sub_assembly_code,
                    'block_assembly_date': original.assembly_start_date.strftime('%Y-%m-%d') if getattr(original, 'assembly_start_date', None) else '',
                    'width_m': round(getattr(original, 'width', block.width), 1),
                    'longi_count': getattr(original, 'longi_count', block.longi_count),
                    'seam_count': getattr(original, 'seam_count', block.seam_count),
                    'c_seam_count': getattr(original, 'c_seam_count', getattr(block, 'c_seam_count', 0)),
                    'has_curved_plate': getattr(original, 'has_curved_plate', getattr(block, 'has_curved_plate', False)),
                    'main_plate_count': getattr(original, 'main_plate_count', block.main_plate_count)
                })
            result['subassembly_expansion'] = subassembly_expansion
            result['subassembly_group_size'] = subassembly_group_size
            result['subassembly_representative_id'] = block.block_id
            result['is_subassembly_member'] = subassembly_group_size > 1
            result['is_subassembly_representative'] = True
            result['violations_representative'] = violation_summary['violations']

            # 개별 공정 시간 추가
            for j, proc_time in enumerate(block.processing_times):
                result[f'process_{j+1}_time_min'] = round(proc_time, 1)

            sequence_results.append(result)
            total_violations.extend(violations)
            date_violations.extend(violations)

            # 진행 상황 출력
            violation_status = f"({len(violations)}건 위반)" if violations else "✅"
            # print(f"   #{excel_sequence:2d} 블록{block.block_id:3d}: {block.port_starboard.value} {block.width:4.1f}m {block.longi_count:2d}론지 {violation_status}")

        # 날짜별 요약 통계 출력
        # print(f"\n   📊 {date_key} 요약:")
        # 베이 분포를 동적으로 출력
        bay_str = ", ".join([f"{k} {v}개" for k, v in bay_assignments.items() if v > 0])
        # print(f"      • 베이 분포: {bay_str}")
        
        # P/S/C 분포를 동적으로 출력  
        ps_str = ", ".join([f"{k} {v}개" for k, v in ps_distribution.items() if v > 0])
        # print(f"      • P/S/C 분포: {ps_str}")
        
        # 조립 분포를 동적으로 출력
        assembly_str = ", ".join([f"{k} {v}개" for k, v in assembly_distribution.items() if v > 0])
        # print(f"      • 조립 분포: {assembly_str}")
        # print(f"      • 폭 범위: {min(width_stats):.1f}~{max(width_stats):.1f}m (평균: {sum(width_stats)/len(width_stats):.1f}m)")
        # print(f"      • 론지 범위: {min(longi_stats)}~{max(longi_stats)}개 (평균: {sum(longi_stats)/len(longi_stats):.1f}개)")
        # print(f"      • 제약조건 위반: {len(date_violations)}건 (위반률: {len(date_violations)/len(blocks_in_date)*100:.1f}%)")
        
        # 위반 종류별 통계
        if date_violations:
            violation_types = {}
            for violation in date_violations:
                constraint_id = violation.constraint_id
                violation_types[constraint_id] = violation_types.get(constraint_id, 0) + 1
            
            # print(f"      • 위반 상세:")
            # for constraint_id, count in violation_types.items():
            #     print(f"        - {constraint_id}: {count}건")

    # makespan 계산 및 시간 분배
    # print(f"\n⏱️ makespan 계산 및 시간 분배 중...")
    
    date_groups_results = {}
    for result in sequence_results:
        date = result['date']
        if date not in date_groups_results:
            date_groups_results[date] = []
        date_groups_results[date].append(result)

    all_enhanced_results = []
    total_makespan_hours = 0
    
    for date_key in sorted(date_groups_results.keys()):
        date_blocks = date_groups_results[date_key]
        
        # ✅ 날짜별 상태 리셋 (연속성/용량 중복 누적 방지)
        env.bay_tracker.reset()
        env.ps_manager.reset()
        env.capacity_tracker.reset_daily()
        
        try:
            year = int(date_key[:4])
            month = int(date_key[4:6])
            day = int(date_key[6:8])
            date_start_time = datetime(year, month, day, 8, 0)
        except:
            date_start_time = env.start_time
        
        # 날짜별 재검증을 위해 상태 리셋 후 순차 재생
        env.bay_tracker.reset()
        env.ps_manager.reset()
        env.capacity_tracker.reset_daily()
        block_sequence = [r['block_id'] for r in date_blocks]
        bay_assignments = {r['block_id']: BayType.BAY_35A if r['assigned_bay'] == '35A' else BayType.BAY_36B for r in date_blocks}
        
        try:
            # ✅ 모든 makespan 관련 정보를 한 번에 받기
            makespan_seconds, detailed_result = env.calculate_makespan(block_sequence, bay_assignments)
            makespan_minutes = makespan_seconds / 60
            makespan_hours = makespan_minutes / 60
            total_makespan_hours += makespan_hours
            
            completion_time = date_start_time + timedelta(minutes=makespan_minutes)
            
            print(f"   📅 {date_key}: {makespan_minutes:.1f}분 = {makespan_hours:.2f}시간")
            print(f"      시작: {date_start_time.strftime('%Y-%m-%d %H:%M')} → 완료: {completion_time.strftime('%Y-%m-%d %H:%M')}")
            
            # 🆕 공정별 상세 스케줄링 CSV 생성 (엑셀 방식)
            try:
                # 블록 딕셔너리 생성
                blocks_dict = {r['block_id']: next(b for b in blocks if b.block_id == r['block_id']) for r in date_blocks}
                
                save_detailed_process_schedule_excel(
                    date_key=date_key,
                    sequence=block_sequence,
                    bay_assignments=bay_assignments,
                    ct_tables=detailed_result['ct_tables'],
                    blocks_dict=blocks_dict,
                    date_start_time=date_start_time
                )
            except Exception as process_err:
                print(f"      ⚠️ 엑셀 공정별 상세 스케줄 생성 실패: {process_err}")
            
            # ✅ 이미 계산된 block_schedules 사용 (중복 계산 제거!)
            block_schedules = detailed_result['block_schedules']
            
            for i, result in enumerate(date_blocks):
                enhanced_result = result.copy()
                block_id = result['block_id']
                
                # 해당 블록의 스케줄 정보 찾기
                block_schedule = next((bs for bs in block_schedules if bs['block_id'] == block_id), None)
                
                if block_schedule:
                    # 이미 계산된 시간 정보 사용
                    block_start_time = date_start_time + timedelta(seconds=block_schedule['start_seconds'])
                    block_end_time = date_start_time + timedelta(seconds=block_schedule['end_seconds'])
                else:
                    # 스케줄 정보를 찾지 못한 경우 fallback
                    block_start_time = date_start_time
                    block_end_time = date_start_time + timedelta(minutes=result['total_time_min'])
                
                enhanced_result.update({
                    'start_time': block_start_time.strftime('%Y-%m-%d %H:%M'),
                    'end_time': block_end_time.strftime('%Y-%m-%d %H:%M'),
                    'date_start_time': date_start_time.strftime('%Y-%m-%d %H:%M'),
                    'date_completion_time': completion_time.strftime('%Y-%m-%d %H:%M'),
                    'makespan_minutes': round(makespan_minutes, 1),
                    'makespan_hours': round(makespan_hours, 2)
                })
                #################################################################################################################################################
                ###############                                            완화 모드                                                               ###############  
                #################################################################################################################################################
                schedule_info_rows.append({
                    'date': date_key,
                    'sequence': result.get('sequence', i),
                    'block_id': block_id,
                    'block_name': result.get('block_name', getattr(actual_block, 'block_name', f'BLK_{block_id}')),
                    'assigned_bay': result.get('assigned_bay', ''),
                    'start_time': block_start_time.strftime('%Y-%m-%d %H:%M'),
                    'end_time': block_end_time.strftime('%Y-%m-%d %H:%M'),
                    'duration_min': result.get('total_time_min', 0),
                    'violations': enhanced_result.get('violations', 0),
                    'port_starboard': getattr(actual_block.port_starboard, 'value', '') if actual_block else '',
                    'assembly_type': getattr(actual_block.assembly_type, 'value', '') if actual_block else '',
                    'assembly_workshop': getattr(actual_block, 'assembly_workshop_code', ''),
                    'line_group': getattr(actual_block, 'line_group', '')
                })
                bay_info_rows.append({
                    'date': date_key,
                    'sequence': result.get('sequence', i),
                    'block_id': block_id,
                    'block_name': result.get('block_name', getattr(actual_block, 'block_name', f'BLK_{block_id}')),
                    'assigned_bay': result.get('assigned_bay', ''),
                    'width': getattr(actual_block, 'width', None) if actual_block else None,
                    'longi_count': getattr(actual_block, 'longi_count', None) if actual_block else None,
                    'seam_count': getattr(actual_block, 'seam_count', None) if actual_block else None,
                    'c_seam_count': getattr(actual_block, 'c_seam_count', None) if actual_block else None,
                    'port_starboard': getattr(actual_block.port_starboard, 'value', '') if actual_block else '',
                    'assembly_type': getattr(actual_block.assembly_type, 'value', '') if actual_block else '',
                    'assembly_workshop': getattr(actual_block, 'assembly_workshop_code', ''),
                    'line_group': getattr(actual_block, 'line_group', ''),
                    'total_time_min': result.get('total_time_min', 0)
                })

                # 🎯 여기에 사후 제약조건 검증 추가!
                # 실제 블록과 베이 정보로 정확한 시간 기준 제약조건 재검증
                
                # 디버깅: 블록 찾기 확인
                # print(f"      🔍 블록 {block_id} 사후 검증 준비 - 날짜: {date_key}")
                
                # blocks_sorted가 올바르게 설정되어 있는지 확인
                actual_block = block
                try:
                    # 현재 날짜의 블록들을 다시 가져오기
                    blocks_in_current_date = date_groups[date_key]
                    blocks_sorted_current = sorted(blocks_in_current_date, key=lambda b: getattr(b, 'sequence_number', 0))
                    found_block = next((b for b in blocks_sorted_current if b.block_id == block_id), None)
                    if found_block is not None:
                        actual_block = found_block

                    # print(f"      📋 현재 날짜 블록 수: {len(blocks_sorted_current)}, 찾은 블록: {actual_block is not None}")
                    
                except Exception as find_error:
                    print(f"      ❌ 블록 찾기 실패: {find_error}")
                    actual_block = block

                actual_bay = BayType.BAY_35A if result['assigned_bay'] == '35A' else BayType.BAY_36B

                if actual_block is not None:
                    # ✅ SAW 시간 제약조건만 실제 시간으로 재검증
                    try:
                        # 디버깅: 사후 검증 시작 확인
                        # print(f"      🔍 블록 {block_id} SAW 사후 검증 시작 - 시간: {block_start_time.strftime('%H:%M')}")
                        
                        # 기존 위반들 중 SAW 관련 제거
                        existing_violations = []
                        existing_details = result.get('violation_details', [])
                        existing_severities = result.get('violation_severity', [])
                        existing_constraint_ids = result.get('constraint_ids', [])
                        
                        # 디버깅: 기존 위반 확인
                        # print(f"      📋 기존 위반: {existing_constraint_ids}")
                        
                        # SAW 관련이 아닌 위반들만 유지
                        for i, constraint_id in enumerate(existing_constraint_ids):
                            if not constraint_id.startswith('P6#'):  # SAW 제약조건이 아닌 것만 유지
                                existing_violations.append({
                                    'constraint_id': constraint_id,
                                    'message': existing_details[i] if i < len(existing_details) else '',
                                    'severity': existing_severities[i] if i < len(existing_severities) else 'INFO'
                                })
                        
                        # 디버깅: SAW 제외 후 남은 위반
                        # print(f"      🚫 SAW 제외 후: {[v['constraint_id'] for v in existing_violations]}")
                        
                        # 🆕 실제 머신 2번(전면SAW) 시작 시간 계산 (CT 테이블 기반)
                        actual_machine_2_start_time = None
                        if block_schedule:
                            try:
                                # CT 테이블에서 전면SAW 시작 시간 계산
                                # block_schedule에서 전면SAW 완료 시간 추출 필요
                                # makespan_calculator 결과에서 CT 테이블 사용
                                
                                # 현재 블록의 시퀀스 인덱스 찾기
                                current_sequence = [r['block_id'] for r in date_blocks]
                                if block_id in current_sequence:
                                    seq_idx = current_sequence.index(block_id)
                                    
                                    # CT 테이블에서 전면SAW 완료 시간 가져오기
                                    ct_tables = detailed_result['ct_tables']
                                    ct_common = ct_tables['ct_common']
                                    
                                    # 전면SAW 완료 시간 (CT 테이블 1-based 인덱스)
                                    saw_end_seconds = ct_common[seq_idx + 1, 2]  # 2번 컬럼: 전면SAW 완료
                                    
                                    # 전면SAW 시작 시간 = 완료 시간 - 처리 시간
                                    saw_duration_seconds = actual_block.processing_times[1] * 60  # 전면SAW 처리 시간
                                    machine_2_start_seconds = saw_end_seconds - saw_duration_seconds
                                    
                                    actual_machine_2_start_time = date_start_time + timedelta(seconds=machine_2_start_seconds)
                                    
                                    # 디버깅: 계산된 시간 확인
                                    print(f"      🔧 엑셀 BLK_{block_id}: 계산된 머신2번 시작 = {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                                    
                                    # 날짜 전환 체크 (22시 이후 → 다음날 08시)
                                    if actual_machine_2_start_time.hour >= 22:
                                        next_date = actual_machine_2_start_time.date() + timedelta(days=1)
                                        from datetime import time
                                        actual_machine_2_start_time = datetime.combine(next_date, time(8, 0))
                                        print(f"      📅 날짜 전환: 22시 이후 → {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                                    
                            except Exception as calc_err:
                                # 계산 실패 시 기본 시간 사용
                                actual_machine_2_start_time = block_start_time
                        
                        # SAW 제약조건 검증 시 실제 머신 2번 시간 전달
                        saw_violations = env._validate_saw_constraints_realtime(
                            actual_block, block_start_time, actual_machine_2_start_time
                        )
                        
                        # 디버깅: 새로운 SAW 위반 확인
                        # print(f"      🎯 새로운 SAW 위반: {[v.constraint_id + ':' + v.message for v in saw_violations]}")
                        
                        # 기존 위반들과 새로운 SAW 위반들 결합
                        all_combined_violations = existing_violations.copy()
                        for saw_violation in saw_violations:
                            all_combined_violations.append({
                                'constraint_id': saw_violation.constraint_id,
                                'message': saw_violation.message,
                                'severity': saw_violation.severity
                            })
                        
                        # [AGENT-EDIT] 제약 카운트/상세/완화 요약 통일
                        combined_objs = [
                            ConstraintViolation(
                                constraint_id=v.get('constraint_id', ''),
                                message=v.get('message', ''),
                                severity=v.get('severity', 'INFO'),
                                block_id=block_id,
                            )
                            for v in all_combined_violations
                        ]
                        enhanced_result.update(
                            summarize_violations(
                                combined_objs,
                                keep_info=True,
                                include_guard=False,
                            )
                        )
                        
                        # 디버깅: 최종 결과 확인
                        # print(f"      ✅ 최종 위반: {[v['constraint_id'] for v in all_combined_violations]}")
                        
                        # 사후 검증된 SAW 위반들을 total_violations에 추가
                        total_violations.extend(saw_violations)
                        
                    except Exception as validation_error:
                        print(f"      ⚠️ SAW 사후 검증 실패 블록 {block_id}: {validation_error}")
                        # 검증 실패 시 기존값 유지
                else:
                    print(f"      ❌ 블록 {block_id}를 찾을 수 없음 - 사후 검증 건너뜀")
                
                all_enhanced_results.append(enhanced_result)

            save_detailed_schedule_info_excel(date_key, schedule_info_rows)
            save_detailed_bay_info_excel(date_key, bay_info_rows)

        except Exception as e:
            print(f"   ❌ {date_key} makespan 계산 실패: {e}")
            # 실패 시 오류 발생 (fallback 제거)
            raise Exception(f"{date_key} makespan 계산 실패: {e}")

    # 별판 확장 행 생성 (통계 및 CSV 저장용)
    expanded_sequence_results = expand_rows_with_subassembly(sequence_results)
    expanded_enhanced_results = expand_rows_with_subassembly(all_enhanced_results)

    # 상세 CSV 저장
    df_sequence = pd.DataFrame(expanded_enhanced_results)
    csv_filename = "detailed_constraint_schedule.csv"
    df_sequence.to_csv(csv_filename, index=False, encoding='utf-8-sig')

    # 최종 통계 출력
    print(f"\n📊 최종 분석 결과:")
    print(f"=" * 60)
    integrated_block_count = len(sequence_results)
    expanded_block_count = len(expanded_sequence_results)
    print(f"총 처리 블록: {expanded_block_count}개 (통합 기준 {integrated_block_count}개)")
    
    # ✅ 수정: ERROR, WARNING만 카운팅하는 실제 위반 건수 계산
    actual_total_violations = sum(result.get('violations', 0) for result in all_enhanced_results)
    violation_base = integrated_block_count if integrated_block_count else 1
    print(f"총 제약조건 위반: {actual_total_violations}건 (위반률: {actual_total_violations/violation_base*100:.1f}% - 통합 기준)")
    print(f"총 Makespan: {total_makespan_hours:.2f}시간 ({total_makespan_hours/24:.1f}일)")
    print(f"일평균 처리시간: {total_makespan_hours/len(date_groups):.2f}시간")
    
    # 위반 종류별 통계
    if total_violations:
        violation_types = {}
        severity_count = {'ERROR': 0, 'WARNING': 0, 'INFO': 0}
        for violation in total_violations:
            constraint_id = violation.constraint_id
            violation_types[constraint_id] = violation_types.get(constraint_id, 0) + 1
            severity_count[violation.severity] += 1
        
        print(f"\n위반 종류별 상세:")
        for constraint_id, count in sorted(violation_types.items()):
            print(f"  • {constraint_id}: {count}건")
        
        print(f"\n위반 심각도별:")
        for severity, count in severity_count.items():
            if count > 0:
                print(f"  • {severity}: {count}건")
    
    # 베이별 통계
    bay_stats = {}
    for result in expanded_sequence_results:
        bay_value = result['assigned_bay']
        bay_stats[bay_value] = bay_stats.get(bay_value, 0) + 1
    
    print(f"\n베이 할당 분포:")
    total_bay_blocks = expanded_block_count if expanded_block_count else 1
    for bay_name, count in bay_stats.items():
        percentage = count/total_bay_blocks*100
        print(f"  • {bay_name} 베이: {count}개 ({percentage:.1f}%)")
    
    print(f"\n📄 상세 결과 저장: {csv_filename}")
    print(f"=" * 60)

    env.close()
    
    return expanded_enhanced_results, total_violations


# 독립 실행 로직
if __name__ == "__main__":
    print("Excel 순번 기반 스케줄링 독립 실행")
    print("=" * 60)
    
    # 🆕 test.py 설정 가져오기 (circular import 방지)
    try:
        import sys
        if 'test' in sys.modules:
            test_module = sys.modules['test']
            excel_path = getattr(test_module, 'EXCEL_PATH', DEFAULT_EXCEL_PATH)
            print(f"✅ test.py 설정 로드: {excel_path}")
        else:
            raise ImportError("test.py not loaded yet")
    except (ImportError, AttributeError):
        # [AGENT-EDIT] config.yaml data.excel_path가 있으면 기본값 덮어쓰기
        runtime_cfg = get_runtime_config() or {}
        if isinstance(runtime_cfg, dict):
            data_cfg = runtime_cfg.get("data") or {}
            cfg_path = data_cfg.get("excel_path")
            if cfg_path:
                excel_path = str(cfg_path)
                print(f"✅ config.yaml data.excel_path 적용: {excel_path}")
            else:
                excel_path = 'environment/판넬 블록 데이터셋_250618_SNU.xlsx'
                print(f"⚠️ test.py 설정 사용 불가, 기본값 사용: {excel_path}")
        else:
            excel_path = 'environment/판넬 블록 데이터셋_250618_SNU.xlsx'
            print(f"⚠️ test.py 설정 사용 불가, 기본값 사용: {excel_path}")
    
    try:
        # 1. 데이터 로딩
        blocks, metadata, applied_sheet = load_excel_blocks_with_plan(excel_path)
        if applied_sheet:
            print(f"🗂️ 적용 순번 시트: {applied_sheet}")
        else:
            print("🗂️ 외부 순번 시트 미적용 (기존 순번 사용)")
        
        print(f"\n📊 Excel 순번 기반 스케줄링 실행 중...")
        
        # 2. Excel 순번 기반 스케줄링 실행
        excel_results, excel_violations = create_makespan_schedule(blocks, metadata)
        
        # 3. 결과 저장 (test.py와 동일한 방식)
        import pandas as pd
        df = pd.DataFrame(excel_results)
        csv_filename = 'excel_sequence_standalone_results.csv'
        df.to_csv(csv_filename, index=False, encoding='utf-8-sig')
        
        # 4. 통계 출력
        print(f"\n📊 Excel 순번 방식 결과:")
        print(f"   처리된 블록: {len(excel_results)}개")
        
        # Makespan 계산 (날짜별 최대 makespan 합산)
        makespan_by_date = {}
        for result in excel_results:
            date = result.get('date', 'N/A')
            makespan = result.get('makespan_hours', 0)
            if date not in makespan_by_date or makespan > makespan_by_date[date]:
                makespan_by_date[date] = makespan
        
        total_makespan = sum(makespan_by_date.values())
        total_violations = sum(r.get('violations', 0) for r in excel_results)
        
        print(f"   총 makespan: {total_makespan:.2f}시간")
        print(f"   총 위반: {total_violations}개")
        print(f"   성공률: {len(excel_results) / len(blocks) * 100:.1f}%")
        
        # 베이 분포
        bay_stats = {}
        for result in excel_results:
            bay = result.get('assigned_bay', 'N/A')
            bay_stats[bay] = bay_stats.get(bay, 0) + 1
        
        print(f"   베이 분포:")
        for bay, count in bay_stats.items():
            percentage = count / len(excel_results) * 100 if excel_results else 0
            print(f"     {bay}: {count}개 ({percentage:.1f}%)")
        
        print(f"\n📄 결과 파일: {csv_filename}")
        print(f"✅ Excel 순번 기반 스케줄링 완료!")
        
    except Exception as e:
        print(f"❌ Excel 순번 기반 스케줄링 실패: {e}")
        import traceback
        traceback.print_exc()
