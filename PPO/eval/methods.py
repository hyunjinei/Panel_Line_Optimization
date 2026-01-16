# -*- coding: utf-8 -*-
"""평가 방법 실행 모음 (runner.py에서 분리)."""
# [AGENT-ADD] runner.py에서 분리한 평가 메서드

from __future__ import annotations

import os
import traceback
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch

from runtime_config import get_runtime_config
from enhanced_environment.models import ConstraintViolation
from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import (
    run_assembly_decoding_sequence_with_blocks,
    save_detailed_process_schedule_assembly,
    create_block_result
)
from scheduling.start_date.action_sequence_착수일기준휴리스틱 import create_actionmasking_schedule
from scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱 import (
    create_makespan_schedule,
    apply_plan_sequence_from_excel,
    PLAN_SHEET_PRIORITY,
)
from utils.csv_save import (
    save_assembly_decoding_schedule_info,
    save_assembly_decoding_bay_info,
)

from PPO.models.single_step_actor import (
    SingleStepPtrNet,
    ENV_STATE_DIM,
    BLOCK_FEATURE_DIM,
    REDUCED_ENV_STATE_DIM,
    REDUCED_BLOCK_FEATURE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    CONSTRAINT_BLOCK_FEATURE_DIM
)
from scheduling.assembly_start.rl_assembly_scheduler import run_rl_assembly_decoding_sequence_with_blocks

from PPO.eval.helpers import sanitize_label_for_filename
from PPO.eval.files import (
    rename_excel_detailed_csv_files,
    rename_actionmasking_detailed_csv_files,
    rename_detailed_csv_files,
)


def _get_setting(settings: Optional[Dict[str, object]], key: str, default: object) -> object:
    if not settings:
        return default
    return settings.get(key, default)


