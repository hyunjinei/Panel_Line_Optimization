# [AGENT-ADD] Split from masking/core.py for readability.

import os
from typing import Optional, Any, List, Tuple, Dict
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.models import EnhancedBlock

class DebugMixin:
    def _resolve_debug_flag(self, config: Optional[ConstraintConfig]) -> bool:
        """
        Determine whether verbose masking diagnostics should be printed.
    
        Priority:
        1. Environment variable PBS_FORCE_DEBUG (1/true/on vs 0/false/off),
        2. ConstraintConfig.enable_debug_violations,
        3. Default True to keep legacy behavior.
        """
        env_value = os.environ.get("PBS_FORCE_DEBUG", "")
        if env_value:
            normalized = env_value.strip().lower()
            if normalized in {"1", "true", "on", "yes"}:
                return True
            if normalized in {"0", "false", "off", "no"}:
                return False
        if config is not None:
            return getattr(config, 'enable_debug_violations', True)
        return True
    

    def _is_trace_block(self, block_id: Optional[int]) -> bool:
        """Return True if the block_id is explicitly requested for tracing."""
        if not block_id or not self.trace_block_ids:
            return False
        return block_id in self.trace_block_ids
    

    def _debug_candidate_stage(self, debug_enabled: bool, label: str, collection: Any, history: Optional[List[Tuple[str, Any]]] = None) -> None:
        """Helper to print candidate counts for each filtering stage when debugging is enabled."""
        if collection is None:
            count = 0
        elif isinstance(collection, (int, float)):
            count = collection
        else:
            try:
                count = len(collection)
            except TypeError:
                count = 'n/a'
    
        if history is not None:
            history.append((label, count))
    
        if not debug_enabled:
            return
    
        # print(f"   [DEBUG] {label}: {count}")
    

    def _print_candidate_diagnostics(
        self,
        selected_blocks: List[int],
        block_analysis: List[Dict[str, Any]],
        blocks: List[EnhancedBlock],
        debug_enabled: bool = False
    ) -> None:
        """Print detailed diagnostics when no selectable blocks remain."""
        # [AGENT-EDIT] 평가 로그 정리 목적: verbose 플래그가 꺼져 있으면 출력 생략.
        if not debug_enabled:
            return
        debug_verbose = os.environ.get("PBS_DEBUG_VERBOSE", "").strip().lower() in {"1", "true", "on", "yes"}
        if not debug_verbose:
            return
        try:
            block_lookup: Dict[int, EnhancedBlock] = {}
            if hasattr(self, 'blocks_dict') and isinstance(self.blocks_dict, dict):
                block_lookup = self.blocks_dict
            else:
                block_lookup = {blk.block_id: blk for blk in blocks}
    
            bay_history: Dict[int, str] = {}
            if hasattr(self, 'bay_tracker') and getattr(self, 'bay_tracker', None) is not None:
                history = getattr(self.bay_tracker, 'assignment_history', None)
                if history:
                    for blk_id, bay, _ in history:
                        bay_label = getattr(bay, 'value', str(bay)) if bay is not None else '미할당'
                        bay_history[blk_id] = bay_label
    
            print("   🔎 최근 선택된 블록:")
            if selected_blocks:
                recent_ids = selected_blocks[-5:]
                for block_id in recent_ids:
                    block_obj = block_lookup.get(block_id)
                    if block_obj:
                        block_name = getattr(block_obj, 'block_name', f'BLK_{block_id}')
                        start_date = getattr(block_obj, 'assembly_start_date', None)
                        start_str = start_date.strftime('%Y-%m-%d') if start_date else '정보 없음'
                        assembly_type = getattr(getattr(block_obj, 'assembly_type', None), 'value', '정보 없음')
                        port_starboard = getattr(getattr(block_obj, 'port_starboard', None), 'value', '정보 없음')
                        line_group = getattr(block_obj, 'line_group', None)
                        line_group_str = line_group if line_group else '정보 없음'
                        assigned_bay = bay_history.get(block_id)
                        if not assigned_bay:
                            bay_value = getattr(getattr(block_obj, 'assigned_bay', None), 'value', None)
                            assigned_bay = bay_value or '미할당'
                        print(
                            f"    - ID {block_id} ({block_name}), 착수일 {start_str}, 조립타입 {assembly_type}, 라인그룹 {line_group_str}, P/S {port_starboard}, 베이 {assigned_bay}"
                        )
                    else:
                        print(f"    - ID {block_id} (상세 정보 없음)")
            else:
                print("    - 선택된 블록 없음")
    
            # 남은 블록의 라인그룹/조립타입 분포 요약
            remaining_blocks = [blk for blk in blocks if blk.block_id not in selected_blocks]
            if remaining_blocks:
                from collections import Counter
    
                assembly_counter = Counter()
                line_group_counter = Counter()
    
                for blk in remaining_blocks:
                    assembly_counter[getattr(getattr(blk, 'assembly_type', None), 'value', '정보 없음')] += 1
                    line_group_value = getattr(blk, 'line_group', None)
                    if not line_group_value:
                        from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
                        resolved_group, _ = get_line_group_and_workshop_code(blk)
                        line_group_value = resolved_group or '정보 없음'
                    line_group_counter[str(line_group_value)] += 1
    
                print("   📊 남은 블록 조립타입 분포:")
                for assembly, count in assembly_counter.most_common(5):
                    print(f"    - {assembly}: {count}개")
    
                print("   📊 남은 블록 라인그룹 분포:")
                for group, count in line_group_counter.most_common(5):
                    print(f"    - {group}: {count}개")
    
            available_infos = [info for info in block_analysis if info.get('is_available')]
            if available_infos:
                print("   ✅ 제약 통과 후보:")
                for info in available_infos[:10]:
                    inclusion = info.get('inclusion_reason') or '사유 없음'
                    print(f"    - ID {info['block_id']} ({info.get('block_name')}), {inclusion}")
                if len(available_infos) > 10:
                    print(f"    ... 외 {len(available_infos) - 10}건")
            else:
                print("   ✅ 제약 통과 후보: 없음")
    
            failure_infos = [info for info in block_analysis if not info.get('is_available')]
            if failure_infos:
                reason_summary: Dict[str, List[int]] = {}
                for info in failure_infos:
                    reason = info.get('exclusion_reason') or '사유 미상'
                    reason_summary.setdefault(reason, []).append(info['block_id'])
    
                print("   ❌ 제외 사유별 후보:")
                sorted_reasons = sorted(reason_summary.items(), key=lambda item: len(item[1]), reverse=True)
                for reason, ids in sorted_reasons[:10]:
                    suffix = '...' if len(ids) > 10 else ''
                    preview = ids[:10]
                    print(f"    - {reason}: {preview}{suffix}")
                if len(sorted_reasons) > 10:
                    print(f"    ... 기타 제외 사유 {len(sorted_reasons) - 10}건")
    
            print("   ❌ 제외된 후보 상세:")
            detailed_failures = failure_infos[:10]
            for info in detailed_failures:
                block_id = info['block_id']
                block_obj = block_lookup.get(block_id)
                block_name = info.get('block_name') or getattr(block_obj, 'block_name', f'BLK_{block_id}')
                reason = info.get('exclusion_reason') or '사유 미상'
                start_date = getattr(block_obj, 'assembly_start_date', None) if block_obj else None
                start_str = start_date.strftime('%Y-%m-%d') if start_date else '정보 없음'
                assembly_type = getattr(getattr(block_obj, 'assembly_type', None), 'value', info.get('assembly_type', '정보 없음'))
                port_starboard = getattr(getattr(block_obj, 'port_starboard', None), 'value', info.get('port_starboard', '정보 없음'))
                line_group = getattr(block_obj, 'line_group', None) if block_obj else None
                if not line_group:
                    line_group = info.get('line_group') or info.get('assembly_workshop_code')
                line_group_str = line_group if line_group else '정보 없음'
                bay_label = bay_history.get(block_id)
                if not bay_label and block_obj:
                    bay_label = getattr(getattr(block_obj, 'assigned_bay', None), 'value', '미할당')
                elif not bay_label:
                    bay_label = '미할당'
                workshop_code = getattr(block_obj, 'assembly_workshop_code', None) if block_obj else None
                if not workshop_code:
                    workshop_code = info.get('assembly_workshop_code', '정보 없음')
                print(
                    f"    - ID {block_id} ({block_name}), 착수일 {start_str}, 조립타입 {assembly_type}, 라인그룹 {line_group_str}, P/S {port_starboard}, 베이 {bay_label}, 작업장 {workshop_code}, 사유: {reason}"
                )
                # 동일 작업장 내 남은 미선택 블록도 함께 출력
                remaining_same_workshop = []
                if workshop_code != '정보 없음':
                    for blk in remaining_blocks:
                        code = getattr(blk, 'assembly_workshop_code', None)
                        if code == workshop_code and blk.block_id != block_id:
                            remaining_same_workshop.append(
                                (blk.block_id,
                                 getattr(blk, 'assembly_start_date', None),
                                 getattr(getattr(blk, 'assembly_type', None), 'value', None),
                                 getattr(getattr(blk, 'port_starboard', None), 'value', None))
                            )
                if remaining_same_workshop:
                    formatted = [
                        f"ID {bid} (착수일 {dt.strftime('%Y-%m-%d') if dt else '정보 없음'}, 타입 {atype}, P/S {ps})"
                        for bid, dt, atype, ps in remaining_same_workshop
                    ]
                    print(f"      ↳ 동일 작업장 남은 블록: {formatted}")
            else:
                print("   ❌ 제외된 후보 없음")
    
        except Exception as exc:
            print(f"   [진단 출력 중 오류 발생: {exc}]")
