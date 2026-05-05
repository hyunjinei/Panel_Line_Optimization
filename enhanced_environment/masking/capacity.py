# [AGENT-ADD] Split from masking/core.py for readability.

from datetime import datetime, date, time, timedelta
from typing import List, Tuple
from enhanced_environment.models import EnhancedBlock, AssemblyType

class CapacityMixin:
    @staticmethod
    def _get_block_capacity_load(block: EnhancedBlock) -> int:
        """Return SEAM+C/SEAM count used for capacity constraints."""
        return block.seam_count + getattr(block, 'c_seam_count', 0)
    

    def check_daily_capacity(self, blocks_to_add: List[EnhancedBlock], current_date: datetime) -> Tuple[bool, List[EnhancedBlock], str]:
        """
        🆕 일일 용량 체크 및 분할
    
        Args:
            blocks_to_add: 추가하려는 블록들
            current_date: 현재 날짜
    
        Returns:
            (용량_내_선택_가능, 선택된_블록들, 상태_메시지)
        """
        # 현재 날짜의 용량 상태 확인
        is_weekend = current_date.weekday() >= 5
        is_hot_season = self.calendar_manager.is_hot_season(current_date)
        is_holiday_eve = self.calendar_manager.is_holiday_eve(current_date)
    
        capacity_status = self.capacity_tracker.get_status(is_weekend, is_hot_season, is_holiday_eve, current_time=current_date)
        daily_limit = capacity_status["seam_limit"]
        current_used = capacity_status["seam_used"]
        remaining_capacity = daily_limit - current_used
    
        # 추가할 블록들의 총 심수 계산
        total_seams_to_add = sum(self._get_block_capacity_load(block) for block in blocks_to_add)
    
        if total_seams_to_add <= remaining_capacity:
            # 모든 블록 추가 가능
            return True, blocks_to_add, f"용량 내 모든 블록 선택 가능 ({total_seams_to_add}/{remaining_capacity}심)"
    
        # 용량 내에서 최대한 선택
        selected_blocks = []
        used_seams = 0
    
        # 🔥 수정: 심수 오름차순 정렬 대신 원래 순서 유지 (Assembly Decoding용)
        # sorted_blocks = sorted(blocks_to_add, key=lambda b: b.seam_count)
        # → 조립착수일 기준이나 다른 우선순위를 유지하기 위해 원래 순서 사용
        sorted_blocks = blocks_to_add  # 원래 순서 유지
    
        for block in sorted_blocks:
            block_load = self._get_block_capacity_load(block)
            if used_seams + block_load <= remaining_capacity:
                selected_blocks.append(block)
                used_seams += block_load
    
        if selected_blocks:
            status_msg = f"용량 제한으로 일부 선택 ({used_seams}/{remaining_capacity}심, {len(selected_blocks)}/{len(blocks_to_add)}블록)"
        else:
            status_msg = f"용량 초과로 선택 불가 ({total_seams_to_add}심 > {remaining_capacity}심)"
    
        return len(selected_blocks) > 0, selected_blocks, status_msg
    

    def _check_delivery_date(self, block: EnhancedBlock, current_time: datetime) -> bool:
        """P5#1: 납기 기반 착수일 체크"""
        # 현재 시간이 최대 착수일을 넘으면 False
        if current_time > block.max_start_date:
            return False
    
        # 조립 착수일이 설정되어 있으면, 오늘 날짜가 조립 착수일을 넘지 않았는지 확인
        if hasattr(block, 'assembly_start_date') and block.assembly_start_date:
            assembly_date = block.assembly_start_date.date()
            current_date = current_time.date()
            if current_date > assembly_date:
                return False
    
        return True
    

    def _check_material_ready(self, block: EnhancedBlock) -> bool:
        """P5#13: 자재 미입고 Skip"""
        return block.material_ready
    

    def _check_afternoon_start_time(self, block: EnhancedBlock, current_time: datetime) -> bool:
        """[AGENT-EDIT] P6#1,#2,#3 제거 이후 legacy 인터페이스 호환용 no-op."""
        return True
    

    def _get_afternoon_constraint_id(self, block: EnhancedBlock) -> str:
        """[AGENT-EDIT] P6#1,#2,#3 제거 이후 legacy ID 유지."""
        return "P6#1"
    

    def _check_holiday_eve_constraint(self, block: EnhancedBlock, current_time: datetime) -> Tuple[bool, str]:
        """
        P5#15: 명절 전날 야간(15:00 이후) 차단
    
        Args:
            block: 체크할 블록
            current_time: 현재 시간
    
        Returns:
            (통과 여부, 사유)
        """
        if not self.constraint_config.is_constraint_enabled("P5#15"):
            return True, "P5#15 제약조건 비활성화"
    
        # 명절 전날인지 확인
        if self.calendar_manager.is_holiday_eve(current_time):
            # 현재 시간이 야간인지 확인 (15:00 이후)
            shift_type = self.calendar_manager.get_shift_type(current_time)
            if shift_type == "NIGHT":
                # 다음날 공휴일명 가져오기 (가능하면)
                next_day = current_time.date() + timedelta(days=1)
                holiday_name = "명절"  # 기본값
                if hasattr(self.calendar_manager, 'kr_holidays') and self.calendar_manager.kr_holidays:
                    holiday_name = self.calendar_manager.kr_holidays.get(next_day, "명절")
    
                return False, f"P5#15: {holiday_name} 전날 야간(15:00 이후) 작업 불가 ({current_time.strftime('%H:%M')})"
    
        return True, "P5#15 제약조건 통과"
    

    def _check_integrated_capacity_constraints(self, block: EnhancedBlock, current_time: datetime) -> Tuple[bool, str]:
        """
        P5#8,9,10,16: 통합 달력+용량 제약조건
    
        우선순위:
        1. 달력 상태 파악 (혹서기, 주말)
        2. 기본 용량 한계 계산 (혹서기 반영)
        3. 현재 사용량 + 예상 사용량 체크
        4. P5#9 용량 확장 메커니즘 (평일만)
    
        Args:
            block: 체크할 블록
            current_time: 현재 시간
    
        Returns:
            (통과 여부, 사유)
        """
        if not self.constraint_config.is_constraint_enabled("P5#8"):
            return True, "P5#8,9,10,16 제약조건 비활성화"
    
        # ===============================================
        # 1단계: 달력 상태 파악
        # ===============================================
        # [AGENT-EDIT] Assembly 모드에서는 계획일 기준으로 혹서기/주말 여부를 판단 (오탐 방지)
        capacity_time = current_time
        if getattr(self, 'decoding_type', '') == "assembly" and getattr(self, 'current_panel_date', None):
            capacity_time = datetime.combine(self.current_panel_date, time(8, 0))
        is_weekend = self.calendar_manager.is_weekend(capacity_time)
        is_hot_season = self.calendar_manager.is_hot_season(capacity_time)

        # [AGENT-ADD] 특정 날짜 블록 수 제한 (현업 하드 제한)
        override_map = getattr(self.constraint_config, "daily_block_cap_overrides", {}) or {}
        override_limit = None
        if isinstance(override_map, dict):
            # 날짜 키 정규화 (YYYYMMDD)
            target_key = capacity_time.strftime("%Y%m%d")
            if target_key in override_map:
                override_limit = override_map[target_key]
            else:
                # 다양한 포맷 대응
                for raw_key, raw_limit in override_map.items():
                    try:
                        if isinstance(raw_key, datetime):
                            key_str = raw_key.strftime("%Y%m%d")
                        else:
                            key_str = str(raw_key).replace("-", "").replace("/", "").strip()
                        if key_str == target_key:
                            override_limit = raw_limit
                            break
                    except Exception:
                        continue

        if override_limit is not None:
            current_blocks = self.capacity_tracker.get_block_count(is_weekend)
            if current_blocks + 1 > int(override_limit):
                return False, f"일일 블록 수 제한: {current_blocks + 1}/{override_limit}개"
    
        # ===============================================
        # 2단계: 기본 용량 한계 계산 (혹서기 자동 반영)
        # ===============================================
        base_limit = self.capacity_tracker.get_capacity_limits(is_weekend, is_hot_season, current_time=capacity_time)
        weekday_capacity_enabled = self.constraint_config.is_constraint_enabled("P5#8")
        weekend_capacity_enabled = self.constraint_config.is_constraint_enabled("P5#10")
        hot_season_capacity_enabled = self.constraint_config.is_constraint_enabled("P5#16")
    
        # 제약조건 ID 생성
        constraint_ids = []
        if is_weekend and weekend_capacity_enabled:
            constraint_ids.append("P5#10")  # 주말 제약
        elif (not is_weekend) and weekday_capacity_enabled:
            constraint_ids.append("P5#8")   # 평일 제약
    
        if is_hot_season and hot_season_capacity_enabled:
            constraint_ids.append("P5#16")  # 혹서기 제약
    
        # ===============================================
        # 3단계: 현재 사용량 + 예상 사용량 체크
        # ===============================================
        block_load = self._get_block_capacity_load(block)
        current_seam = self.capacity_tracker.get_capacity_used(is_weekend)
        predicted_seam = current_seam + block_load
    
        # ===============================================
        # 4단계: 기본 용량 체크
        # ===============================================
        if predicted_seam <= base_limit:
            return True, f"용량 여유: {predicted_seam}/{base_limit}심"
    
        # ===============================================
        # 5단계: P5#9 (평일 72심 초과 시 17블록 이상 필수)
        # ===============================================
        if (not is_weekend and 
            self.constraint_config.is_constraint_enabled("P5#9") and
            predicted_seam > 72):
            current_blocks = self._get_current_block_count()
            if current_blocks + 1 < 17:
                if getattr(self.constraint_config, 'allow_capacity_relaxation', False):
                    return True, f"P5#9 완화: {predicted_seam}심 > 72심이지만 {current_blocks + 1}/17블록 → 허용"
                return False, f"P5#9: 72심 초과 시 17블록 이상 필요 (블록수: {current_blocks + 1})"
    
        # ===============================================
        # 6단계: 일반적인 용량 초과 (P5#9 적용 불가)
        # ===============================================
        if not constraint_ids:
            return True, f"용량 제약 비활성: {predicted_seam}/{base_limit}심"

        constraint_id_str = "+".join(constraint_ids)
        overflow_reason = f"{constraint_id_str}: 용량 초과 {predicted_seam}/{base_limit}심"
    
        # 추가 정보 제공
        if is_hot_season and hot_season_capacity_enabled and is_weekend:
            overflow_reason += " (혹서기 주말)"
        elif is_hot_season and hot_season_capacity_enabled:
            overflow_reason += " (혹서기 평일)"
        elif is_weekend and weekend_capacity_enabled:
            overflow_reason += " (주말)"
    
        return False, overflow_reason