def run_excel_heuristic(
    blocks: List,
    metadata: Dict,
    start_date: str,
    result_folder: str = None,
    plan_label: Optional[str] = None,
) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """
    엑셀 순번 기반 휴리스틱 실행 (MODE 2에서만 사용)
    integrated_learning_and_scheduling.py와 동일한 방식
    """
    if plan_label:
        print(f" 엑셀 순번 기반 휴리스틱 실행 중... (계획 시트: {plan_label})")
    else:
        print(" 엑셀 순번 기반 휴리스틱 실행 중...")

    raw_label = (plan_label or "").strip()
    label_suffix = sanitize_label_for_filename(plan_label)
    if raw_label in ("기본", "default", "DEFAULT"):
        label_suffix = ""
    elif label_suffix:
        label_suffix = f"_{label_suffix}"

    suffixed_output_csv = None
    if label_suffix:
        suffixed_output_csv = (
            os.path.join(result_folder, f"excel_evaluation_results{label_suffix}.csv")
            if result_folder
            else f"excel_evaluation_results{label_suffix}.csv"
        )
        if os.path.exists(suffixed_output_csv):
            os.remove(suffixed_output_csv)

    try:
        # 🆕 현재 작업 디렉토리 변경 (CSV 파일이 결과 폴더에 생성되도록)
        original_cwd = os.getcwd()
        if result_folder:
            os.chdir(result_folder)

        try:
            # integrated_learning_and_scheduling.py와 동일한 방식으로 실행
            excel_results, excel_violations = create_makespan_schedule(blocks, metadata)
        finally:
            # 작업 디렉토리 복원
            os.chdir(original_cwd)

        # 통계 계산 (integrated_learning_and_scheduling.py와 동일)
        if excel_results:
            # 실제 시작~끝 시간 차이로 계산 (연속 생산 고려)
            excel_start_times = []
            excel_end_times = []
            for result in excel_results:
                if 'start_time' in result and result['start_time']:
                    try:
                        start_dt = pd.to_datetime(result['start_time'])
                        excel_start_times.append(start_dt)
                    except Exception:
                        pass
                if 'end_time' in result and result['end_time']:
                    try:
                        end_dt = pd.to_datetime(result['end_time'])
                        excel_end_times.append(end_dt)
                    except Exception:
                        pass

            if excel_start_times and excel_end_times:
                excel_min_time = min(excel_start_times)
                excel_max_time = max(excel_end_times)
                excel_makespan = (excel_max_time - excel_min_time).total_seconds() / 3600.0
            else:
                # Fallback: 기존 방식
                makespan_by_date = {}
                for result in excel_results:
                    date = result.get('date', 'N/A')
                    makespan = result.get('makespan_hours', 0)
                    if date not in makespan_by_date or makespan > makespan_by_date[date]:
                        makespan_by_date[date] = makespan
                excel_makespan = sum(makespan_by_date.values())
        else:
            excel_makespan = 0.0

        #  실제 위반 카운트 계산 (제약 위반 객체 기반)
        actual_violations_count = sum(
            1 for v in excel_violations if getattr(v, 'severity', '') in ['ERROR', 'WARNING']
        )

        statistics = {
            'makespan_hours': excel_makespan,
            'total_violations': actual_violations_count  #  실제 ERROR/WARNING 위반만 카운트
        }

        # CSV 저장
        if excel_results:
            df = pd.DataFrame(excel_results)
            if suffixed_output_csv:
                df.to_csv(suffixed_output_csv, index=False, encoding='utf-8-sig')
            else:
                output_csv = (
                    os.path.join(result_folder, "excel_evaluation_results.csv")
                    if result_folder
                    else "excel_evaluation_results.csv"
                )
                if os.path.exists(output_csv):
                    os.remove(output_csv)
                df.to_csv(output_csv, index=False, encoding='utf-8-sig')

        # 🆕 엑셀 방식에서 생성된 상세 CSV 파일들 이름 변경
        if excel_results:
            date_keys = list(set([r.get('date', '20250101') for r in excel_results if r.get('date')]))
            #  파일들이 PPO 폴더에 생성되므로 작업 디렉토리 복원 후 처리
            os.chdir(original_cwd)
            rename_excel_detailed_csv_files(date_keys, result_folder, plan_label)
            # 다시 결과 폴더로 이동 (finally에서 복원됨)
            if result_folder:
                os.chdir(result_folder)

        episode_data = []
        env = None

        if plan_label:
            print(f"   엑셀 순번({plan_label}) 완료: Makespan = {excel_makespan:.2f}시간")
        else:
            print(f"   엑셀 순번 완료: Makespan = {excel_makespan:.2f}시간")
        return excel_results, statistics, episode_data, env

    except Exception as e:
        if plan_label:
            print(f"   ❌ 엑셀 순번({plan_label}) 실행 실패: {e}")
        else:
            print(f"   ❌ 엑셀 순번 실행 실패: {e}")
        return [], {'makespan_hours': 0, 'total_violations': 0}, [], None


