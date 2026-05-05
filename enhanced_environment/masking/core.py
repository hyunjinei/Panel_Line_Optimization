"""
Enhanced Panel Block Shop - Action Masking for PFSP
==================================================

PFSP(Permutation Flow Shop) 방식을 위한 액션 마스킹

두 단계로 구분:
1. SEQUENCE_DECISION: 블록 순서 결정 (처음 한번만)
2. BRANCH_SELECTION: 분기점에서 베이 선택 (Line 1 → Line 2 전환시)

P/S 제약이 순서 기반으로 대폭 단순화됨
"""

# [AGENT-ADD] action_masking.py moved to masking/core.py.

import os
import logging
from datetime import datetime, time, timedelta, date
from typing import List, Dict, Tuple, Optional, Any, Set
from itertools import combinations
import numpy as np
import math
import re  # [AGENT-ADD] 한글/영문 완화 키 정규화

from enhanced_environment.models import (
    EnhancedBlock,
    EnvironmentState,
    AssemblyType,
    PortStarboard,
    WorkshopType,
    BayType,
    ConstraintViolation,
    ActionMask,
    ProcessPhase,
    SequenceState
)
from enhanced_environment.constraints.managers import (
    CapacityTracker,
    PSBlockManager,
    BayStateTracker,
    CalendarManager
)
from enhanced_environment.constraints import ConstraintConfig  # 설정 클래스 추가
from enhanced_environment.bay.assigner import preview_assign_bay



from enhanced_environment.masking.debug import DebugMixin
from enhanced_environment.masking.capacity import CapacityMixin
from enhanced_environment.masking.routing import RoutingMixin
from enhanced_environment.masking.ps_mixing import PSMixingMixin
from enhanced_environment.masking.workshop_window import WorkshopWindowMixin
from enhanced_environment.masking.scoring import ScoringMixin
from enhanced_environment.masking.saw_time import SawTimeMixin

