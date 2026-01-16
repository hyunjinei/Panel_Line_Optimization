# [AGENT-ADD] Split from masking/core.py for readability.

from datetime import datetime, date, timedelta
from typing import List, Dict, Tuple, Optional, Set
# [AGENT-EDIT] 타입 힌트 참조 해소를 위해 데이터 구조 타입을 명시적으로 임포트
from enhanced_environment.models import EnhancedBlock, AssemblyType, ConstraintViolation

class WorkshopWindowMixin:
    def _get_minimum_lead_days(self) -> int:
        config = getattr(self, 'constraint_config', None)
        lead_days = getattr(config, 'minimum_panel_lead_days', 0) if config else 0
        return max(0, lead_days or 0)
    

    def _is_overdue_block(self, block: EnhancedBlock, current_panel_date: date) -> bool:
        """Assembly 착수 기준 리드타임을 넘긴 블록인지 확인."""
        lead_days = self._get_minimum_lead_days()
        start_dt = getattr(block, 'assembly_start_date', None)
        if not isinstance(start_dt, datetime):
            return False
        return current_panel_date > (start_dt.date() + timedelta(days=lead_days))
    

    def _push_constraint_override(self, stage_definitions: List[Tuple[str, List[str]]]) -> Dict[str, bool]:
        """
        stage_definitions에서 사용하는 제약 키를 임시로 비활성화하고,
        원래 상태를 반환하여 이후 복구에 사용.
        """
        original = {}
        config = getattr(self, 'constraint_config', None)
        if not config:
            return original
    
        mapping = {
            "LINE_GROUP_CONSTRAINT": "enable_line_group_constraint",
            "P5#11": "enable_p5_11_fixed_line_mixing",
            "P5#12": "enable_p5_12_internal_external_mixing",
            "P5#8": "enable_p5_8_weekday_capacity",
            "P5#9": "enable_p5_9_block_count_check",
            "P5#10": "enable_p5_10_weekend_capacity",
            "P5#16": "enable_p5_16_hot_season_capacity",
            "P6#4": "enable_p6_4_cross_seam_mixing",
            "P5#15": "enable_p5_15_holiday_shift",
            "ROUTING_C_SEAM_SPACING": "enable_c_seam_spacing",
            # [AGENT-EDIT] Routing 계열 독립 제어
            "ROUTING_CURVED_SPACING": "enable_routing_curved_spacing",
            "ROUTING_HIGH_SEAM_SPACING": "enable_routing_high_seam_spacing",
            "ROUTING_WORKSHOP_ORDER": "enable_routing_workshop_order",
            "P6#1": "enable_p6_1_draft_afternoon",
            "P6#2": "enable_p6_2_cross_seam_afternoon",
            "P6#3": "enable_p6_3_dc_block_afternoon",
            "CONSECUTIVE_3BAY": "enable_consecutive_3bay_prevention",
        }
    
        for _, keys in stage_definitions:
            for key in keys:
                attr = mapping.get(key)
                if attr and hasattr(config, attr):
                    original[attr] = getattr(config, attr)
                    setattr(config, attr, False)
        return original
    

    def _pop_constraint_override(self, original: Dict[str, bool]) -> None:
        """_push_constraint_override로 변경한 제약 설정을 원복"""
        config = getattr(self, 'constraint_config', None)
        if not config:
            return
        for attr, val in original.items():
            setattr(config, attr, val)
    

    def _is_leadtime_guard_block(self, block: EnhancedBlock, current_date: date) -> bool:
        """리드타임 가드 대상(P6 강제) 여부 판정"""
        lead_days = self._get_minimum_lead_days()
        start_dt = getattr(block, 'assembly_start_date', None)
        if not isinstance(start_dt, datetime):
            return False
        delta = (start_dt.date() - current_date).days
        return 0 <= delta <= lead_days
    

    def _get_pre_relax_lead_floor(self) -> int:
        config = getattr(self, 'constraint_config', None)
        floor = getattr(config, 'pre_relax_min_lead_days', 0) if config else 0
        return max(0, floor or 0)
    

    def _get_relax_lead_floor(self) -> int:
        config = getattr(self, 'constraint_config', None)
        floor = getattr(config, 'relax_min_lead_days', 0) if config else 0
        return max(0, floor or 0)
    

    def _check_minimum_lead_time(
        self,
        block: EnhancedBlock,
        current_panel_date: date,
        override_required_days: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """Ensure assembly start date is at least configured days after current panel date."""
        if override_required_days is None:
            required_days = self._get_minimum_lead_days()
        else:
            required_days = max(0, override_required_days)
    
        if required_days <= 0:
            return True, None
    
        assembly_start = getattr(block, 'assembly_start_date', None)
        if not isinstance(assembly_start, datetime):
            return True, None
    
        delta_days = (assembly_start.date() - current_panel_date).days
        if delta_days < required_days:
            reason = (
                f"조립착수일까지 리드타임 {delta_days}일 < 최소 {required_days}일 "
                f"(착수 {assembly_start.date()}, 현재 {current_panel_date})"
            )
            return False, reason
        return True, None
    

    def _classify_block_type(self, block: EnhancedBlock) -> str:
        """
        블록을 3가지 타입으로 분류
    
        Args:
            block: 분류할 블록
    
        Returns:
            "노말", "21m초과", "P/S small" 중 하나
        """
        # 21m초과: 폭이 21m를 초과 (우선 체크)
        if block.width > 21.0:
            return "21m초과"
    
        # P/S small: P/S 쌍이면서 론지가 7 미만
        elif (hasattr(block, 'pair_block_id') and block.pair_block_id and 
              hasattr(block, 'longi_count') and block.longi_count < 7):
            return "P/S small"
    
        # 노말: 나머지 (21m 이하이면서 P/S small이 아님)
        else:
            return "노말"
    

    def _get_workshop_priority_key(self, block: EnhancedBlock) -> Optional[Tuple[str, str, str]]:
        from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
    
        if block is None:
            return None
    
        # assembly_type은 Enum이므로 문자열로 변환
        assembly_type_value = block.assembly_type.value if getattr(block, 'assembly_type', None) else None
    
        # 라인 그룹 및 작업장 코드 보정
        line_group = getattr(block, 'line_group', None)
        workshop_code = getattr(block, 'assembly_workshop_code', None)
    
        resolved_line_group, resolved_workshop_code = get_line_group_and_workshop_code(block)
    
        if not line_group:
            line_group = resolved_line_group
        if not workshop_code:
            workshop_code = resolved_workshop_code
    
        if not workshop_code:
            # 작업장 코드가 없다면 우선순위 키를 만들지 않음 (마스킹 대상에서 제외)
            return None
    
        workshop_root = self._normalize_workshop_code(workshop_code)
    
        return (
            assembly_type_value,
            str(line_group) if line_group else None,
            workshop_root if workshop_root else None,
        )
    
    ################################################################################################################################################################################################
    # fix 2025_09_05_17_10: 작업장 코드 접두부 정규화
    ################################################################################################################################################################################################

    def _normalize_workshop_code(self, workshop_code: str) -> str:
        code = workshop_code.strip()
        if '_' not in code:
            return code
    
        prefix, suffix = code.split('_', 1)
        if not suffix:
            return code
    
        sanitized_suffix = ''.join(ch for ch in suffix if ch.isalnum()) or suffix
    
        prefix_upper = prefix.upper()
    
        if prefix_upper == 'L':
            digits = ''.join(ch for ch in suffix if ch.isdigit())
            root_digit = digits[0] if digits else ''
            if root_digit:
                return f"{prefix_upper}_{root_digit}"
            return f"{prefix_upper}_{sanitized_suffix}".upper()
    
        if prefix_upper == 'F':
            return f"{prefix_upper}_{sanitized_suffix}".upper()
    
        # 기타 작업장은 기존 규칙 유지 (첫 숫자 위주 축약)
        first_digit = next((ch for ch in suffix if ch.isdigit()), '')
        if first_digit:
            return f"{prefix_upper}_{first_digit}"
    
        return f"{prefix_upper}_{sanitized_suffix}".upper()
    

    def _resolve_assembly_category(self, block: EnhancedBlock) -> Optional[str]:
        """Return normalized assembly category (라인 그룹 or FIXED)."""
        if block is None:
            return None
    
        assembly_type = getattr(block, 'assembly_type', None)
        if assembly_type == AssemblyType.FIXED:
            from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
            _, workshop_code = get_line_group_and_workshop_code(block)
            if workshop_code:
                return workshop_code
            return 'FIXED'
    
        if assembly_type == AssemblyType.LINE:
            line_group = getattr(block, 'line_group', None)
            if not line_group or not str(line_group).startswith('L'):
                from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
                line_group, _ = get_line_group_and_workshop_code(block)
            if line_group:
                return str(line_group)
            return 'LINE'
    
        if assembly_type is None:
            return None
    
        value = getattr(assembly_type, 'value', None)
        return str(value).upper() if value else str(assembly_type)
    
    ################################################################################################################################################################################################
    # fix 2025_09_05_15_45: 조립 작업장별 남은 블록 중 최소 착수일 계산
    ################################################################################################################################################################################################

    def _compute_workshop_min_dates(
        self,
        candidate_blocks: List[EnhancedBlock],
        selected_blocks: List[int]
    ) -> Dict[Tuple[str, str, str], datetime]:
        remaining_source = self.all_blocks if getattr(self, 'all_blocks', None) else candidate_blocks
        min_dates: Dict[Tuple[str, str, str], datetime] = {}
    
        for block in remaining_source:
            if block.block_id in selected_blocks:
                continue
    
            key = self._get_workshop_priority_key(block)
            if not key:
                continue
    
            assembly_date = block.assembly_start_date.date() if getattr(block, 'assembly_start_date', None) else None
            if assembly_date is None:
                continue
    
            if key not in min_dates or assembly_date < min_dates[key]:
                min_dates[key] = assembly_date
    
        return min_dates
    

    def _collect_workshop_priority_ids(
        self,
        candidate_blocks: List[EnhancedBlock],
        selected_blocks: List[int],
        analysis_map: Dict[int, Dict],
        violations: List[ConstraintViolation]
    ) -> Tuple[Set[int], Dict[int, str], int]:
        config = getattr(self, 'constraint_config', None)
        initial_window = getattr(config, 'workshop_priority_initial_window_days', 0) or 0
        max_window = getattr(config, 'workshop_priority_max_window_days', initial_window)
        if max_window < initial_window:
            max_window = initial_window
    
        workshop_min_dates = self._compute_workshop_min_dates(candidate_blocks, selected_blocks)
        remaining_blocks = [blk for blk in candidate_blocks if blk.block_id not in selected_blocks]
    
        allowed_ids: Set[int] = set()
        block_reasons: Dict[int, str] = {}
        window_used = initial_window
    
        window = initial_window
        final_failures: Dict[int, str] = {}
    
        while window <= max_window:
            allowed_ids.clear()
            final_failures = {}
    
            for block in remaining_blocks:
                allowed, reason = self._is_workshop_priority_satisfied(
                    block,
                    workshop_min_dates,
                    window
                )
    
                if allowed:
                    allowed_ids.add(block.block_id)
                elif reason:
                    final_failures[block.block_id] = reason
    
            if allowed_ids:
                window_used = window
                break
    
            window += 1
    
        if not allowed_ids:
            window_used = max_window
    
        # 최종 PASS/FAIL 기록 업데이트
        for block in remaining_blocks:
            analysis = analysis_map.get(block.block_id)
            if block.block_id in allowed_ids:
                if analysis:
                    analysis['workshop_priority'] = 'PASS'
                continue
    
            reason = final_failures.get(block.block_id)
            if analysis:
                analysis['workshop_priority'] = 'FAIL'
                analysis['is_available'] = False
                if reason:
                    analysis['exclusion_reason'] = reason
            if reason:
                block_reasons[block.block_id] = reason
                violations.append(ConstraintViolation(
                    constraint_id="ROUTING_WORKSHOP_PRIORITY",
                    message=reason,
                    block_id=block.block_id,
                    severity='ERROR'
                ))
    
        return set(allowed_ids), block_reasons, window_used
    

    def _collect_workshop_heads(
        self,
        workshop_buckets: Dict[str, List[EnhancedBlock]],
        selected_set: Set[int],
        current_panel_date: date,
        window_days: Optional[int] = None
    ) -> List[EnhancedBlock]:
        """Return earliest block per workshop filtered by initial window."""
        heads: List[EnhancedBlock] = []
        has_window = window_days is not None
        limit_date = current_panel_date + timedelta(days=max(0, window_days or 0))
    
        for key in sorted(workshop_buckets.keys()):
            bucket = workshop_buckets[key]
            for block in bucket:
                if block.block_id in selected_set:
                    continue
    
                if has_window:
                    start_dt = getattr(block, 'assembly_start_date', None)
                    if isinstance(start_dt, datetime) and start_dt.date() > limit_date:
                        continue
                heads.append(block)
                break
        return heads
    

    def _build_workshop_buckets(
        self,
        blocks: List[EnhancedBlock],
        selected_blocks: List[int]
    ) -> Dict[str, List[EnhancedBlock]]:
        selected_set = set(selected_blocks)
        buckets: Dict[str, List[EnhancedBlock]] = {}
    
        for block in blocks:
            if block.block_id in selected_set:
                continue
    
            workshop_code = getattr(block, 'assembly_workshop_code', None) or ''
            normalized = self._normalize_workshop_code(workshop_code) or 'UNKNOWN'
            buckets.setdefault(normalized, []).append(block)
    
        def sort_key(item: EnhancedBlock) -> Tuple[datetime, int]:
            start_date = getattr(item, 'assembly_start_date', None)
            if isinstance(start_date, datetime):
                return (start_date, item.block_id)
            return (datetime.max, item.block_id)
    
        for workshop in buckets:
            buckets[workshop].sort(key=sort_key)
    
        return buckets
    

    def _collect_workshop_window_candidates(
        self,
        workshop_buckets: Dict[str, List[EnhancedBlock]],
        selected_blocks: List[int],
        current_panel_date: date,
        window_days: int,
        all_blocks: List[EnhancedBlock]
    ) -> Tuple[List[EnhancedBlock], Set[int], Dict[int, str]]:
        allowed_ids: Set[int] = set()
        block_reasons: Dict[int, str] = {}
        primary_candidates: List[EnhancedBlock] = []
        secondary_candidates: List[EnhancedBlock] = []
        selected_set = set(selected_blocks)
        safe_window = max(0, window_days)
        window_limit = current_panel_date + timedelta(days=safe_window)
        deadline_buffer = getattr(self.constraint_config, 'deadline_force_buffer_days', 0) if self.constraint_config else 0
        deadline_limit = current_panel_date + timedelta(days=max(0, deadline_buffer))
    
        def is_deadline_soon(block: EnhancedBlock) -> bool:
            start_date = getattr(block, 'assembly_start_date', None)
            return isinstance(start_date, datetime) and start_date.date() <= deadline_limit
    
        allow_deadline_lead_override = getattr(self.constraint_config, 'allow_deadline_lead_override', True)
    
        def sort_key(item: EnhancedBlock) -> Tuple[int, datetime, int]:
            start_date = getattr(item, 'assembly_start_date', None)
            deadline_priority = 0 if is_deadline_soon(item) else 1
            if isinstance(start_date, datetime):
                return (deadline_priority, start_date, item.block_id)
            return (deadline_priority, datetime.max, item.block_id)
        pre_relax_floor = self._get_pre_relax_lead_floor()
        effective_lead = max(pre_relax_floor, self._get_minimum_lead_days() - safe_window)
    
        for workshop_code in sorted(workshop_buckets.keys()):
            bucket = workshop_buckets[workshop_code]
            primary_added = False
            for block in bucket:
                if block.block_id in selected_set:
                    continue
                lead_pass, lead_reason = self._check_minimum_lead_time(block, current_panel_date, override_required_days=effective_lead)
                deadline_close = is_deadline_soon(block)
                if not lead_pass and deadline_close and allow_deadline_lead_override:
                    lead_pass = True
                    lead_reason = None
                    if self._is_trace_block(block.block_id):
                        print(f"[TRACE][WorkshopWindow] block {block.block_id} lead override due to deadline")
                if not lead_pass:
                    block_reasons[block.block_id] = lead_reason or "리드타임 부족"
                    if self._is_trace_block(block.block_id):
                        print(
                            f"[TRACE][WorkshopWindow] block {block.block_id} rejected (lead>= {effective_lead}): {lead_reason}"
                        )
                    continue
    
                start_date = getattr(block, 'assembly_start_date', None)
                deadline_close = is_deadline_soon(block)
                if start_date and start_date.date() > window_limit and not deadline_close:
                    block_reasons[block.block_id] = (
                        f"작업장 {workshop_code}: {start_date.strftime('%m-%d')} > 허용 {window_limit.strftime('%m-%d')}"
                    )
                    if self._is_trace_block(block.block_id):
                        print(
                            f"[TRACE][WorkshopWindow] block {block.block_id} ({getattr(block, 'block_name', '')}) "
                            f"start {start_date.date()} > limit {window_limit} at window {safe_window}"
                        )
                    continue
                elif start_date and start_date.date() > window_limit and deadline_close:
                    if self._is_trace_block(block.block_id):
                        print(
                            f"[TRACE][WorkshopWindow] block {block.block_id} allowed despite window due to deadline (start {start_date.date()}, limit {window_limit})"
                        )
    
                ps_pass, ps_reason = self._check_ps_order_constraint(block, selected_blocks, all_blocks)
                if not ps_pass:
                    block_reasons[block.block_id] = ps_reason
                    if self._is_trace_block(block.block_id):
                        print(
                            f"[TRACE][WorkshopWindow] block {block.block_id} P/S order blocked: {ps_reason}"
                        )
                    continue
    
                allowed_ids.add(block.block_id)
                if not primary_added:
                    primary_candidates.append(block)
                    primary_added = True
                else:
                    secondary_candidates.append(block)
    
                if self._is_trace_block(block.block_id):
                    print(
                        f"[TRACE][WorkshopWindow] block {block.block_id} allowed in window {safe_window} "
                        f"(workshop {workshop_code}, start {start_date.date() if start_date else 'N/A'})"
                    )
    
            if not primary_added and bucket:
                first_block = bucket[0]
                start_date = getattr(first_block, 'assembly_start_date', None)
                if start_date and start_date.date() > window_limit:
                    block_reasons[first_block.block_id] = (
                        f"작업장 {workshop_code}: {start_date.strftime('%m-%d')} > 허용 {window_limit.strftime('%m-%d')}"
                    )
    
        ordered_candidates = sorted(primary_candidates, key=sort_key)
        ordered_candidates.extend(sorted(secondary_candidates, key=sort_key))
    
        return ordered_candidates, allowed_ids, block_reasons
    

    def _select_workshop_priority_blocks(
        self,
        blocks: List[EnhancedBlock],
        allowed_ids: Set[int]
    ) -> List[EnhancedBlock]:
        if not allowed_ids:
            return []
    
        buckets: Dict[str, List[EnhancedBlock]] = {}
    
        for block in blocks:
            if block.block_id not in allowed_ids:
                continue
            if block.block_id in getattr(self, 'selected_blocks_set', set()):
                continue
    
            workshop_code = getattr(block, 'assembly_workshop_code', None)
            normalized = self._normalize_workshop_code(workshop_code) if workshop_code else None
            if not normalized:
                continue
    
            buckets.setdefault(normalized, []).append(block)
    
        if not buckets:
            return []
    
        def sort_key(item: EnhancedBlock) -> Tuple[datetime, int]:
            start_date = getattr(item, 'assembly_start_date', None)
            if isinstance(start_date, datetime):
                return (start_date, item.block_id)
            return (datetime.max, item.block_id)
    
        prioritized: List[EnhancedBlock] = []
        used_ids: Set[int] = set()
    
        for workshop_code in sorted(buckets.keys()):
            bucket = buckets[workshop_code]
            bucket.sort(key=sort_key)
            head = bucket[0]
            prioritized.append(head)
            used_ids.add(head.block_id)
    
        # 나머지 허용 블록들을 assembly_start_date 순으로 이어 붙임
        remaining_allowed: List[EnhancedBlock] = []
        for bucket in buckets.values():
            for block in bucket[1:]:
                if block.block_id in used_ids:
                    continue
                remaining_allowed.append(block)
                used_ids.add(block.block_id)
    
        remaining_allowed.sort(key=sort_key)
        prioritized.extend(remaining_allowed)
    
        # 기존 blocks 목록에서 아직 포함되지 않은 나머지 블록들을 그대로 이어 붙임
        for block in blocks:
            if block.block_id in used_ids:
                continue
            prioritized.append(block)
            used_ids.add(block.block_id)
    
        return prioritized
    
    ################################################################################################################################################################################################
    # fix 2025_09_05_15_45: 조립 작업장 착수일 우선 마스킹 체크
    ################################################################################################################################################################################################

    def _is_workshop_priority_satisfied(
        self,
        block: EnhancedBlock,
        workshop_min_dates: Dict[Tuple[str, str, str], datetime],
        allowed_window_days: int = 0
    ) -> Tuple[bool, Optional[str]]:
        key = self._get_workshop_priority_key(block)
        if not key:
            return True, None
    
        min_date = workshop_min_dates.get(key)
        if not min_date:
            return True, None
    
        block_date = block.assembly_start_date.date() if getattr(block, 'assembly_start_date', None) else None
        if not block_date:
            return True, None
    
        allowed_date = min_date + timedelta(days=max(0, allowed_window_days))
    
        if block_date > allowed_date:
            reason = (
                f"조립 작업장 우선 마스킹: {min_date.strftime('%Y-%m-%d')}~{allowed_date.strftime('%Y-%m-%d')} 범위 블록 우선"
            )
            return False, reason
    
        return True, None