def run_actionmasking_heuristic(excel_path: str, result_folder: str = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """
    착수일기준휴리스틱 실행 (MODE 2에서만 사용)
    integrated_learning_and_scheduling.py와 동일한 방식
    """
    print("착수일기준휴리스틱 실행 중...")

    if result_folder:
        output_csv = os.path.join(result_folder, "actionmasking_evaluation_results.csv")
    else:
        output_csv = "actionmasking_evaluation_results.csv"

    if os.path.exists(output_csv):
        os.remove(output_csv)

    try:
        original_cwd = os.getcwd()
        if result_folder:
            os.chdir(result_folder)

        try:
            am_results, bay_analysis_by_date, step_info_by_date = create_actionmasking_schedule(excel_path)
            from utils.csv_save import save_detailed_masking_info, save_detailed_bay_selection_info

            print(f"  상세 분석 CSV 저장 중...")
            save_detailed_bay_selection_info(bay_analysis_by_date)
            for date_key, (daily_step_info, blocks_in_date) in step_info_by_date.items():
                if daily_step_info:
                    save_detailed_masking_info(date_key, daily_step_info, blocks_in_date)
                    print(f"    Action Masking 스텝 분석: detailed_actionmasking_schedule_info_{date_key}.csv")
        finally:
            os.chdir(original_cwd)

        if am_results:
            am_start_times = []
            am_end_times = []
            for result in am_results:
                if 'start_time' in result and result['start_time']:
                    try:
                        start_dt = pd.to_datetime(result['start_time'])
                        am_start_times.append(start_dt)
                    except Exception:
                        pass
                if 'end_time' in result and result['end_time']:
                    try:
                        end_dt = pd.to_datetime(result['end_time'])
                        am_end_times.append(end_dt)
                    except Exception:
                        pass

            if am_start_times and am_end_times:
                am_min_time = min(am_start_times)
                am_max_time = max(am_end_times)
                am_makespan = (am_max_time - am_min_time).total_seconds() / 3600.0
            else:
                makespan_by_date = {}
                for result in am_results:
                    date = result.get('date', 'N/A')
                    makespan = result.get('makespan_hours', 0)
                    if date not in makespan_by_date or makespan > makespan_by_date[date]:
                        makespan_by_date[date] = makespan
                am_makespan = sum(makespan_by_date.values())
        else:
            am_makespan = 0.0

        am_violations = []
        for result in am_results:
            if result.get('violations', 0) > 0:
                violation_details = result.get('violation_details', [])
                constraint_ids = result.get('constraint_ids', [])
                violation_severity = result.get('violation_severity', [])

                for i, constraint_id in enumerate(constraint_ids):
                    detail = violation_details[i] if i < len(violation_details) else "위반 상세 정보 없음"
                    severity = violation_severity[i] if i < len(violation_severity) else "INFO"

                    if severity in ['ERROR', 'WARNING'] and not constraint_id.endswith('_DETAIL'):
                        violation = ConstraintViolation(
                            constraint_id=constraint_id,
                            message=detail,
                            block_id=result['block_id'],
                            severity=severity
                        )
                        am_violations.append(violation)

        # [AGENT-EDIT] C/Seam 위반 카운트 포함
        cseam_ids = {"ROUTING_C_SEAM_SPACING", "C_SEAM_SPACING"}
        total_cseam_violations = 0
        for v in am_violations:
            if getattr(v, "constraint_id", "") in cseam_ids and getattr(v, "severity", "").upper() in {"ERROR", "WARNING"}:
                total_cseam_violations += 1

        statistics = {
            'makespan_hours': am_makespan,
            'total_violations': len(am_violations),
            'total_cseam_violations': total_cseam_violations
        }

        if am_results:
            df = pd.DataFrame(am_results)
            df.to_csv(output_csv, index=False, encoding='utf-8-sig')

        if am_results:
            date_keys = list(set([r.get('date', '20250101') for r in am_results if r.get('date')]))
            rename_actionmasking_detailed_csv_files(date_keys, result_folder)

        episode_data = []
        env = None
        print(f"   착수일기준휴리스틱 완료: Makespan = {am_makespan:.2f}시간")
        return am_results, statistics, episode_data, env

    except Exception as e:
        print(f"   ❌ 착수일기준휴리스틱 실행 실패: {e}")
        return [], {'makespan_hours': 0, 'total_violations': 0}, [], None


def _get_common_settings(settings: Optional[Dict[str, object]]) -> Tuple[int, int, bool]:
    max_days = int(_get_setting(settings, "assembly_max_days", 20))
    date_offset = int(_get_setting(settings, "assembly_date_offset", 0))
    save_detailed = bool(_get_setting(settings, "save_detailed_csv", True))
    return max_days, date_offset, save_detailed


def run_spt_heuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                      settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    print(" SPT 휴리스틱 실행 중...")

    output_csv = os.path.join(result_folder, "spt_evaluation_results.csv") if result_folder else "spt_evaluation_results.csv"
    if os.path.exists(output_csv):
        os.remove(output_csv)

    max_days, date_offset, save_detailed = _get_common_settings(settings)
    results, statistics = run_assembly_decoding_sequence_with_blocks(
        blocks=blocks,
        metadata=metadata,
        decoding_type="assembly",
        selection_method="spt",
        max_days=max_days,
        start_date=start_date,
        date_offset=date_offset,
        output_csv=output_csv,
        save_csv=True,
        save_detailed=save_detailed,
        forced_sequence=None
    )

    episode_data = []
    env = None
    print(f" SPT 완료: Makespan = {statistics.get('makespan_hours', 0):.2f}시간")
    return results, statistics, episode_data, env


def run_lpt_heuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                      settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    print(" LPT 휴리스틱 실행 중...")

    output_csv = os.path.join(result_folder, "lpt_evaluation_results.csv") if result_folder else "lpt_evaluation_results.csv"
    if os.path.exists(output_csv):
        os.remove(output_csv)

    max_days, date_offset, save_detailed = _get_common_settings(settings)
    results, statistics = run_assembly_decoding_sequence_with_blocks(
        blocks=blocks,
        metadata=metadata,
        decoding_type="assembly",
        selection_method="lpt",
        max_days=max_days,
        start_date=start_date,
        date_offset=date_offset,
        output_csv=output_csv,
        save_csv=True,
        save_detailed=save_detailed,
        forced_sequence=None
    )

    episode_data = []
    env = None
    print(f"    LPT 완료: Makespan = {statistics.get('makespan_hours', 0):.2f}시간")
    return results, statistics, episode_data, env


def run_seam_min_heuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                           settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    print("🌊 SEAM_MIN 휴리스틱 실행 중...")

    output_csv = os.path.join(result_folder, "seam_min_evaluation_results.csv") if result_folder else "seam_min_evaluation_results.csv"
    if os.path.exists(output_csv):
        os.remove(output_csv)

    max_days, date_offset, save_detailed = _get_common_settings(settings)
    results, statistics = run_assembly_decoding_sequence_with_blocks(
        blocks=blocks,
        metadata=metadata,
        decoding_type="assembly",
        selection_method="seam_min",
        max_days=max_days,
        start_date=start_date,
        date_offset=date_offset,
        output_csv=output_csv,
        save_csv=True,
        save_detailed=save_detailed,
        forced_sequence=None
    )

    episode_data = []
    env = None
    print(f"   SEAM_MIN 완료: Makespan = {statistics.get('makespan_hours', 0):.2f}시간")
    return results, statistics, episode_data, env


def run_rl_evaluation(blocks: List, metadata: Dict, start_date: str, result_folder: str = None, num_samples: int = 50,
                      settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """
    RL 모델 평가 실행 (샘플링 후 최고 성능 선택)
    """
    print(f" RL 모델 평가 실행 중 ({num_samples}번 샘플링)...")

    rl_model_path = str(_get_setting(settings, "rl_model_path", "") or "").strip()
    if not rl_model_path:
        print("❌ RL 모델 경로가 비어있습니다.")
        return [], {}, [], None

    if not os.path.exists(rl_model_path):
        print(f"❌ RL 모델 파일을 찾을 수 없습니다: {rl_model_path}")
        return [], {}, [], None

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    cfg = get_runtime_config() or {}
    if isinstance(cfg, dict):
        cfg_device = cfg.get("device") or (cfg.get("evaluation") or {}).get("device")
        if cfg_device:
            dev = str(cfg_device).strip().lower()
            if dev == "cpu":
                device = torch.device("cpu")
            elif dev.startswith("cuda"):
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"   🔧 디바이스: {device}")

    # ==== [AGENT-EDIT BEGIN: RL checkpoint shape auto-match] ====
    def _extract_actor_state_dict(ckpt: Dict) -> Dict:
        if isinstance(ckpt, dict):
            if 'actor_state_dict' in ckpt:
                return ckpt['actor_state_dict']
            if 'model_state_dict' in ckpt:
                return ckpt['model_state_dict']
        return ckpt

    def _infer_feature_mode_from_dim(dim: Optional[int]) -> str:
        if dim == REDUCED_BLOCK_FEATURE_DIM:
            return "reduced"
        if dim == CONSTRAINT_BLOCK_FEATURE_DIM:
            return "constraint"
        if dim == BLOCK_FEATURE_DIM:
            return "full"
        return str(_get_setting(settings, "feature_mode", "reduced"))

    def _infer_actor_params_from_state(state_dict: Dict) -> Dict:
        inferred = {}
        embed_w = state_dict.get('embedding.0.weight')
        embed2_w = state_dict.get('embedding.2.weight')
        if embed_w is not None:
            inferred['embedding_dim'] = int(embed_w.shape[0])
            inferred['feature_dim'] = int(embed_w.shape[1])
        if embed2_w is not None:
            inferred['hidden_dim'] = int(embed2_w.shape[0])
        if 'env_state_mean' in state_dict:
            inferred['use_env_state'] = True
            inferred['env_state_dim'] = int(state_dict['env_state_mean'].shape[0])
        else:
            inferred['use_env_state'] = False
        inferred['use_positional_encoding'] = 'positional_encoding' in state_dict
        if 'feature_dim' in inferred:
            inferred['feature_mode'] = _infer_feature_mode_from_dim(inferred['feature_dim'])
        return inferred

    checkpoint = torch.load(rl_model_path, map_location=device)
    actor_state = _extract_actor_state_dict(checkpoint)
    inferred = _infer_actor_params_from_state(actor_state)

    feature_mode = inferred.get('feature_mode', str(_get_setting(settings, "feature_mode", "reduced")))
    if feature_mode == "reduced":
        fallback_feature_dim = REDUCED_BLOCK_FEATURE_DIM
        fallback_env_dim = REDUCED_ENV_STATE_DIM
    elif feature_mode == "constraint":
        fallback_feature_dim = CONSTRAINT_BLOCK_FEATURE_DIM
        fallback_env_dim = CONSTRAINT_ENV_STATE_DIM
    else:
        fallback_feature_dim = BLOCK_FEATURE_DIM
        fallback_env_dim = ENV_STATE_DIM

    actor_params = {
        'embedding_dim': inferred.get('embedding_dim', 256),
        'hidden_dim': inferred.get('hidden_dim', inferred.get('embedding_dim', 256)),
        'n_layers': 2,
        'n_heads': 4,
        'dropout': 0.1,
        'use_logit_clipping': True,
        'C': 10.0,
        'T': 0.5,
        'feature_dim': inferred.get('feature_dim', fallback_feature_dim),
        'feature_mode': feature_mode,
        'use_positional_encoding': inferred.get('use_positional_encoding', bool(_get_setting(settings, "use_positional_encoding", False))),
        'use_env_state': inferred.get('use_env_state', True),
        'env_state_dim': inferred.get('env_state_dim', fallback_env_dim)
    }
    print(
        "   🔧 RL 모델 파라미터 자동 추론: "
        f"feature_mode={actor_params['feature_mode']}, "
        f"feature_dim={actor_params['feature_dim']}, "
        f"env_state_dim={actor_params['env_state_dim']}, "
        f"embedding_dim={actor_params['embedding_dim']}, "
        f"hidden_dim={actor_params['hidden_dim']}, "
        f"use_env_state={actor_params['use_env_state']}, "
        f"use_positional_encoding={actor_params['use_positional_encoding']}"
    )
    # ==== [AGENT-EDIT END] ====

    model = SingleStepPtrNet(**actor_params).to(device)
    model.load_state_dict(actor_state, strict=False)
    model.eval()
    print(f"   ✅ PPO 모델 로드 완료: {rl_model_path}")

    best_results = None
    best_statistics = None
    best_episode_data = None
    best_env = None
    best_makespan = float('inf')

    save_detailed_flag = bool(_get_setting(settings, "save_detailed_csv", True))

    for i in range(num_samples):
        print(f"   샘플 {i+1}: ", end="")
        schedule_results, stats, episode_data, env = run_rl_assembly_decoding_sequence_with_blocks(
            blocks=blocks,
            metadata=metadata,
            rl_agent=model,
            device=device,
            start_date=start_date,
            max_days=int(_get_setting(settings, "assembly_max_days", 20)),
            date_offset=int(_get_setting(settings, "assembly_date_offset", 0)),
            output_csv=(
                os.path.join(result_folder, "rl_best_results.csv")
                if result_folder
                else "rl_best_results.csv"
            ),
            save_csv=True,
            # [AGENT-EDIT] 상세 CSV는 best 갱신 시점에만 생성
            save_detailed=False,
            training_mode=False,
        )

        makespan = stats.get('makespan_hours', float('inf')) if stats else float('inf')
        print(f"{makespan:.2f}h (현재 최고: {best_makespan:.2f}h)")

        if makespan < best_makespan:
            best_makespan = makespan
            best_results = schedule_results
            best_statistics = stats
            best_episode_data = episode_data
            best_env = env
            print(f"   🏆 새로운 최고 성능! {best_makespan:.2f}h")

            # ==== [AGENT-EDIT BEGIN: Best 결과 기반 상세 CSV 생성] ====
            if best_results and save_detailed_flag:
                print("    Best 결과 기반 Detailed CSV 생성 중...")
                date_keys = list(set([r.get('date', '20250101') for r in best_results if r.get('date')]))

                # 기존 상세 파일 삭제 로그 출력
                for date_key in date_keys:
                    for prefix in [
                        "detailed_assembly_schedule_info_",
                        "detailed_assembly_bayselect_info_",
                        "detailed_assembly_decoding_schedule_processes_",
                    ]:
                        filename = f"{prefix}{date_key}.csv"
                        candidates = [filename]
                        if result_folder:
                            candidates.append(os.path.join(result_folder, filename))
                        for candidate in candidates:
                            if os.path.exists(candidate):
                                print(f"    기존 파일 삭제: {os.path.basename(candidate)}")
                                try:
                                    os.remove(candidate)
                                except Exception:
                                    pass

                forced_sequence = [
                    r.get("block_id")
                    for r in sorted(best_results, key=lambda r: r.get("am_sequence", 0))
                    if r.get("block_id") is not None
                ]
                if forced_sequence:
                    run_rl_assembly_decoding_sequence_with_blocks(
                        blocks=blocks,
                        metadata=metadata,
                        rl_agent=model,
                        device=device,
                        start_date=start_date,
                        max_days=int(_get_setting(settings, "assembly_max_days", 20)),
                        date_offset=int(_get_setting(settings, "assembly_date_offset", 0)),
                        output_csv=os.path.join(result_folder, "_rl_detail_rebuild.csv")
                        if result_folder
                        else "_rl_detail_rebuild.csv",
                        save_csv=False,
                        save_detailed=True,
                        training_mode=False,
                        forced_sequence=forced_sequence,
                    )
                print("    Best 결과 기반 Detailed CSV 생성 완료")
            # ==== [AGENT-EDIT END] ====

    # [AGENT-ADD] 최종 RL 결과 CSV 확정 저장 (best 결과 보장)
    if best_results is not None:
        output_csv = (
            os.path.join(result_folder, "rl_best_results.csv")
            if result_folder
            else "rl_best_results.csv"
        )
        try:
            pd.DataFrame(best_results).to_csv(output_csv, index=False, encoding='utf-8-sig')
        except Exception as e:
            print(f"   ⚠️ RL 결과 CSV 저장 실패: {e}")

    # [AGENT-EDIT] best 갱신 시 상세 생성으로 이동 (중복 재생성 제거)

    return best_results or [], best_statistics or {}, best_episode_data or [], best_env


__all__ = [
    "run_excel_heuristic",
    "run_actionmasking_heuristic",
    "run_spt_heuristic",
    "run_lpt_heuristic",
    "run_seam_min_heuristic",
    "run_rl_evaluation",
]