class ConstraintChecker(DebugMixin, CapacityMixin, RoutingMixin, PSMixingMixin, WorkshopWindowMixin, ScoringMixin, SawTimeMixin):
    """
    제약조건 검증 및 액션 마스킹 통합 클래스
    
    전체 제약조건을 체계적으로 검증하고 액션 마스킹을 제공합니다.
    """
    def __init__(self, 
                 capacity_tracker: CapacityTracker,
                 ps_manager: PSBlockManager,
                 bay_tracker: BayStateTracker,
                 calendar_manager: CalendarManager,
                 constraint_config: 'ConstraintConfig',
                 metadata: Dict = None):  # 메타데이터 매개변수 추가
        self.capacity_tracker = capacity_tracker
        self.ps_manager = ps_manager
        self.bay_tracker = bay_tracker
        self.calendar_manager = calendar_manager
        self.constraint_config = constraint_config
    
        # 메타데이터 저장 및 처리
        self.metadata = metadata or {}
        self.constraint_application_map = self.metadata.get('constraint_application_map', {})
        self.bay_assignment_strategy = self.metadata.get('bay_assignment_strategy', {})
        self.constraint_conflicts = self.metadata.get('constraint_conflicts', {})
    
        # PFSP 상태 추적
        self.sequence_state = SequenceState()
    
        # 🆕 Assembly Decoding 상태 추적
        self.decoding_type = "random"  # "random" 또는 "assembly"
        self.current_panel_date = None  # 현재 판넬 착수일
        self.all_blocks = []  # 전체 블록 풀
        self.selected_blocks_set = set()  # 이미 선택된 블록들
        self.selection_history: List[int] = []  # 선택 순서 기록
        self.relaxation_level = 0  # 제약 완화 단계 (0-4)
    
        # P5#11,12,P6#4: 혼합 배치 상태 추적
        self.last_assembly_type = None
        self.cross_seam_consecutive_count = 0
    
        # P5#6: 라인 B 작업장 3seam+ FAB 간격 추적
        self.line_b_fab_interval_count = 0
    
        # [AGENT-ADD] 특정 블록 추적용(Debug)
        self.trace_block_ids: Set[int] = set()
        trace_env = os.environ.get("PBS_TRACE_BLOCKS", "")
        if trace_env:
            for token in trace_env.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    self.trace_block_ids.add(int(token))
                except ValueError:
                    continue
    
        # 제약조건 체크 통계
        self.constraint_checks = {
            # P5 제약조건
            "P5#1": 0, "P5#3": 0, "P5#4": 0, "P5#6": 0, "P5#8": 0, 
            "P5#9": 0, "P5#10": 0, "P5#11": 0, "P5#12": 0, "P5#11,12": 0, "P5#13": 0,
            # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거 이후 P6는 pure cross seam 혼합만 유지한다.
            "P6#4": 0,
            # P7 제약조건
            "P7#1": 0, "P7#2": 0, "P7#3": 0, "P7#7": 0, "P7#8": 0, 
            "P7#10": 0, "P7#11": 0, "P7#12": 0,
        }
    
        self.violation_counts = {key: 0 for key in self.constraint_checks.keys()}

    # ========================================
    # [AGENT-ADD] Relax order helpers
    # ========================================

    def _normalize_relax_key(self, raw: str) -> str:
        """완화 키 정규화 (한글/영문 혼용 지원)."""
        if raw is None:
            return ""
        text = str(raw).strip().lower()
        return re.sub(r"[^a-z0-9가-힣]+", "", text)

    def _resolve_strict_rules(self) -> Set[str]:
        """strict_rules(한글/영문)를 constraint id set으로 변환."""
        strict_raw = getattr(self.constraint_config, "strict_rules", []) or []
        # [AGENT-ADD] scope별 하드 제약도 strict_rules로 통합
        scope = getattr(self.constraint_config, "constraint_scope", "")
        if scope == "start_date":
            strict_raw = list(strict_raw) + (getattr(self.constraint_config, "hard_constraints_start_date", []) or [])
        elif scope == "assembly":
            strict_raw = list(strict_raw) + (getattr(self.constraint_config, "hard_constraints_assembly", []) or [])
        strict_set: Set[str] = set()

        mapping = {
            "작업장순서": "ROUTING_WORKSHOP_ORDER",
            "workshoporder": "ROUTING_WORKSHOP_ORDER",
            "cseam": "ROUTING_C_SEAM_SPACING",
            "곡판": "ROUTING_CURVED_SPACING",
            "고심수": "ROUTING_HIGH_SEAM_SPACING",
            "3bay": "CONSECUTIVE_3BAY",
            "라인그룹": "LINE_GROUP_CONSTRAINT",
            "라인고정간격": "LINE_GROUP_CONSTRAINT",
            "라인혼합": ["P5#11", "P5#12"],
            "용량": ["P5#8", "P5#9", "P5#10", "P5#16"],
            # [AGENT-ADD] P/S 연속 키 확장
            "ps연속": ["P5#3", "P5#4"],
            "ps연속라인": "P5#3",
            "ps연속고정": "P5#4",
        }

        for raw in strict_raw:
            norm = self._normalize_relax_key(raw)
            if not norm:
                continue
            if norm in mapping:
                mapped = mapping[norm]
                if isinstance(mapped, list):
                    strict_set.update(mapped)
                else:
                    strict_set.add(mapped)
                continue
            # 직접 constraint id 전달 지원
            strict_set.add(str(raw).strip())

        return strict_set

    def _expand_constraint_aliases(self, constraint_ids: List[str]) -> Set[str]:
        """[AGENT-ADD] 묶인 constraint id를 개별 id까지 확장한다."""
        expanded: Set[str] = set()
        for raw in constraint_ids or []:
            text = str(raw).strip()
            if not text:
                continue
            expanded.add(text)
            if text == "P5#3,4":
                expanded.update(["P5#3", "P5#4"])
            elif text == "P5#8,9,10,16":
                expanded.update(["P5#8", "P5#9", "P5#10", "P5#16"])
            elif text == "P5#11,12":
                expanded.update(["P5#11", "P5#12"])
            elif text == "P7#3,4":
                expanded.update(["P7#3", "P7#4"])
        return expanded

    def _has_strict_constraint_failure(self, failed_constraints: List[str], strict_rules: Set[str]) -> bool:
        """[AGENT-ADD] strict rule set과 failed constraints를 동일 기준으로 비교한다."""
        if not strict_rules:
            return False
        return bool(self._expand_constraint_aliases(failed_constraints) & strict_rules)

    def _get_relax_mapping(self) -> Dict[str, Tuple[str, List[str]]]:
        """완화 키 매핑 테이블."""
        return {
            "3bay완화": ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            "3bay": ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            "3베이": ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            "라인고정간격": ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
            "라인그룹": ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
            "라인연속": ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
            "라인혼합": ("P5#11,12 완화", ["P5#11", "P5#12"]),
            "혼합": ("P5#11,12 완화", ["P5#11", "P5#12"]),
            "p511": ("P5#11,12 완화", ["P5#11", "P5#12"]),
            "cseam": ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
            "cseam완화": ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
            "곡판": ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
            "곡판간격": ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
            "고심수": ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
            "고심수간격": ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
            "p64": ("P6#4 완화", ["P6#4"]),
            "p615": ("P5#15 완화", ["P5#15"]),
            "p515": ("P5#15 완화", ["P5#15"]),
            "용량": ("P5#8,9,10,16 완화", ["P5#8", "P5#9", "P5#10", "P5#16"]),
            "용량완화": ("P5#8,9,10,16 완화", ["P5#8", "P5#9", "P5#10", "P5#16"]),
        }

    def _resolve_relax_stages(
        self,
        default_stages: List[Tuple[str, List[str]]],
        *,
        relax_list: Optional[List[str]] = None,
        label_prefix: str = "",
    ) -> List[Tuple[str, List[str]]]:
        """config.relax_order를 반영해 완화 단계 순서를 재구성."""
        custom = relax_list if relax_list is not None else (getattr(self.constraint_config, "relax_order", []) or [])
        if not custom:
            return default_stages

        strict_rules = self._resolve_strict_rules()
        allow_capacity_relax = getattr(self.constraint_config, "allow_capacity_relaxation", False)
        mapping = self._get_relax_mapping()

        resolved: List[Tuple[str, List[str]]] = []
        for raw in custom:
            norm = self._normalize_relax_key(raw)
            if not norm:
                continue
            if norm in mapping:
                stage_name, keys = mapping[norm]
            else:
                # 직접 constraint id 제공 시
                stage_name = str(raw).strip()
                keys = [str(raw).strip()]

            # 용량 완화는 옵션일 때만 허용
            if not allow_capacity_relax and any(k in ["P5#8", "P5#9", "P5#10", "P5#16"] for k in keys):
                continue

            filtered = [k for k in keys if k not in strict_rules]
            if not filtered:
                continue

            if label_prefix:
                stage_name = f"{label_prefix}{stage_name}"
            resolved.append((stage_name, filtered))

        return resolved or default_stages
    
    ################################################################################################################################################################################################
    # [AGENT-ADD] Debug flag resolver: allows env override without CLI flags
    ################################################################################################################################################################################################

    def set_blocks_dict(self, blocks_dict: Dict[int, EnhancedBlock]):
        """
        블록 딕셔너리 설정 (연속성 체크를 위해 필요)
    
        Args:
            blocks_dict: 블록 ID -> 블록 객체 매핑
        """
        self.blocks_dict = blocks_dict
    

    def get_next_available_blocks(
        self,
        blocks: List[EnhancedBlock],
        current_time: datetime,
        selected_blocks: List[int],
        last_assembly_type: AssemblyType = None
    ) -> Tuple[List[int], List[ConstraintViolation], List[Dict]]:
        """
        완전 재구성: 올바른 제약조건 우선순위 적용
    
        🆕 Assembly Decoding 모드 지원:
        - decoding_type = "random": 기존 방식 (날짜별 그룹)
        - decoding_type = "assembly": 조립착수일 기준 전체 풀 선택
    
        새로운 우선순위:
         최우선: P5#15 (명절 전날 야간 차단) - 물리적 불가능
         1순위: P5#8,9,10,16 (통합 용량 제약) - 환경 기본 한계
         2순위: P5#3,4 (P/S 순서 제약) - 블록 순서 논리
         3순위: P5#11,12 (Assembly Type 제약) - 작업 효율
         4순위: P6#4 (Cross seam 제약) - 품질 관리
        """
        ####################################################################################################################################
        # [AGENT-EDIT] 착수일 고정 휴리스틱 전용 경량 마스킹 + 단계적 완화
        #  - 날짜 창/리드타임 확장 없음
        #  - 선택 불가 시 핵심 제약을 순차 완화 (RELAX_STAGE) → 조립착수일 휴리스틱과 동일한 위반 로깅 형식 유지
        #  - 기존 15시 일괄 해제 로직은 비활성화 (정확성 우선)
        ####################################################################################################################################
        # [AGENT-ADD] 착수일 경로 제약 스코프 적용
        if getattr(self, "constraint_config", None):
            self.constraint_config.set_constraint_scope("start_date")

        self._set_current_selected_blocks(selected_blocks)
    
        violations: List[ConstraintViolation] = []
        block_analysis: List[Dict] = []
    
        today = current_time.date()
        candidate_blocks = [
            blk for blk in blocks
            if getattr(blk, 'assembly_start_date', None)
            and getattr(blk.assembly_start_date, 'date', lambda: None)() == today
            and blk.block_id not in selected_blocks
        ]
        if not candidate_blocks:
            candidate_blocks = [blk for blk in blocks if blk.block_id not in selected_blocks]
    
        for blk in candidate_blocks:
            block_analysis.append({
                'block_id': blk.block_id,
                'block_name': getattr(blk, 'block_name', f'BLK_{blk.block_id}'),
                'assembly_type': blk.assembly_type.value,
                'port_starboard': blk.port_starboard.value,
                'is_available': False,
                'exclusion_reason': '',
                'inclusion_reason': '',
                'three_bay_check': '',
                # [AGENT-ADD] 완화 단계/대상 정보 (CSV 기록용)
                'relax_level': 0,
                'relax_stage': '',
                'relax_constraints': [],
                'relax_targets': [],
                'relax_target_count': 0,
                # [AGENT-ADD] 최후 선택(최소 위반) 단계 기록
                'final_choice_flag': False,
                'final_violation_list': [],
                'final_violation_count': 0,
                'constraint_checks': {}
            })
    
        def _evaluate(stage_label: str, override_keys: Optional[List[str]] = None) -> List[int]:
            original = {}
            try:
                if override_keys:
                    original = self._push_constraint_override([(stage_label, override_keys)])
                evaluated = self._evaluate_blocks_stage(
                    candidate_blocks,
                    stage_label,
                    selected_blocks,
                    block_analysis,
                    current_time,
                    last_assembly_type,
                    blocks,
                    constraints_to_relax=override_keys,
                    violations=violations
                )
                return [b.block_id for b in evaluated]
            finally:
                if original:
                    self._pop_constraint_override(original)
    
        available_block_ids = _evaluate("착수일고정-기본", override_keys=None)
    
        if not available_block_ids:
            default_relax_stages = [
                ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
                ("P5#11,12 완화", ["P5#11", "P5#12"]),
                ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
                ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
                ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
                # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거: 완화 단계에서도 제외
                ("P6#4 완화", ["P6#4"]),
                ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            ]
            relax_list = getattr(self.constraint_config, "relax_order_start_date", []) or getattr(self.constraint_config, "relax_order", [])
            relax_stages = self._resolve_relax_stages(default_relax_stages, relax_list=relax_list)
            for stage_name, keys in relax_stages:
                available_block_ids = _evaluate(f"착수일고정-{stage_name}", override_keys=keys)
                if available_block_ids:
                    break
    
        if not available_block_ids:
            remaining = [b.block_id for b in blocks if b.block_id not in selected_blocks]
            if remaining:
                violations.append(ConstraintViolation(
                    constraint_id="EMERGENCY_RELEASE",
                    message="모든 단계 실패 → 남은 블록 중 임의 선택",
                    block_id=remaining[0],
                    severity="WARNING"
                ))
                # [AGENT-ADD] 최후 선택(임의 선택) 표시
                for info in block_analysis:
                    if info.get('block_id') == remaining[0]:
                        info['is_available'] = True
                        info['final_choice_flag'] = True
                        info['final_violation_list'] = ['EMERGENCY_RELEASE']
                        info['final_violation_count'] = 1
                available_block_ids = remaining
    
        return available_block_ids, violations, block_analysis

    def get_next_available_blocks_assembly(
        self,
        blocks: List[EnhancedBlock],
        current_time: datetime,
        selected_blocks: List[int],
        last_assembly_type: AssemblyType = None,
        include_relax_candidates: bool = False,
        panel_date: Optional[date] = None,  # [AGENT-ADD] 작업일 기준 날짜 오버라이드
        previous_machine_state: Optional[Dict] = None,  # [AGENT-EDIT] CT 동기화용 이전 머신 상태
        current_bay_assignments: Optional[Dict[int, BayType]] = None,  # [AGENT-EDIT] 확정된 베이 배정 공유
        current_day_selected_blocks: Optional[List[int]] = None  # [AGENT-EDIT] 당일 선택 시퀀스 (CT 날짜 리셋 반영)
    ) -> Tuple[List[int], List[ConstraintViolation], List[Dict]]:
        """
        Assembly Decoding 전용 블록 선택 로직 (조립착수일 우선순위 기반)
    
        새로운 우선순위:
        1. 조립착수일 우선순위 마스킹 (급한 것부터 순차 해제)
        2. 연속 3판 B베이 방지
        3. 나머지 기본 제약조건들
        """
        violations = []
        block_analysis = []
        constraint_config = getattr(self, 'constraint_config', None)
        # [AGENT-ADD] 조립착수일 경로 제약 스코프 적용
        if constraint_config:
            constraint_config.set_constraint_scope("assembly")
        debug_enabled = self._resolve_debug_flag(constraint_config)
        # [AGENT-ADD] 상세 로그는 PBS_DEBUG_VERBOSE=1일 때만 출력
        debug_verbose = debug_enabled and os.environ.get("PBS_DEBUG_VERBOSE", "").strip().lower() in {"1", "true", "on", "yes"}
        # [AGENT-EDIT] 비상 모드 제거로 인해 사용하지 않음
        stage_history: List[Tuple[str, Any]] = []
    
        # ==== [AGENT-EDIT BEGIN: 현재 패널 날짜/시간 동기화] ====
        # 호출자가 전달한 시간/날짜를 내부 상태에 저장하여 연속 선택 시 참조 값이 누적되지 않도록 한다.
        self.current_panel_date = panel_date if panel_date else current_time.date()
        self.current_time = current_time
        # [AGENT-EDIT] 현재까지 선택된 순서를 SequenceState에 동기화하여 CT 계산 시 대기열을 반영
        try:
            self.set_sequence(selected_blocks)
        except Exception as exc:
            raise RuntimeError("Assembly masking sequence_state 동기화 실패") from exc
        # [AGENT-EDIT] 당일 시퀀스 별도 보관 (P6 시간 계산 시 날짜 리셋 반영)
        self.current_day_selected_blocks = current_day_selected_blocks or []
        # ==== [AGENT-EDIT END] ====
    
        # ✅ 항상 초기화
        constraints_to_relax: List[str] = []
    
        # print(f"   🔧 Assembly 전용 로직 시작")
    
        # 🔍 디버깅: 전체 후보 블록 수 확인
        total_candidates = [block for block in blocks if block.block_id not in selected_blocks]
        self._debug_candidate_stage(debug_enabled, "총 후보 블록", total_candidates, stage_history)
        # print(f"   📊 전체 후보 블록: {len(total_candidates)}개 (이미 선택된 {len(selected_blocks)}개 제외)")
    
        # 🔍 디버깅: 선택된 블록 ID들 확인
        # if selected_blocks:
        #     print(f"   🔍 이미 선택된 블록들: {selected_blocks[-10:]}..." if len(selected_blocks) > 10 else f"   🔍 이미 선택된 블록들: {selected_blocks}")
    
        # 🆕 모든 블록에 대한 분석 정보 수집 (Action Masking과 동일한 형식)
        for block in blocks:
            if block.block_id in selected_blocks:
                continue
    
            # 기본 분석 정보
            analysis = {
                'block_id': block.block_id,
                'assembly_type': block.assembly_type.value,
                'port_starboard': block.port_starboard.value,
                'assembly_start_date': block.assembly_start_date.strftime('%Y-%m-%d'),
                'is_available': False,
                'inclusion_reason': '',
                'exclusion_reason': '',
                'constraint_checks': {},
                'masking_stage': '',
                'three_bay_check': '',
                'ps_forced': False,
                'relax_level': 0,
                # [AGENT-ADD] 완화 단계/대상 정보 (CSV 기록용)
                'relax_stage': '',
                'relax_constraints': [],
                'relax_targets': [],
                'relax_target_count': 0,
                # [AGENT-ADD] 최후 선택(최소 위반) 단계 기록
                'final_choice_flag': False,
                'final_violation_list': [],
                'final_violation_count': 0,
                #############################################################
                # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                #############################################################
                'has_curved_plate': getattr(block, 'has_curved_plate', False),
                #############################################################
                # fix 2025_09_05_15_45: 조립 작업장별 착수일 우선 마스킹 상태 추적
                #############################################################
                #############################################################
                # fix 2025_09_05_17_10: 조립 작업장 루트 우선 마스킹 상태 추적
                #############################################################
                'workshop_priority': ''
            }
    
            # 가용성 체크 (여기서는 간단하게 처리)
            analysis['is_available'] = True
            analysis['inclusion_reason'] = '모든 제약조건 통과'
    
            block_analysis.append(analysis)
    
        analysis_map = {analysis['block_id']: analysis for analysis in block_analysis}
    
        ############################################################################################################
        # [AGENT-EDIT] 리드타임 기반 층 분리 (Emergency / Urgent / Normal)
        ############################################################################################################
        min_lead_days = self._get_minimum_lead_days()
    
        def _slack_days(b: EnhancedBlock) -> int:
            start_dt = getattr(b, 'assembly_start_date', None)
            if not isinstance(start_dt, datetime):
                return 10_000
            return (start_dt.date() - self.current_panel_date).days
    
        # 층별 분리 (모든 미선택 블록 대상)
        remaining_unscheduled = [blk for blk in blocks if blk.block_id not in selected_blocks]
        remaining_all_unscheduled = remaining_unscheduled
        # [AGENT-ADD] 작업장 순서 하드 제약: 전체 미선택 기준으로 작업장별 최소 착수일 블록만 후보로 유지
        from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
        workshop_min_by_key: Dict[str, Tuple[date, List[EnhancedBlock]]] = {}
        undated_blocks: List[EnhancedBlock] = []
        for blk in remaining_all_unscheduled:
            line_group, workshop_code = get_line_group_and_workshop_code(blk)
            workshop_key = workshop_code or line_group
            start_dt = getattr(blk, 'assembly_start_date', None)
            if not workshop_key or not isinstance(start_dt, datetime):
                undated_blocks.append(blk)
                continue
            blk_date = start_dt.date()
            entry = workshop_min_by_key.get(workshop_key)
            if entry is None or blk_date < entry[0]:
                workshop_min_by_key[workshop_key] = (blk_date, [blk])
            elif blk_date == entry[0]:
                entry[1].append(blk)
        workshop_heads_full: List[EnhancedBlock] = undated_blocks + [
            b for _, (_, blks) in workshop_min_by_key.items() for b in blks
        ]
        workshop_head_ids = {b.block_id for b in workshop_heads_full}
        # [AGENT-EDIT] workshop-head masking 토글이 assembly 경로의 선필터에도 동일하게 적용되도록 맞춘다.
        # 이전에는 여기서 무조건 workshop head만 남겨 뒤쪽 토글 분기가 사실상 무효였다.
        workshop_head_masking = bool(getattr(self.constraint_config, 'enable_workshop_head_masking', True))
        remaining_unscheduled = workshop_heads_full if workshop_head_masking else remaining_all_unscheduled
    
        # ==== [AGENT-EDIT BEGIN: 용량 하드 제약 선필터] ====
        # [AGENT-EDIT] 강제/PS-only와 무관하게 일일 용량을 넘길 수 없는 경우 당일 선택을 종료
        #   - allow_capacity_relaxation=False일 때만 적용 (기본: 하드 제약)
        allow_capacity_relax = getattr(self.constraint_config, 'allow_capacity_relaxation', False)
        capacity_filtered: List[EnhancedBlock] = []
        if remaining_unscheduled and not allow_capacity_relax:
            for blk in remaining_unscheduled:
                capacity_ok, capacity_reason = self._check_integrated_capacity_constraints(blk, current_time)
                if capacity_ok:
                    capacity_filtered.append(blk)
                else:
                    analysis = analysis_map.get(blk.block_id)
                    if analysis:
                        analysis['is_available'] = False
                        analysis['exclusion_reason'] = f"용량 제약 위반: {capacity_reason}"
                        analysis['constraint_checks']['P5#8_9_10_16_capacity'] = {
                            'result': 'FAIL',
                            'reason': capacity_reason
                        }
            if not capacity_filtered:
                if debug_enabled:
                    print("⚠️ 용량 제약으로 당일 선택 불가 → 다음날로 전환")
                return [], violations, block_analysis
            remaining_unscheduled = capacity_filtered
        # ==== [AGENT-EDIT END] ====
    
        # ==== [AGENT-EDIT BEGIN: P/S 즉시 추종 강제 제거] ====
        # 논문 실험 기준으로 P 이후 S 즉시 추종 강제는 제거한다.
        # P/S 순서 자체는 _check_ps_order_constraint()가 계속 유지한다.
        # ==== [AGENT-EDIT END] ====
        leadtime_layers_enabled = bool(getattr(self.constraint_config, 'enable_assembly_start_leadtime_layers', True))
        if leadtime_layers_enabled:
            emergency_blocks = [b for b in remaining_unscheduled if _slack_days(b) < min_lead_days]
            urgent_blocks = [b for b in remaining_unscheduled if _slack_days(b) == min_lead_days]
            normal_blocks = [b for b in remaining_unscheduled if _slack_days(b) > min_lead_days]
        else:
            # [AGENT-EDIT] 조립착수일 slack 기반 층 분리를 끄면 전체 후보를 normal 한 층으로 본다.
            emergency_blocks = []
            urgent_blocks = []
            normal_blocks = remaining_unscheduled
        if debug_enabled:
            if leadtime_layers_enabled:
                print(
                    f"[Masking] layer_counts: emergency={len(emergency_blocks)} "
                    f"urgent={len(urgent_blocks)} normal={len(normal_blocks)} (total={len(remaining_unscheduled)})"
                )
            else:
                print(f"[Masking] leadtime layers disabled: normal={len(normal_blocks)} (total={len(remaining_unscheduled)})")
    
        def _mark_inclusion(cands: List[EnhancedBlock], reason: str):
            for c in cands:
                a = analysis_map.get(c.block_id)
                if a:
                    a['is_available'] = True
                    a['inclusion_reason'] = reason
    
        def _ps_only_candidates(source_blocks: List[EnhancedBlock]) -> List[EnhancedBlock]:
            """P/S 순서만 유지한 후보 리스트"""
            result = []
            for blk in source_blocks:
                ok, _ = self._check_ps_order_constraint(blk, selected_blocks, blocks)
                if ok:
                    result.append(blk)
            return result
    
        ############################################################################################################
        # 1) Emergency 층: slack < min_lead_days
        ############################################################################################################
        def _process_layer(
            layer_blocks: List[EnhancedBlock],
            label: str,
            allow_ps_only: bool,
            *,
            check_leadtime: bool = True,
            allow_force: bool = True,
        ) -> Optional[List[int]]:
            if not layer_blocks:
                return None
            if debug_enabled:
                print(f"[Masking] {label}: layer_total={len(layer_blocks)}")
            # 리드타임 필터 (Emergency는 check_leadtime=False로 통과)
            lt_pass_blocks: List[EnhancedBlock] = []
            lt_fail_blocks: List[EnhancedBlock] = []
            for blk in layer_blocks:
                if not check_leadtime:
                    lt_pass_blocks.append(blk)
                    continue
                lt_ok, lt_reason = self._check_minimum_lead_time(blk, self.current_panel_date)
                if lt_ok:
                    lt_pass_blocks.append(blk)
                else:
                    lt_fail_blocks.append(blk)
                    analysis = analysis_map.get(blk.block_id)
                    if analysis:
                        analysis['is_available'] = False
                        analysis['exclusion_reason'] = lt_reason or "리드타임 미달"
                    violations.append(ConstraintViolation(
                        constraint_id="LEADTIME_FAIL",
                        message=lt_reason or "리드타임 미달",
                        severity="ERROR",
                        block_id=blk.block_id
                    ))
    
            # 리드타임 통과 블록이 없고 강제도 허용되는 경우 (Emergency에서 리드타임 미달만 있는 상황)
            if not lt_pass_blocks:
                if allow_force and lt_fail_blocks:
                    lt_pass_blocks = lt_fail_blocks.copy()
                else:
                    return None
            if debug_enabled:
                print(f"[Masking] {label}: lead_pass={len(lt_pass_blocks)} lead_fail={len(lt_fail_blocks)}")
    
            # 기본 제약 평가
            evaluated = self._evaluate_blocks_stage(
                lt_pass_blocks,
                label,
                selected_blocks,
                block_analysis,
                current_time,
                last_assembly_type,
                blocks,
                constraints_to_relax=None,
                violations=violations,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments,
                current_day_selected_blocks=current_day_selected_blocks
            )
            if evaluated:
                if debug_enabled:
                    print(f"[Masking] {label}: pass_after_base={len(evaluated)}")
                _mark_inclusion(evaluated, f"[{label}] 기본 통과")
                return [b.block_id for b in evaluated]
    
            # 완화 시퀀스 적용 (리드타임은 이미 유지/위반 상태 그대로)
            default_relax_defs = [
                ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
                ("P5#11,12 완화", ["P5#11", "P5#12"]),
                ("용량 완화", ["P5#8", "P5#9", "P5#10", "P5#16"]),
                ("P6#4 완화", ["P6#4"]),
                ("P5#15 완화", ["P5#15"]),
                ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
                ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
                ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
                # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거: 완화 단계에서도 제외
                ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            ]
            relax_list = getattr(self.constraint_config, "relax_order_assembly", []) or getattr(self.constraint_config, "relax_order", [])
            relax_defs = self._resolve_relax_stages(default_relax_defs, relax_list=relax_list)
            for stage_name, overrides in relax_defs:
                original_constraints = self._push_constraint_override([(stage_name, overrides)])
                try:
                    relaxed = self._evaluate_blocks_stage(
                        lt_pass_blocks,
                        f"{label}-{stage_name}",
                        selected_blocks,
                        block_analysis,
                        current_time,
                        last_assembly_type,
                        blocks,
                        constraints_to_relax=overrides,
                        violations=violations,
                        previous_machine_state=previous_machine_state,
                        current_bay_assignments=current_bay_assignments,
                        current_day_selected_blocks=current_day_selected_blocks
                    )
                    if relaxed:
                        if debug_enabled:
                            print(f"[Masking] {label}: pass_after_relax={len(relaxed)} stage={stage_name}")
                        _mark_inclusion(relaxed, f"[{label}] {stage_name}")
                        return [b.block_id for b in relaxed]
                finally:
                    self._pop_constraint_override(original_constraints)
    
            if allow_ps_only:
                ps_only = _ps_only_candidates(lt_pass_blocks)
                if ps_only:
                    if debug_enabled:
                        print(f"[Masking] {label}: pass_ps_only={len(ps_only)}")
                    _mark_inclusion(ps_only, f"[{label}] PS-only")
                    # [AGENT-EDIT] EMERGENCY_PS_ONLY는 INFO로 강등 (중복 경고 카운트 방지)
                    severity = "INFO" if label == "EMERGENCY" else "WARNING"
                    violations.append(ConstraintViolation(
                        constraint_id=f"{label}_PS_ONLY",
                        message=f"{label}: 다른 제약 완화 실패 → PS 순서만 유지하여 후보 복구",
                        severity=severity,
                        block_id=ps_only[0].block_id
                    ))
                    return [b.block_id for b in ps_only]
            # 모든 제약/완화/PS-only가 실패했지만 강제 허용 시: 해당 층의 (리드타임 통과 or 스킵) 블록 중 slack 최소 그룹을 강제 후보로 반환
            if allow_force and lt_pass_blocks:
                def _slack(blk: EnhancedBlock) -> int:
                    start_dt = getattr(blk, 'assembly_start_date', None)
                    if isinstance(start_dt, datetime):
                        return (start_dt.date() - self.current_panel_date).days
                    return 10_000
                min_slack = min(_slack(b) for b in lt_pass_blocks)
                forced = [b for b in lt_pass_blocks if _slack(b) == min_slack]
                if debug_enabled:
                    print(f"[Masking] {label}: pass_forced={len(forced)} min_slack={min_slack}")
                for blk in forced:
                    violations.append(ConstraintViolation(
                        constraint_id=f"{label}_FORCED",
                        message=f"{label}: 제약 통과 0 → slack 최소 그룹 강제 선택지 제공",
                        severity="WARNING",
                        block_id=blk.block_id
                    ))
                return [b.block_id for b in forced]
            return None
    
        # 층 게이트: Emergency가 있으면 Urgent/Normal은 평가하지 않음
        if emergency_blocks:
            # 긴급 블록만 평가(다른 층과 혼합 금지)
            emergency_ids = _process_layer(
                emergency_blocks, "EMERGENCY",
                allow_ps_only=True, check_leadtime=False, allow_force=True
            )
            if emergency_ids:
                # 착수일이 가장 이른 날짜 그룹만 남김
                def _start_date(b: EnhancedBlock):
                    dt = getattr(b, 'assembly_start_date', None)
                    return dt.date() if isinstance(dt, datetime) else date.max
                earliest = min(_start_date(self.blocks_dict[i]) for i in emergency_ids if i in self.blocks_dict)
                emergency_ids = [i for i in emergency_ids if _start_date(self.blocks_dict.get(i)) == earliest]
                filtered_analysis = [a for a in block_analysis if a["block_id"] in emergency_ids]
                return emergency_ids, violations, filtered_analysis
            # [AGENT-EDIT] Emergency 후보가 있어도 선택 불가면 정상 루트로 계속 진행
    
        ############################################################################################################
        # 2) Urgent 층: slack == min_lead_days
        ############################################################################################################
        # Emergency가 없고 Urgent가 있을 때만 처리, Normal은 건너뜀
        if urgent_blocks:
            # Urgent: 착수일+2 == 오늘 → 가능한 한 제약 준수, 실패 시 slack 최저 1개 강제
            def _start_key(b: EnhancedBlock):
                dt = getattr(b, 'assembly_start_date', None)
                return (dt if isinstance(dt, datetime) else datetime.max, b.block_id)
            urgent_ids = _process_layer(urgent_blocks, "URGENT", allow_ps_only=True, check_leadtime=True, allow_force=True)
            if urgent_ids:
                def _start_date(bid: int):
                    blk = self.blocks_dict.get(bid) if hasattr(self, 'blocks_dict') else None
                    dt = getattr(blk, 'assembly_start_date', None)
                    return dt.date() if isinstance(dt, datetime) else date.max
                earliest = min(_start_date(i) for i in urgent_ids)
                urgent_ids = [i for i in urgent_ids if _start_date(i) == earliest]
                filtered_analysis = [a for a in block_analysis if a["block_id"] in urgent_ids]
                return urgent_ids, violations, filtered_analysis
            # [AGENT-EDIT] Urgent 후보가 있어도 선택 불가면 정상 루트로 계속 진행
    
        ############################################################################################################
        # 이후 Normal 경로: 기존 작업장 헤드/창 로직을 normal_blocks로 제한
        ############################################################################################################
        # normal_blocks가 없으면 비상 PS-only로 이동
        if not normal_blocks:
            # 비상 PS-only: 리드타임 유지 + PS 순서만 유지, 통과 집합 반환(없으면 빈 리스트)
            ps_only_candidates = _ps_only_candidates(remaining_unscheduled)
            ps_ids = _process_layer(ps_only_candidates, "PS_ONLY_EMERGENCY", allow_ps_only=False, check_leadtime=True, allow_force=True)
            if ps_ids:
                _mark_inclusion([blk for blk in ps_only_candidates if blk.block_id in ps_ids], "PS-only 비상")
                violations.append(ConstraintViolation(
                    constraint_id="PS_ONLY_EMERGENCY",
                    message="리드타임/완화 후 후보 0 → PS 순서만 유지한 비상 후보 반환",
                    severity="WARNING",
                    block_id=ps_ids[0]
                ))
            return ps_ids or [], violations, block_analysis
    
    
        def _force_deadline_candidate(candidates: List[EnhancedBlock]) -> Optional[EnhancedBlock]:
            """Return the earliest block whose 조립착수일이 deadline buffer 내에 들어온 경우."""
            if not candidates:
                return None
            buffer_days = getattr(self.constraint_config, 'deadline_force_buffer_days', 0) if self.constraint_config else 0
            if buffer_days <= 0:
                return None
            limit_date = current_panel_date + timedelta(days=buffer_days)
            prioritized: Optional[EnhancedBlock] = None
            for block in candidates:
                start_dt = getattr(block, 'assembly_start_date', None)
                if isinstance(start_dt, datetime) and start_dt.date() <= limit_date:
                    # [AGENT-EDIT] 작업장 순서 제약은 하드 유지: deadline 강제 후보에서도 위반 블록 제외
                    workshop_ok, _ = self._check_workshop_order_constraint(block, blocks, selected_blocks)
                    if not workshop_ok:
                        continue
                    if prioritized is None or start_dt < getattr(prioritized, 'assembly_start_date', start_dt):
                        prioritized = block
            return prioritized
    
        def _force_deadline_when_empty() -> Optional[EnhancedBlock]:
            if not getattr(self.constraint_config, 'enable_deadline_forced_selection', True):
                return None
            # 리드타임이 이미 지난 블록들 중 가장 급한 것을 선택
            overdue: Optional[EnhancedBlock] = None
            for block in blocks:
                if block.block_id in selected_blocks:
                    continue
                start_dt = getattr(block, 'assembly_start_date', None)
                if not isinstance(start_dt, datetime):
                    continue
                if start_dt.date() <= current_panel_date:
                    # [AGENT-EDIT] 작업장 순서 제약은 하드 유지: deadline 강제 후보에서도 위반 블록 제외
                    workshop_ok, _ = self._check_workshop_order_constraint(block, blocks, selected_blocks)
                    if not workshop_ok:
                        continue
                    if overdue is None or start_dt < overdue.assembly_start_date:
                        overdue = block
            return overdue
    
    
        ################################################################################################################################################################################################
        # [AGENT-EDIT] Workshop window bootstrap (단순화 버전):
        #  - 각 작업장별(루트 기준) 최소 조립착수일 블록만 모은 뒤
        #  - 현재일+window 범위(초기 2일, 최대 5일) 안에서만 제약 평가
        #  - 계속 실패 시 “작업장 최소일 +1일”까지 완화하여 재평가
        ################################################################################################################################################################################################
        config = getattr(self, 'constraint_config', None)
        current_panel_date = panel_date if panel_date else current_time.date()
        workshop_initial_window = getattr(config, 'workshop_priority_initial_window_days', 2) or 2
        workshop_max_window_cfg = getattr(config, 'workshop_priority_max_window_days', 7) or 7
        max_expansion_days = max(workshop_initial_window, workshop_max_window_cfg)
    
        # 작업장 루트별 최소 착수일 블록 집계 (L_11,L_12→L_1, L_21,L_22→L_2, F_x 그대로)
        def _workshop_root(block: EnhancedBlock) -> str:
            code = getattr(block, 'assembly_workshop_code', '') or ''
            norm = self._normalize_workshop_code(code) or 'UNKNOWN'
            if norm.startswith('L_') and len(norm) > 2 and norm[2].isdigit():
                return f"L_{norm[2]}"
            return norm
    
        remaining_blocks = normal_blocks
    
        ######################################################################
        # [AGENT-ADD] 리드타임 임박 블록 우선 처리 (slack ≤ 2일)
        ######################################################################
        urgent_blocks: List[EnhancedBlock] = []
        urgent_slack_days = getattr(self.constraint_config, 'minimum_panel_lead_days', 1) or 1
        urgent_slack_days = max(urgent_slack_days, 2)  # 기본 2일 임계
        if leadtime_layers_enabled:
            for blk in remaining_blocks:
                start_dt = getattr(blk, 'assembly_start_date', None)
                if isinstance(start_dt, datetime):
                    slack = (start_dt.date() - current_panel_date).days
                    if slack <= urgent_slack_days:
                        urgent_blocks.append(blk)
    
        if leadtime_layers_enabled and urgent_blocks:
            # 긴급 후보에도 일반 제약 순서를 적용하여 통과한 블록만 후보로 올린다
            evaluated_urgent = self._evaluate_blocks_stage(
                urgent_blocks,
                "LEADTIME_URGENT",
                selected_blocks,
                block_analysis,
                current_time,
                last_assembly_type,
                blocks,
                constraints_to_relax=None,
                violations=None,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments
            )
    
            if evaluated_urgent:
                # 긴급 통과 블록 전체를 available_ids로 승격 → 아래 selection_method에서 선택
                urgent_ids = [b.block_id for b in evaluated_urgent]
                return urgent_ids, violations, block_analysis
            ################################################################################################
            # [AGENT-ADD] 리드타임 임박 블록: 제약 완화 → 최후 강제 선택 순서로 반드시 선택
            ################################################################################################
            default_urgent_relax_stages = [
                ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
                ("P5#11,12 완화", ["P5#11", "P5#12"]),
                ("용량 완화", ["P5#8", "P5#9", "P5#10", "P5#16"]),
                ("P6#4 완화", ["P6#4"]),
                ("P5#15 완화", ["P5#15"]),
                ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
                ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
                ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
                # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거: 완화 단계에서도 제외
                ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            ]
            relax_list = getattr(self.constraint_config, "relax_order_assembly", []) or getattr(self.constraint_config, "relax_order", [])
            urgent_relax_stages = self._resolve_relax_stages(
                default_urgent_relax_stages,
                relax_list=relax_list,
                label_prefix="URGENT-",
            )

            for stage_name, relax_keys in urgent_relax_stages:
                original_constraints = self._push_constraint_override([(stage_name, relax_keys)])
                try:
                    relaxed = self._evaluate_blocks_stage(
                        urgent_blocks,
                        stage_name,
                        selected_blocks,
                        block_analysis,
                        current_time,
                        last_assembly_type,
                        blocks,
                        constraints_to_relax=relax_keys,
                        violations=violations,
                        previous_machine_state=previous_machine_state,
                        current_bay_assignments=current_bay_assignments
                    )
                    if relaxed:
                        for blk in relaxed:
                            violations.append(
                                ConstraintViolation(
                                    constraint_id="URGENT_RELAX",
                                    message=f"{stage_name} 적용으로 리드타임 임박 블록 선택",
                                    block_id=blk.block_id,
                                    severity="WARNING"
                                )
                            )
                        return [b.block_id for b in relaxed], violations, block_analysis
                finally:
                    self._pop_constraint_override(original_constraints)
    
            # 최후: 리드타임 임박 블록 중 착수일이 가장 빠른 1개를 강제 선택
            forced_urgent = min(
                urgent_blocks,
                key=lambda b: getattr(b, "assembly_start_date", datetime.max)
            )
            violations.append(
                ConstraintViolation(
                    constraint_id="URGENT_FORCE",
                    message="리드타임 임박으로 제약을 일부 무시하고 강제 선택",
                    block_id=forced_urgent.block_id,
                    severity="ERROR"
                )
            )
            analysis = next((a for a in block_analysis if a["block_id"] == forced_urgent.block_id), None)
            if analysis:
                analysis["is_available"] = True
                analysis["inclusion_reason"] = "[URGENT_FORCE] 리드타임 임박 강제"
            return [forced_urgent.block_id], violations, block_analysis
        min_by_root: Dict[str, Tuple[date, List[EnhancedBlock]]] = {}
        for blk in remaining_blocks:
            root = _workshop_root(blk)
            start_dt = getattr(blk, 'assembly_start_date', None)
            if not isinstance(start_dt, datetime):
                continue
            start_date = start_dt.date()
            if root not in min_by_root or start_date < min_by_root[root][0]:
                min_by_root[root] = (start_date, [blk])
            elif start_date == min_by_root[root][0]:
                min_by_root[root][1].append(blk)
    
        if not workshop_head_masking:
            # [AGENT-ADD] workshop-head masking 비활성화 시 날짜창 안의 전체 후보를 평가한다.
            workshop_heads = [
                blk for blk in remaining_blocks
                if isinstance(getattr(blk, 'assembly_start_date', None), datetime)
            ]
        else:
            workshop_heads: List[EnhancedBlock] = []
            for _, (_, blks) in min_by_root.items():
                workshop_heads.extend(blks)
        if debug_enabled:
            print(f"[Masking] normal_workshop_heads={len(workshop_heads)}")
            if not workshop_head_masking:
                print("[Masking] workshop-head masking disabled: using all dated remaining blocks")
    
        # 창별 후보 필터 함수
        window_filter_enabled = bool(getattr(self.constraint_config, 'enable_assembly_start_window_filter', True))
        if debug_enabled and not window_filter_enabled:
            print('[Masking] assembly_start window filter disabled: evaluating full candidate pool')
        def _filter_by_window(cands: List[EnhancedBlock], window_days: int) -> List[EnhancedBlock]:
            if not window_filter_enabled:
                return list(cands)
            upper = current_panel_date + timedelta(days=max(0, window_days))
            return [
                blk for blk in cands
                if isinstance(getattr(blk, 'assembly_start_date', None), datetime)
                and current_panel_date <= blk.assembly_start_date.date() <= upper
            ]
    
        window_cache: Dict[int, List[EnhancedBlock]] = {}
        selected_set = set(selected_blocks)
    
        ############################################################################################################################################################################
        # [AGENT-EDIT] Stage 1: 작업장 헤드 + 날짜창 필터 → 기본 제약
        ############################################################################################################################################################################
        def _attempt_leadtime_guard(
            stage_blocks: List[EnhancedBlock],
            stage_label: str,
            lead_override: Optional[int] = None
        ) -> Optional[List[int]]:
            if not stage_blocks:
                return None
            # [AGENT-EDIT] 리드타임 강제 선택 시 P6 15:00 충족 후보를 우선 선택
            guard_block = self._pick_leadtime_guard_block(
                stage_blocks,
                current_panel_date,
                lead_override,
                prefer_time_feasible=getattr(self.constraint_config, 'leadtime_guard_respect_p6', True),
                current_time=current_time,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments
            )
            if guard_block is None:
                return None
            # 제약을 한 번 평가해 어떤 제약을 어겼는지 기록
            self._evaluate_blocks_stage(
                [guard_block],
                stage_label,
                selected_blocks,
                block_analysis,
                current_time,
                last_assembly_type,
                blocks,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments
            )
            analysis = analysis_map.get(guard_block.block_id)
            if analysis:
                reason = analysis.get('exclusion_reason') or '리드타임 임박 강제 선택'
                analysis['inclusion_reason'] = f"[LEADTIME-GUARD] {reason}"
                analysis['is_available'] = True
                # 강제 선택 시 해당 블록이 어긴 제약을 별도로 위반 로그에 남김
                fail_reason = analysis.get('exclusion_reason')
                if fail_reason:
                    violations.append(ConstraintViolation(
                        constraint_id="LEADTIME_GUARD_CONSTRAINT",
                        message=f"리드타임 강제 선택 중 추가 제약 위반: {fail_reason}",
                        block_id=guard_block.block_id,
                        severity='WARNING'
                    ))
                # [AGENT-ADD] 강제 선택 자체를 명시적으로 로깅 (INFO)
                violations.append(ConstraintViolation(
                    constraint_id="LEADTIME_GUARD_DETAIL",
                    message=f"{stage_label}: 리드타임 가드 강제 선택 적용 (사유: {reason})",
                    block_id=guard_block.block_id,
                    severity='INFO'
                ))
            lead_days = lead_override
            if lead_days is None:
                lead_days = self._get_minimum_lead_days()
            violations.append(ConstraintViolation(
                constraint_id="LEADTIME_GUARD",
                message=f"{stage_label}: 리드타임 {lead_days}일 임박 강제 선택",
                block_id=guard_block.block_id,
                severity='ERROR'
            ))
            return [guard_block.block_id]
    
        def _resolve_lead_override(window_value: int, *, relax: bool = False) -> Optional[int]:
            base_lead = self._get_minimum_lead_days()
            floor = self._get_relax_lead_floor() if relax else self._get_pre_relax_lead_floor()
            if base_lead <= 0 and floor <= 0:
                return None
            # 창 확장과 무관하게 리드타임 요구치를 유지(기본 2일 유지)
            candidate = base_lead
            if relax:
                return max(floor, candidate)
            return max(floor, candidate, 0)
    
        def _evaluate_window(window_days: int, stage_label: str) -> List[EnhancedBlock]:
            def _run_group(cands: List[EnhancedBlock], base_label: str) -> List[EnhancedBlock]:
                # 3베이 필터
                valid = []
                for block in cands:
                    ok, reason = self._check_consecutive_3bay_prevention(block, selected_blocks)
                    if ok:
                        valid.append(block)
                    else:
                        analysis = analysis_map.get(block.block_id)
                        if analysis:
                            analysis['is_available'] = False
                            analysis['exclusion_reason'] = reason
                            analysis['three_bay_check'] = 'FAIL'
    
                # 기본 제약 평가
                evaluated = self._evaluate_blocks_stage(
                    valid,
                    base_label,
                    selected_blocks,
                    block_analysis,
                    current_time,
                    last_assembly_type,
                    blocks,
                    constraints_to_relax=None,
                    violations=violations,
                    previous_machine_state=previous_machine_state,
                    current_bay_assignments=current_bay_assignments
                )
                if evaluated:
                    if debug_enabled:
                        print(f"[Masking] NORMAL: base_pass={len(evaluated)} stage={base_label}")
                    return evaluated
    
                # [AGENT-EDIT] 단계적 완화에서도 config.yaml의 relax_order_assembly를 우선 적용
                default_relax_defs = [
                    ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
                    ("P5#11,12 완화", ["P5#11", "P5#12"]),
                    ("P5#8,9,10,16 완화", ["P5#8", "P5#9", "P5#10", "P5#16"]),
                    ("P6#4 완화", ["P6#4"]),
                    ("P5#15 완화", ["P5#15"]),
                    ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
                    ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
                    ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
                    # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거: 완화 단계에서도 제외
                    ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
                ]
                relax_list = getattr(self.constraint_config, "relax_order_assembly", []) or getattr(self.constraint_config, "relax_order", [])
                stages = self._resolve_relax_stages(default_relax_defs, relax_list=relax_list)
                for stage_name, override_keys in stages:
                    # 각 단계별로 해당 제약만 임시로 비활성화
                    original_constraints = self._push_constraint_override([(stage_name, override_keys)])
                    try:
                        evaluated = self._evaluate_blocks_stage(
                            valid or cands,
                            f"{base_label}-{stage_name}",
                            selected_blocks,
                            block_analysis,
                            current_time,
                            last_assembly_type,
                            blocks,
                            constraints_to_relax=override_keys,
                            violations=violations,
                            previous_machine_state=previous_machine_state,
                            current_bay_assignments=current_bay_assignments
                        )
                        if evaluated:
                            if debug_enabled:
                                print(f"[Masking] NORMAL: relax_pass={len(evaluated)} stage={base_label}-{stage_name}")
                            return evaluated
                    finally:
                        self._pop_constraint_override(original_constraints)
    
                return []
    
            # 창을 2→3→4→5일까지 확장하며 그룹별(비P6→P6강제→P6비강제)로 순차 평가/완화
            for wd in range(workshop_initial_window, max_expansion_days + 1):
                window_candidates = window_cache.get(wd)
                if window_candidates is None:
                    window_candidates = _filter_by_window(workshop_heads, wd)
                    window_cache[wd] = window_candidates
    
                self._debug_candidate_stage(debug_enabled, f"{stage_label}-{wd}일창 후보", window_candidates, stage_history)
                if debug_enabled:
                    print(f"[Masking] NORMAL: window={wd} candidates={len(window_candidates)}")
    
                # [AGENT-EDIT] P6#1,#2,#3 제거 이후 작업장 창 평가는 단일 후보군만 사용한다.
                groups = [
                    ("WORKSHOP_WINDOW", window_candidates),
                ]

                for g_label, g_list in groups:
                    if not g_list:
                        continue
                    picked = _run_group(g_list, f"{stage_label}-{wd}d-{g_label}")
                    if picked:
                        return picked
    
            return []
    
        # 초기 창(기본 2일)로 평가
        final_candidates = _evaluate_window(workshop_initial_window, "작업장헤드")
    
        # 창 확장: +3,4,5 ... (최대 max_expansion_days)
        window_value = workshop_initial_window + 1
        while not final_candidates and window_value <= max_expansion_days:
            stage_label = f"범위확장+{window_value - workshop_initial_window}일"
            final_candidates = _evaluate_window(window_value, stage_label)
    
            if not final_candidates:
                # 리드타임 가드 시도
                guard_selection = _attempt_leadtime_guard(
                    window_cache.get(window_value, []),
                    f"{stage_label}-리드타임",
                    _resolve_lead_override(window_value)
                )
                if guard_selection:
                    return guard_selection, violations, block_analysis
            window_value += 1
    
        # [AGENT-EDIT] window 기준을 현재 날짜로 통일 (작업장 최소일 기반 fallback 제거)
    
        # [AGENT-EDIT] 이후 단계 참조용 기본 집합 초기화 (작업장 필터 결과 없을 때 보호)
        workshop_allowed_ids: Set[int] = {blk.block_id for blk in blocks if blk.block_id not in selected_blocks}
        workshop_block_reasons: Dict[int, str] = {}
    
        # [AGENT-EDIT] P 이후 S 즉시 추종 강제 제거.
        # 이 경로에서는 특정 S 블록을 강제 반환하지 않고, 아래 완화/후보 평가만 사용한다.
    
    #################################################################################################################################################
    ###############                                            완화 모드                                                               ###############  
    #################################################################################################################################################
    
        base_window_value = workshop_initial_window
        step_increment = getattr(self.constraint_config, 'assembly_expansion_days', 1) if self.constraint_config else 1
        step_increment = step_increment or 1
    
        has_base_candidates = bool(final_candidates)
        base_candidates = final_candidates.copy()
        base_candidate_ids = {blk.block_id for blk in base_candidates}
        if base_candidates:
            for analysis in block_analysis:
                if analysis['block_id'] in base_candidate_ids:
                    analysis['relax_level'] = 0
    
        final_candidates = base_candidates.copy()
    
        run_relaxation = include_relax_candidates or not base_candidates
        if run_relaxation:
            # print(f"   🚨 모든 조립착수일 확장 실패 → 제약조건 완화 모드 시작")
    
            default_stage_definitions = [
                ("라인 그룹 완화", ["LINE_GROUP_CONSTRAINT"]),
                ("P5#11,12 완화", ["P5#11", "P5#12"]),
                ("P5#8,9,10,16 완화", ["P5#8", "P5#9", "P5#10", "P5#16"]),
                ("P6#4 완화", ["P6#4"]),
                ("P5#15 완화", ["P5#15"]),
                ("C/Seam 완화", ["ROUTING_C_SEAM_SPACING"]),
                ("곡판 간격 완화", ["ROUTING_CURVED_SPACING"]),
                ("고심수 간격 완화", ["ROUTING_HIGH_SEAM_SPACING"]),
                # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거: 완화 단계에서도 제외
                ("3베이 패턴 완화", ["CONSECUTIVE_3BAY"]),
            ]
            relax_list = getattr(self.constraint_config, "relax_order_assembly", []) or getattr(self.constraint_config, "relax_order", [])
            stage_definitions = self._resolve_relax_stages(default_stage_definitions, relax_list=relax_list)
    
            relaxation_sequences: List[Tuple[int, str, List[str]]] = []
            stage_count = len(stage_definitions)
            sequence_counter = 1
    
            for name, constraints_to_relax in stage_definitions:
                relaxation_sequences.append((sequence_counter, name, list(dict.fromkeys(constraints_to_relax))))
                sequence_counter += 1
    
            if stage_count > 1:
                for size in range(2, stage_count + 1):
                    for idx_tuple in combinations(range(stage_count), size):
                        combo_names = [stage_definitions[i][0] for i in idx_tuple]
                        combo_constraints: List[str] = []
                        for idx_stage in idx_tuple:
                            combo_constraints.extend(stage_definitions[idx_stage][1])
                        combo_constraints = list(dict.fromkeys(combo_constraints))
                        relaxation_sequences.append((sequence_counter, " + ".join(combo_names), combo_constraints))
                        sequence_counter += 1
    
            last_relaxation_candidates: List[EnhancedBlock] = []
            relax_start = min(base_window_value + step_increment, max_expansion_days)
            relax_start = max(relax_start, base_window_value + 1)
    
            for buffer_days in range(relax_start, max_expansion_days + 1, step_increment):
                relaxation_cutoff = current_panel_date + timedelta(days=buffer_days)
                relaxation_candidates = []
                candidate_relax_reason: Dict[int, Optional[str]] = {}
    
                lead_override = max(self._get_relax_lead_floor(), self._get_minimum_lead_days() - buffer_days)
                for block in blocks:
                    if block.block_id in selected_blocks:
                        continue
                    if block.block_id not in workshop_allowed_ids:
                        continue
                    lead_pass, lead_reason = self._check_minimum_lead_time(
                        block,
                        current_panel_date,
                        override_required_days=lead_override
                    )
                    if not lead_pass:
                        candidate_relax_reason[block.block_id] = lead_reason
                        analysis = analysis_map.get(block.block_id)
                        if analysis and lead_reason:
                            analysis['is_available'] = False
                            analysis['exclusion_reason'] = lead_reason
                        if self._is_trace_block(block.block_id):
                            print(f"[TRACE][Relax buffer {buffer_days}] block {block.block_id} rejected (lead): {lead_reason}")
                        continue
                    if block.assembly_start_date.date() > relaxation_cutoff:
                        continue
    
                    # P/S 순서는 절대 우회 불가
                    is_valid_ps_order, ps_reason = self._check_ps_order_constraint(block, selected_blocks, blocks)
                    if not is_valid_ps_order:
                        continue
    
                    is_valid_3bay, reason_3bay = self._check_consecutive_3bay_prevention(block, selected_blocks)
                    analysis = analysis_map.get(block.block_id)
                    if not is_valid_3bay:
                        if analysis:
                            analysis['is_available'] = False
                            analysis['exclusion_reason'] = reason_3bay
                            analysis['three_bay_check'] = 'FAIL'
                    relaxation_candidates.append(block)
    
                label = f"완화 단계(+{buffer_days}일) 후보"
                self._debug_candidate_stage(debug_enabled, label, relaxation_candidates, stage_history)
                last_relaxation_candidates = relaxation_candidates[:]
    
                if not relaxation_candidates:
                    continue
    
                relax_guard_override = _resolve_lead_override(buffer_days, relax=True)
                guard_selection = _attempt_leadtime_guard(
                    relaxation_candidates,
                    f"완화리드타임+{buffer_days}",
                    relax_guard_override
                )
                if guard_selection:
                    return guard_selection, violations, block_analysis
    
                # print(f"   🔄 완화 범위({relaxation_cutoff}) 블록 복귀: {len(relaxation_candidates)}개 후보")
    
                combined_relax_constraints: List[str] = []
                attempt_success = False
    
                for seq_id, stage_name, combined_relax_constraints in relaxation_sequences:
    
    ######################################################################################################################################################################################
    #####                                  완화하는 제약조건 목록 확인 가능(예: P5#11,12 완화 시작 (해제 예정: ['P5#11', 'P5#12']))                                                      #####
    ######################################################################################################################################################################################
    
                    # print(f"   [RELAX] {stage_name} 시작 (해제 예정: {combined_relax_constraints})")
    
                    relaxed_candidates: List[EnhancedBlock] = []
                    for block in relaxation_candidates:
                        passes_all = True
                        failed_constraints = []
                        analysis = analysis_map.get(block.block_id)
    
                        ################################################################################################################################################################################################
                        # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                        ################################################################################################################################################################################################
                        if "ROUTING_CURVED_SPACING" in combined_relax_constraints:
                            curved_pass, curved_reason = True, "곡판 간격 완화"
                        else:
                            curved_pass, curved_reason = self._check_curved_plate_spacing(block, selected_blocks)
    
                        if "ROUTING_HIGH_SEAM_SPACING" in combined_relax_constraints:
                            high_seam_pass, high_seam_reason = True, "고심수 간격 완화"
                        else:
                            high_seam_pass, high_seam_reason = self._check_high_seam_spacing(block, selected_blocks)
    
                        if not curved_pass:
                            passes_all = False
                            failed_constraints.append("ROUTING_CURVED_SPACING")
    
                        if not high_seam_pass:
                            passes_all = False
                            failed_constraints.append("ROUTING_HIGH_SEAM_SPACING")
    
                        # 🏆 P5#15: 명절 전날 야간 차단 (완화 가능)
                        if ("P5#15" not in combined_relax_constraints and 
                            self.constraint_config.is_constraint_enabled("P5#15")):
                            holiday_pass, holiday_reason = self._check_holiday_eve_constraint(block, current_time)
                            if not holiday_pass:
                                passes_all = False
                                failed_constraints.append("P5#15")
    
                        # 🥇 P5#8,9,10,16: 통합 용량 제약 (완화 가능)
                        if (not any(c in combined_relax_constraints for c in ["P5#8", "P5#9", "P5#10", "P5#16"]) and 
                            self.constraint_config.is_constraint_enabled("P5#8")):
                            capacity_pass, capacity_reason = self._check_integrated_capacity_constraints(block, current_time)
                            if not capacity_pass:
                                passes_all = False
                                failed_constraints.append("P5#8,9,10,16")
    
                        # [AGENT-EDIT] P6#1,#2,#3 시간 제약 제거: 이 단계에서는 더 이상 검사하지 않는다.
    
                        ######################################################################################################################################################################################
                        # fix: 연속 3베이 방지 유지 (완화 모드에서도 적용)
                        ######################################################################################################################################################################################
                        if "CONSECUTIVE_3BAY" in combined_relax_constraints:
                            is_valid_3bay, reason_3bay = True, "연속 3베이 제약 완화"
                        else:
                            is_valid_3bay, reason_3bay = self._check_consecutive_3bay_prevention(block, selected_blocks)
    
                        if not is_valid_3bay:
                            passes_all = False
                            failed_constraints.append("CONSECUTIVE_3BAY")
                            analysis = analysis_map.get(block.block_id)
                            if analysis:
                                analysis['is_available'] = False
                                analysis['exclusion_reason'] = reason_3bay
                                analysis['three_bay_check'] = 'FAIL'
                        elif "CONSECUTIVE_3BAY" in combined_relax_constraints:
                            analysis = analysis_map.get(block.block_id)
                            if analysis:
                                analysis['three_bay_check'] = 'RELAX'
                                # 완화 적용 시 기존 제외 사유 초기화
                                if analysis.get('exclusion_reason') == reason_3bay:
                                    analysis['exclusion_reason'] = ''
    
                        ######################################################################################################################################################################################
                        #####                                                                 fix: assembly 완화 단계에서도 C/Seam 연속 배치를 차단하도록 동일 제약 유지                                                            #####
                        ######################################################################################################################################################################################
                        if "ROUTING_C_SEAM_SPACING" in combined_relax_constraints:
                            c_seam_pass, c_seam_reason = True, "C/Seam 간격 완화"
                            if analysis is not None:
                                constraint_log = analysis.setdefault('constraint_checks', {})
                                constraint_log['ROUTING_C_SEAM_SPACING'] = {
                                    'result': 'RELAX',
                                    'reason': c_seam_reason
                                }
                        else:
                            c_seam_pass, c_seam_reason = self._check_c_seam_spacing(block, selected_blocks)
                            if not c_seam_pass:
                                passes_all = False
                                failed_constraints.append("ROUTING_C_SEAM_SPACING")
    
                        # [AGENT-EDIT] 작업장 순서 제약은 하드 유지: 완화 여부와 무관하게 항상 체크
                        workshop_pass, workshop_reason = self._check_workshop_order_constraint(block, blocks, selected_blocks)
                        if not workshop_pass:
                            passes_all = False
                            failed_constraints.append("ROUTING_WORKSHOP_ORDER")
    
                        # P5#11,12: Assembly Type 제약 (완화 가능)
                        assembly_type_pass = True
                        assembly_type_reason = None
                        if last_assembly_type:
                            assembly_type_pass, assembly_type_reason = self._check_assembly_type_constraint(
                                block, last_assembly_type, selected_blocks, blocks, combined_relax_constraints
                            )
    
                        if (not any(c in combined_relax_constraints for c in ["P5#11", "P5#12"]) and 
                            last_assembly_type and not assembly_type_pass):
                            passes_all = False
                            failed_constraints.append("P5#11,12")
    
                        # # P6#4: Cross seam 제약 (완화 가능)
                        # if ("P6#4" not in constraints_to_relax and 
                        #     not self._check_cross_seam_constraint(block, selected_blocks, blocks)[0]):
                        #     passes_all = False
                        #     failed_constraints.append("P6#4")
    
                        # P5#3,4: P/S 순서 제약 (절대 완화 불가)
                        if not self._check_ps_order_constraint(block, selected_blocks, blocks)[0]:
                            passes_all = False
                            failed_constraints.append("P5#3,4")
    
                        if passes_all:
                            relaxed_candidates.append(block)
                        else:
                            if debug_enabled:
                                # print(f"     ↳ 블록 {block.block_id} 실패: {', '.join(failed_constraints)}")
                                pass
    
    ######################################################################################################################################################################################
    #####                                             완화 하고난 후 결과 확인 가능(예: [RELAX] C/Seam 완화 결과: 5개 → 2개)                                                            #####
    ######################################################################################################################################################################################
    
                    # print(f"   [RELAX] {stage_name} 결과: {len(relaxation_candidates)}개 → {len(relaxed_candidates)}개")
    
                    if relaxed_candidates:
                        final_candidates = relaxed_candidates
                        attempt_success = True
                        for analysis in block_analysis:
                            for candidate in final_candidates:
                                    if analysis['block_id'] == candidate.block_id:
                                        analysis['is_available'] = True
                                        reason_text = candidate_relax_reason.get(candidate.block_id)
                                        if reason_text and reason_text.startswith("[RELAX]"):
                                            analysis['inclusion_reason'] = reason_text
                                            relax_message = reason_text
                                        else:
                                            relax_constraints_text = ", ".join(combined_relax_constraints) if combined_relax_constraints else "해제 없음"
                                            relax_message = f"[RELAX] {stage_name}: {relax_constraints_text}"
                                            analysis['inclusion_reason'] = relax_message
                                        if candidate.block_id not in base_candidate_ids:
                                            analysis['relax_level'] = seq_id
                                        violations.append(
                                            ConstraintViolation(
                                                constraint_id="RELAX_STAGE",
                                                message=relax_message,
                                            block_id=candidate.block_id,
                                            severity="WARNING"
                                        )
                                    )
                        break
    
                if attempt_success:
                    break
    
            # for buffer loop end
    
            # block_analysis와 final_candidates 동기화 (실제 후보만 유지)
            final_ids_set = {blk.block_id for blk in final_candidates}
            for info in block_analysis:
                blk_id = info.get('block_id')
                if blk_id in final_ids_set:
                    info['is_available'] = True
                    info.pop('exclusion_reason', None)
                else:
                    info['is_available'] = False
    
            if not final_candidates:
                try:
                    current_date_str = current_time.strftime('%Y-%m-%d %H:%M') if isinstance(current_time, datetime) else str(current_time)
                except Exception:
                    current_date_str = str(current_time)
                recent_blocks = selected_blocks[-3:] if len(selected_blocks) >= 3 else selected_blocks
                # [AGENT-EDIT] 디버그 플래그가 켜진 경우에만 상세 출력
                if debug_enabled:
                    print("⚠️ 완화 실패: 선택 가능한 블록 0개")
                    print(f"   현재 시간: {current_date_str}")
                    print(f"   최근 선택 블록: {recent_blocks}")
                    if last_relaxation_candidates:
                        summary = []
                        for blk in last_relaxation_candidates[:10]:
                            summary.append({
                                'block_id': blk.block_id,
                                'workshop': getattr(blk, 'assembly_workshop_code', None),
                                'start': getattr(blk, 'assembly_start_date', None),
                                'seam': getattr(blk, 'seam_count', None),
                                'pair': blk.pair_block_id
                            })
                        print(f"   마지막 후보(최대 10개): {summary}")
                if not has_base_candidates:
                    self._print_candidate_diagnostics(selected_blocks, block_analysis, blocks, debug_enabled)
                    forced_block = _force_deadline_when_empty()
                    if forced_block is not None:
                        analysis = analysis_map.get(forced_block.block_id)
                        if analysis:
                            analysis['is_available'] = True
                            analysis['inclusion_reason'] = '[DEADLINE-FORCED]'
                        violations.append(ConstraintViolation(
                            constraint_id="DEADLINE_FORCE",
                            message=f"리드타임 초과로 강제 선택 (Block {forced_block.block_id})",
                            block_id=forced_block.block_id,
                            severity='ERROR'
                        ))
                        return [forced_block.block_id], violations, block_analysis
                    else:
                        if debug_enabled:
                            print("   ⚠️ deadline 강제 후보 없음 → 최후 선택")
    
            # [AGENT-EDIT] fallback tie-break helper를 공통 위치로 올려
            # MIN_VIOLATION_CHOICE / FINAL_FORCE 양쪽에서 동일하게 사용한다.
            def _start_key_fallback(b: EnhancedBlock):
                dt = getattr(b, 'assembly_start_date', None)
                return (dt if isinstance(dt, datetime) else datetime.max, b.block_id)

            # 제약조건 완화로도 해결되지 않으면 최후 선택(최소 위반)
            if not final_candidates:
                if debug_enabled:
                    print("🚫 후보 없음 → 최소 위반 선택 단계")
                if debug_verbose and stage_history:
                    print("   단계별 후보 수 요약:")
                    for label, count in stage_history:
                        print(f"    - {label}: {count}")
                if debug_verbose and not has_base_candidates:
                    self._print_candidate_diagnostics(selected_blocks, block_analysis, blocks, debug_enabled)
    
                remaining_blocks = [
                    blk for blk in blocks
                    if blk.block_id not in selected_blocks and blk.block_id in workshop_head_ids
                ]

                if remaining_blocks:
                    # ==== [AGENT-EDIT BEGIN: strict fallback filtering] ====
                    strict_rules = self._resolve_strict_rules()
                    best_block: Optional[EnhancedBlock] = None
                    best_score = (float('inf'), datetime.max, float('inf'))
                    best_failed: List[str] = []

                    for blk in remaining_blocks:
                        violation_count, failed = self._score_block_violations(
                            blk,
                            selected_blocks,
                            blocks,
                            current_time,
                            last_assembly_type,
                            previous_machine_state=previous_machine_state,
                            current_bay_assignments=current_bay_assignments,
                            current_day_selected_blocks=current_day_selected_blocks
                        )
                        if self._has_strict_constraint_failure(failed, strict_rules):
                            continue
                        tie_key = (violation_count, _start_key_fallback(blk)[0], blk.block_id)
                        if tie_key < best_score:
                            best_score = tie_key
                            best_block = blk
                            best_failed = failed

                    if best_block is not None:
                        fallback_block = best_block
                        final_candidates = [fallback_block]
                        final_block_ids = [fallback_block.block_id]

                        analysis = analysis_map.get(fallback_block.block_id)
                        if analysis:
                            analysis['is_available'] = True
                            failed_text = ", ".join(best_failed) if best_failed else "위반 없음"
                            analysis['inclusion_reason'] = f"[MIN_VIOLATION] 최후 선택: {failed_text}"
                            analysis['relax_level'] = analysis.get('relax_level') or len(relaxation_sequences) + 1
                            analysis['final_choice_flag'] = True
                            analysis['final_violation_list'] = list(best_failed)
                            analysis['final_violation_count'] = len(best_failed)

                        violations.append(
                            ConstraintViolation(
                                constraint_id="MIN_VIOLATION_CHOICE",
                                message="최후 선택: 제약 위반 수 최소 블록 선택",
                                block_id=fallback_block.block_id,
                                severity="INFO",
                            )
                        )
                    else:
                        final_candidates = []
                        final_block_ids = []
                        if debug_enabled:
                            print("      ⚠️ strict hard 제약으로 MIN_VIOLATION 후보가 없음")
                    # ==== [AGENT-EDIT END] ====
                else:
                    if debug_enabled:
                        print("   ❌ 남은 블록이 없음")
    
        deadline_forced_final = _force_deadline_candidate(final_candidates)
        if deadline_forced_final is not None:
            final_candidates = [deadline_forced_final]
            analysis = analysis_map.get(deadline_forced_final.block_id)
            if analysis:
                analysis['inclusion_reason'] = analysis.get('inclusion_reason') or 'deadline_forced'
            if self._is_trace_block(deadline_forced_final.block_id):
                print(f"[TRACE] block {deadline_forced_final.block_id} deadline-forced at final stage")
    
        self._debug_candidate_stage(debug_enabled, "최종 선택 가능 블록", final_candidates, stage_history)
        if debug_enabled:
            if final_candidates:
                final_summary = [
                    {
                        'block_id': blk.block_id,
                        'workshop': getattr(blk, 'assembly_workshop_code', None),
                        'start': getattr(blk, 'assembly_start_date', None),
                        'seam': getattr(blk, 'seam_count', None),
                        'pair': blk.pair_block_id
                    }
                    for blk in final_candidates
                ]
    ######################################################################################################################################################################################
    #####                                                                          fix                                                                                               #####
    ######################################################################################################################################################################################
                # print(f"✅ 최종 선택 가능 블록 상세: {final_summary}")
            else:
                print("✅ 최종 선택 가능 블록 상세: 없음")
    
            relaxed_candidates = final_candidates.copy()
        else:
            relaxed_candidates = []
    
        if base_candidates and include_relax_candidates:
            extra_relaxed = [blk for blk in relaxed_candidates if blk.block_id not in base_candidate_ids]
            final_candidates = base_candidates + extra_relaxed
        elif not base_candidates:
            final_candidates = relaxed_candidates
    
        # block_analysis와 final_candidates 동기화
        final_ids_set = {blk.block_id for blk in final_candidates}
        for info in block_analysis:
            blk_id = info.get('block_id')
            if blk_id in final_ids_set:
                info['is_available'] = True
                info.pop('exclusion_reason', None)
            else:
                info['is_available'] = False
    
        # 최종 결과 반환
        final_block_ids = [block.block_id for block in final_candidates]
    
        # [AGENT-ADD] 후보 0개 방지: 남은 블록이 있으면 최소 위반 강제 선택
        if not final_block_ids:
            remaining_unscheduled = [blk for blk in blocks if blk.block_id not in selected_blocks]
            if remaining_unscheduled:
                # ==== [AGENT-EDIT BEGIN: final force respects strict rules] ====
                strict_rules = self._resolve_strict_rules()
                best_block: Optional[EnhancedBlock] = None
                best_score = (float('inf'), datetime.max, float('inf'))
                best_failed: List[str] = []

                for blk in remaining_unscheduled:
                    violation_count, failed = self._score_block_violations(
                        blk,
                        selected_blocks,
                        blocks,
                        current_time,
                        last_assembly_type,
                        previous_machine_state=previous_machine_state,
                        current_bay_assignments=current_bay_assignments,
                        current_day_selected_blocks=current_day_selected_blocks
                    )
                    if self._has_strict_constraint_failure(failed, strict_rules):
                        continue
                    tie_key = (violation_count, _start_key_fallback(blk)[0], blk.block_id)
                    if tie_key < best_score:
                        best_score = tie_key
                        best_block = blk
                        best_failed = failed

                if best_block is not None:
                    fallback_block = best_block
                    final_candidates = [fallback_block]
                    final_block_ids = [fallback_block.block_id]

                    analysis = analysis_map.get(fallback_block.block_id)
                    if analysis:
                        analysis['is_available'] = True
                        failed_text = ", ".join(best_failed) if best_failed else "위반 없음"
                        analysis['inclusion_reason'] = f"[FINAL_FORCE] 후보 0 → 최소 위반 강제 선택 ({failed_text})"
                        analysis['final_choice_flag'] = True
                        analysis['final_violation_list'] = list(best_failed)
                        analysis['final_violation_count'] = len(best_failed)

                    violations.append(ConstraintViolation(
                        constraint_id="FINAL_FORCE",
                        message="후보 0개 → 최소 위반 강제 선택으로 공백 방지",
                        block_id=fallback_block.block_id,
                        severity="WARNING"
                    ))
                else:
                    final_candidates = []
                    final_block_ids = []
                    if debug_enabled:
                        print("      ⚠️ strict hard 제약으로 FINAL_FORCE 후보가 없음")
                # ==== [AGENT-EDIT END] ====
    
        # 🆕 Assembly 모드 스텝 선택지 분석 출력
        expansion_mode = getattr(self, 'assembly_expansion_mode', 1)
        # print(f"   📊 Assembly 스텝 선택지 분석 (Mode {expansion_mode}):")
        # print(f"      전체 후보: {len(total_candidates)}개")
        # print(f"      최종 선택 가능: {len(final_block_ids)}개")
    
        # Assembly 모드 통과율 계산
        if len(total_candidates) > 0:
            pass_rate = (len(final_block_ids) / len(total_candidates)) * 100
            # print(f"      통과율: {pass_rate:.1f}%")
    
        if final_block_ids:
            # print(f"      선택 가능 블록: {final_block_ids[:10]}{'...' if len(final_block_ids) > 10 else ''}")
            pass
        else:
            if debug_enabled:
                print(f"      ⚠️ 선택 가능한 블록 없음")
            self._print_candidate_diagnostics(selected_blocks, block_analysis, blocks, debug_enabled)
    
        # 🔍 디버깅: 최종 후보/제외 목록 출력
        try:
            analysis_lookup = {entry['block_id']: entry for entry in block_analysis}
            # print("   ▶︎ 최종 후보 요약:")
            if final_candidates:
                for candidate in final_candidates:
                    analysis = analysis_lookup.get(candidate.block_id, {})
                    inclusion = analysis.get('inclusion_reason') or '사유 없음'
                    assembly_type = candidate.assembly_type.value if getattr(candidate, 'assembly_type', None) else 'unknown'
                    line_group = getattr(candidate, 'line_group', '정보 없음')
                    start_date = ''
                    if getattr(candidate, 'assembly_start_date', None):
                        start_date = candidate.assembly_start_date.strftime('%Y-%m-%d')
                    # print(f"     - ID {candidate.block_id} ({assembly_type}/{line_group}), 착수일 {start_date}, 사유: {inclusion}")
            else:
                print("     - 없음")
    
            excluded_entries = [entry for entry in block_analysis if not entry.get('is_available')]
            if excluded_entries:
                # print(f"   ▶︎ 제외된 후보 {len(excluded_entries)}개")
                for entry in excluded_entries[:20]:
                    exclusion = entry.get('exclusion_reason') or '사유 미상'
                    # print(f"     · ID {entry['block_id']} ({entry.get('block_name')}), 이유: {exclusion}")
                # if len(excluded_entries) > 20:
                    # print(f"     · … 외 {len(excluded_entries) - 20}건")
        except Exception as debug_exc:
            print(f"   [DEBUG 출력 실패: {debug_exc}]")
    
        return final_block_ids, violations, block_analysis
    

    def set_decoding_type(self, decoding_type: str):
        """디코딩 타입 설정"""
        if decoding_type not in ["random", "assembly"]:
            raise ValueError(f"지원하지 않는 decoding_type: {decoding_type}")
        self.decoding_type = decoding_type
        # print(f"🔧 Decoding Type 설정: {decoding_type}")
    

    def set_assembly_blocks(self, blocks: List[EnhancedBlock]):
        """Assembly decoding용 전체 블록 설정"""
        self.all_blocks = blocks
        self.selected_blocks_set = set()
        # print(f"🔧 Assembly Decoding 블록 풀 설정: {len(blocks)}개")
    

    def set_assembly_expansion_mode(self, mode: int):
        """
        🚨 DEPRECATED: 이 함수는 더 이상 사용되지 않습니다.
        대신 ConstraintConfig의 assembly_expansion_* 설정을 사용하세요.
    
        호환성을 위해 유지되지만, Config 설정이 우선됩니다.
    
        Args:
            mode: 1 = 1일씩 확장 (기존), 2 = 2일씩 확장 (새로운 방식)
        """
        print(f"⚠️ DEPRECATED: set_assembly_expansion_mode() 대신 ConstraintConfig 사용을 권장합니다.")
        print(f"   예시: config.assembly_expansion_startday_mode = {2 if mode == 2 else 1}")
        print(f"        config.assembly_expansion_days = {mode}")
    
        # 호환성을 위한 임시 설정 (Config가 없을 때만)
        if not hasattr(self, 'constraint_config') or self.constraint_config is None:
            self.assembly_expansion_mode = mode
            # print(f"🔧 Fallback: Assembly 확장 모드 설정 Mode {mode}")
    

    def set_current_panel_date(self, panel_date: datetime):
        """현재 판넬 착수일 설정"""
        self.current_panel_date = panel_date
        self.relaxation_level = 0  # 새로운 날짜에서는 제약 완화 초기화
        # print(f"📅 현재 판넬 착수일 설정: {panel_date.strftime('%Y-%m-%d')}")
    

    def calculate_panel_start_date(self, assembly_start_date: datetime, assembly_type: AssemblyType, workload_factor: float = 1.0) -> datetime:
        """
        조립착수일 기준 판넬 착수일 계산
    
        Args:
            assembly_start_date: 조립 착수일
            assembly_type: 조립 타입
            workload_factor: 부하 계수 (1.0: 정상, >1.2: 과부하, <0.8: 저부하)
    
        Returns:
            판넬 착수 가능 최소 날짜
        """
        if assembly_type == AssemblyType.LINE:
            if workload_factor > 1.2:  # 과부하
                days_before = 1.5  # 최소 1.5일 전
            else:  # 정상/저부하
                days_before = 3.0  # 3일 전 (저부하시 더 빨라도 OK)
        elif assembly_type == AssemblyType.FIXED:
            days_before = 1.0
        elif assembly_type == AssemblyType.EXTERNAL_M:
            days_before = 3.0
        elif assembly_type == AssemblyType.INTERNAL_A:
            days_before = 1.5
        else:
            days_before = 2.0  # 기본값
    
        return assembly_start_date - timedelta(days=days_before)
    

    def get_relaxation_days(self, relaxation_level: int) -> int:
        """
        제약 완화 단계별 추가 여유 일수 반환 (+1일씩 단계적 확장)
    
        Args:
            relaxation_level: 완화 단계 (0~6)
    
        Returns:
            추가 여유 일수 (높을수록 더 일찍 착수 허용)
        """
        # [AGENT-EDIT] 최대 완화 폭을 +7일까지 확장 (0~6)
        return min(relaxation_level, 6)
    

    def filter_blocks_by_assembly_constraints(self, blocks: List[EnhancedBlock], current_panel_date: datetime, relaxation_level: int = 0) -> List[int]:
        """
        조립착수일 기준 제약조건으로 블록 필터링 (+1일씩 단계적 완화)
    
        Args:
            blocks: 전체 블록 리스트
            current_panel_date: 현재 판넬 착수일
            relaxation_level: 제약 완화 단계 (0~4)
    
        Returns:
            착수 가능한 블록 ID 리스트
        """
        available_blocks = []
        additional_days = self.get_relaxation_days(relaxation_level)
    
        for block in blocks:
            # 이미 선택된 블록은 제외
            if block.block_id in self.selected_blocks_set:
                continue
    
            # 기본 판넬 착수 가능 날짜 계산 (완화 없이)
            base_required_date = self.calculate_panel_start_date(
                block.assembly_start_date, 
                block.assembly_type, 
                1.0  # 기본 부하 계수
            )
    
            # 🆕 완화 적용: 추가 여유 일수만큼 더 일찍 착수 허용
            relaxed_required_date = base_required_date - timedelta(days=additional_days)
    
            # 현재 날짜에 착수 가능한지 체크
            if current_panel_date >= relaxed_required_date:
                available_blocks.append(block.block_id)
    
        return available_blocks
    

    def mark_block_selected(self, block_id: int):
        """블록을 선택됨으로 표시"""
        self.selected_blocks_set.add(block_id)
        if not hasattr(self, 'selection_history') or self.selection_history is None:
            self.selection_history = []
        self.selection_history.append(block_id)
    
        # 선택 결과 디버깅 출력
        try:
            block_obj = None
            if hasattr(self, 'blocks_dict') and isinstance(self.blocks_dict, dict):
                block_obj = self.blocks_dict.get(block_id)
    
            if block_obj is None and getattr(self, 'all_blocks', None):
                block_obj = next((blk for blk in self.all_blocks if getattr(blk, 'block_id', None) == block_id), None)
    
            block_name = getattr(block_obj, 'block_name', f'BLK_{block_id}') if block_obj else f'BLK_{block_id}'
            assembly_type = getattr(getattr(block_obj, 'assembly_type', None), 'value', '정보 없음') if block_obj else '정보 없음'
            line_group = getattr(block_obj, 'line_group', '정보 없음') if block_obj else '정보 없음'
            start_date = ''
            assembly_start = getattr(block_obj, 'assembly_start_date', None) if block_obj else None
            if assembly_start:
                try:
                    start_date = assembly_start.strftime('%Y-%m-%d')
                except Exception:
                    start_date = str(assembly_start)
            else:
                start_date = '정보 없음'
    
            step_no = len(self.selection_history)
    ######################################################################################################################################################################################
    #####                         최종 선택 블록 print(예: 최종 선택(step 65): 블록 63 (BLK_63C), 착수일 2025-07-02, 타입 line, 라인그룹 L_2)                                            #####
    ######################################################################################################################################################################################            
            # print(f"   ✅ 최종 선택(step {step_no}): 블록 {block_id} ({block_name}), 착수일 {start_date}, 타입 {assembly_type}, 라인그룹 {line_group}")
        except Exception as exc:
            print(f"   [선택 로그 출력 실패: {exc}]")
    

    def set_sequence(self, sequence: List[int]):
        """선택된 블록 순서 설정"""
        self.sequence_state.block_sequence = sequence
        self.sequence_state.sequence_decided = True
        self.sequence_state.current_phase = ProcessPhase.PROCESS_EXECUTION
    
        # P/S 매니저에도 순서 설정
        self.ps_manager.set_block_sequence(sequence)
    

    def reset(self):
        """상태 리셋"""
        self.sequence_state = SequenceState()
        self.last_assembly_type = None
        self.cross_seam_consecutive_count = 0
        self.line_b_fab_interval_count = 0
        self.selection_history = []
        self.reset_statistics()
    

    def reset_statistics(self):
        """통계 리셋"""
        for key in self.constraint_checks.keys():
            self.constraint_checks[key] = 0
            self.violation_counts[key] = 0
    

    def get_constraint_summary(self) -> Dict[str, str]:
        """제약조건 요약 정보 반환"""
        return {
            # P5 제약조건 (판계 작업)
            "P5#1": "납기 기반 착수일 체크",
            "P5#3": "라인 P/S 블록 연속 배정 (P→S 강제 순서)",
            "P5#4": "고정 P/S 블록 연속 배정 (P→S 강제 순서)",
            "P5#6": "라인 B FAB 간격 체크",
            "P5#8": "평일 심수 용량 체크 (75심 한계, 심+C/S 합계)",
            "P5#9": "72심 초과시 최소 17블록 체크 (사전 검증)",
            "P5#10": "주말 심수 용량 체크 (45심 한계)",
            "P5#11": "고정/라인조립 혼합 배정 (최대 3개 연속 제한)",
            "P5#12": "사내/사외 혼합 배정 (최대 3개 연속 제한)",
            "P5#13": "자재 미입고 상태 체크",
    
            # [AGENT-EDIT] P6#1,#2,#3은 제거된 legacy 시간 제약이고, P6#4만 유지한다.
            "P6#1": "제거된 legacy 시간 제약",
            "P6#2": "제거된 legacy 시간 제약",
            "P6#3": "제거된 legacy 시간 제약",
            "P6#4": "Cross seam 블록 혼합 배치 (pure cross seam 기준)",
    
            # P7 제약조건 (론지 취부) - 분기 선택에서 적용
            "P7#1": "론지 베이 부하 균등 배정",
            "P7#2": "21m 초과 → B베이 필수",
            "P7#3": "P/S 론지<7 동일베이 (1개 블록 취급)",
            "P7#7": "A베이 연속 배치 제한",
            "P7#8": "주판 Only 연속 제한",
            "P7#10": "론지 30+ → A베이 우선",
            "P7#11": "10번 블록 → B베이 연속 2판",
            "P7#12": "LT강재 → A베이 우선"
        }
    

    def update_state_after_action(self, processed_block: EnhancedBlock):
        """액션 후 상태 업데이트"""
        # 혼합 배정 상태 업데이트
        self.last_assembly_type = processed_block.assembly_type
    
        # 다음 단계로 진행
        self.advance_to_next_step()
    

    def advance_to_next_step(self):
        """다음 단계로 진행"""
        # 현재 구현에서는 단순히 로그만 출력
        # 필요시 추가 상태 업데이트 로직 구현
        pass
    
    # ========================================
    # 새로 추가된 제약조건 함수들 (P5#8,9,10,15,16)
    # ========================================
    

    def _get_current_block_count(self) -> int:
        """
        현재 처리된 블록 수 반환 (P5#9 제약조건용)
    
        Returns:
            현재까지 선택된 블록 수
        """
        # 수정: 현재 작업 중인 selected_blocks 수를 반환하도록 개선
        # get_next_available_blocks 함수에서 호출될 때는 
        # self._current_selected_blocks를 임시 저장하여 사용
        if hasattr(self, '_current_selected_blocks') and self._current_selected_blocks:
            return len(self._current_selected_blocks)
    
        # SequenceState에서 선택된 블록 리스트 가져오기 (fallback)
        if hasattr(self.sequence_state, 'selected_blocks'):
            return len(self.sequence_state.selected_blocks)
    
        # 기본값: 0개
        return 0
    

    def _set_current_selected_blocks(self, selected_blocks: List[int]):
        """
        현재 선택된 블록 리스트를 임시 저장 (P5#9 제약조건 체크용)
    
        Args:
            selected_blocks: 현재까지 선택된 블록 ID 리스트
        """
        self._current_selected_blocks = selected_blocks.copy() if selected_blocks else []
