# -*- coding: utf-8 -*-
"""평가 결과 파일 정리 유틸 (runner.py에서 분리)."""
# [AGENT-ADD] runner.py에서 분리한 파일 정리 유틸

from __future__ import annotations

import os
import shutil
from typing import List, Optional


def rename_excel_detailed_csv_files(
    date_keys: List[str],
    result_folder: Optional[str] = None,
    plan_label: Optional[str] = None
) -> None:
    """
    엑셀 방식에서 생성된 상세 CSV 파일들을 이름 변경
    integrated_learning_and_scheduling.py에서 생성되는 파일명과 동일하게 변경
    기존 파일이 있으면 덮어쓰기
    """
    label_suffix = ""
    if plan_label:
        label_suffix = f"_{plan_label}"

    # ==== [AGENT-EDIT BEGIN: 소스 파일 존재 여부에 따라 삭제/이동 제어] ====
    def _has_source_files() -> bool:
        for date_key in date_keys:
            if result_folder:
                for pattern in [
                    f"detailed_assembly_decoding_schedule_processes_{date_key}.csv",
                    f"detailed_assembly_schedule_info_{date_key}.csv",
                    f"detailed_assembly_bayselect_info_{date_key}.csv",
                ]:
                    candidate = os.path.join(result_folder, pattern)
                    if os.path.exists(candidate):
                        return True
            for pattern in [
                f"detailed_assembly_decoding_schedule_processes_{date_key}.csv",
                f"detailed_assembly_schedule_info_{date_key}.csv",
                f"detailed_assembly_bayselect_info_{date_key}.csv",
            ]:
                if os.path.exists(pattern):
                    return True
        return False

    def _has_target_files() -> bool:
        if result_folder and os.path.exists(result_folder):
            import glob
            return bool(glob.glob(os.path.join(result_folder, f"detailed_excel*{label_suffix}*")))
        return False

    if result_folder and os.path.exists(result_folder):
        # 소스가 없고 대상 파일이 이미 있으면 아무 것도 하지 않는다.
        if not _has_source_files() and _has_target_files():
            return
        import glob
        existing_files = glob.glob(os.path.join(result_folder, f"detailed_excel*{label_suffix}*"))
        for existing_file in existing_files:
            try:
                os.remove(existing_file)
            except Exception as e:
                print(f"     ⚠️ 파일 삭제 실패: {existing_file} - {e}")
    # ==== [AGENT-EDIT END] ====

    for date_key in date_keys:
        #  파일 경로 설정 (폴더 고려)
        if result_folder:
            base_old_process = os.path.join(result_folder, f"detailed_assembly_decoding_schedule_processes_{date_key}.csv")
            fallback_old_process = f"detailed_assembly_decoding_schedule_processes_{date_key}.csv"
            old_process_file = base_old_process if os.path.exists(base_old_process) else fallback_old_process
            new_process_file = os.path.join(result_folder, f"detailed_excel_schedule_processes_{date_key}{label_suffix}.csv")
        else:
            old_process_file = f"detailed_assembly_decoding_schedule_processes_{date_key}.csv"
            new_process_file = f"detailed_excel_schedule_processes_{date_key}{label_suffix}.csv"

        if os.path.exists(old_process_file):
            if os.path.exists(new_process_file):
                os.remove(new_process_file)
            shutil.move(old_process_file, new_process_file)

        # 스케줄 상세 정보 파일
        if result_folder:
            base_old_schedule = os.path.join(result_folder, f"detailed_assembly_schedule_info_{date_key}.csv")
            fallback_old_schedule = f"detailed_assembly_schedule_info_{date_key}.csv"
            old_schedule_file = base_old_schedule if os.path.exists(base_old_schedule) else fallback_old_schedule
            new_schedule_file = os.path.join(result_folder, f"detailed_excel_schedule_info_{date_key}{label_suffix}.csv")
        else:
            old_schedule_file = f"detailed_assembly_schedule_info_{date_key}.csv"
            new_schedule_file = f"detailed_excel_schedule_info_{date_key}{label_suffix}.csv"

        if os.path.exists(old_schedule_file):
            if os.path.exists(new_schedule_file):
                os.remove(new_schedule_file)
            shutil.move(old_schedule_file, new_schedule_file)

        # 베이 선택 상세 정보 파일
        if result_folder:
            base_old_bay = os.path.join(result_folder, f"detailed_assembly_bayselect_info_{date_key}.csv")
            fallback_old_bay = f"detailed_assembly_bayselect_info_{date_key}.csv"
            old_bay_file = base_old_bay if os.path.exists(base_old_bay) else fallback_old_bay
            new_bay_file = os.path.join(result_folder, f"detailed_excel_bayselect_info_{date_key}{label_suffix}.csv")
        else:
            old_bay_file = f"detailed_assembly_bayselect_info_{date_key}.csv"
            new_bay_file = f"detailed_excel_bayselect_info_{date_key}{label_suffix}.csv"

        if os.path.exists(old_bay_file):
            if os.path.exists(new_bay_file):
                os.remove(new_bay_file)
            shutil.move(old_bay_file, new_bay_file)


def rename_actionmasking_detailed_csv_files(date_keys: List[str], result_folder: Optional[str] = None) -> None:
    """
    착수일기준휴리스틱에서 생성된 상세 CSV 파일들을 이름 변경
    integrated_learning_and_scheduling.py에서 생성되는 파일명과 동일하게 변경
    기존 파일이 있으면 덮어쓰기
    """
    # ==== [AGENT-EDIT BEGIN: 소스 파일 존재 여부에 따라 삭제/이동 제어] ====
    def _has_source_files() -> bool:
        for date_key in date_keys:
            if result_folder:
                for pattern in [
                    f"detailed_assembly_decoding_schedule_processes_{date_key}.csv",
                    f"detailed_assembly_schedule_info_{date_key}.csv",
                    f"detailed_assembly_bayselect_info_{date_key}.csv",
                ]:
                    candidate = os.path.join(result_folder, pattern)
                    if os.path.exists(candidate):
                        return True
            for pattern in [
                f"detailed_assembly_decoding_schedule_processes_{date_key}.csv",
                f"detailed_assembly_schedule_info_{date_key}.csv",
                f"detailed_assembly_bayselect_info_{date_key}.csv",
            ]:
                if os.path.exists(pattern):
                    return True
        return False

    def _has_target_files() -> bool:
        if result_folder and os.path.exists(result_folder):
            import glob
            return bool(glob.glob(os.path.join(result_folder, "detailed_actionmasking_*")))
        return False

    if result_folder and os.path.exists(result_folder):
        # 소스가 없고 대상 파일이 이미 있으면 아무 것도 하지 않는다.
        if not _has_source_files() and _has_target_files():
            return
        import glob
        existing_files = glob.glob(os.path.join(result_folder, "detailed_actionmasking_*"))
        for existing_file in existing_files:
            try:
                os.remove(existing_file)
            except Exception as e:
                print(f"     ⚠️ 파일 삭제 실패: {existing_file} - {e}")
    # ==== [AGENT-EDIT END] ====

    for date_key in date_keys:
        #  파일 경로 설정 (폴더 고려)
        if result_folder:
            base_old_process = os.path.join(result_folder, f"detailed_assembly_decoding_schedule_processes_{date_key}.csv")
            fallback_old_process = f"detailed_assembly_decoding_schedule_processes_{date_key}.csv"
            old_process_file = base_old_process if os.path.exists(base_old_process) else fallback_old_process
            new_process_file = os.path.join(result_folder, f"detailed_actionmasking_schedule_processes_{date_key}.csv")
        else:
            old_process_file = f"detailed_assembly_decoding_schedule_processes_{date_key}.csv"
            new_process_file = f"detailed_actionmasking_schedule_processes_{date_key}.csv"

        if os.path.exists(old_process_file):
            if os.path.exists(new_process_file):
                os.remove(new_process_file)
            shutil.move(old_process_file, new_process_file)

        # 스케줄 상세 정보 파일
        if result_folder:
            base_old_schedule = os.path.join(result_folder, f"detailed_assembly_schedule_info_{date_key}.csv")
            fallback_old_schedule = f"detailed_assembly_schedule_info_{date_key}.csv"
            old_schedule_file = base_old_schedule if os.path.exists(base_old_schedule) else fallback_old_schedule
            new_schedule_file = os.path.join(result_folder, f"detailed_actionmasking_schedule_info_{date_key}.csv")
        else:
            old_schedule_file = f"detailed_assembly_schedule_info_{date_key}.csv"
            new_schedule_file = f"detailed_actionmasking_schedule_info_{date_key}.csv"

        if os.path.exists(old_schedule_file):
            if os.path.exists(new_schedule_file):
                os.remove(new_schedule_file)
            shutil.move(old_schedule_file, new_schedule_file)

        # 베이 선택 상세 정보 파일
        if result_folder:
            base_old_bay = os.path.join(result_folder, f"detailed_assembly_bayselect_info_{date_key}.csv")
            fallback_old_bay = f"detailed_assembly_bayselect_info_{date_key}.csv"
            old_bay_file = base_old_bay if os.path.exists(base_old_bay) else fallback_old_bay
            new_bay_file = os.path.join(result_folder, f"detailed_actionmasking_bayselect_info_{date_key}.csv")
        else:
            old_bay_file = f"detailed_assembly_bayselect_info_{date_key}.csv"
            new_bay_file = f"detailed_actionmasking_bayselect_info_{date_key}.csv"

        if os.path.exists(old_bay_file):
            if os.path.exists(new_bay_file):
                os.remove(new_bay_file)
            shutil.move(old_bay_file, new_bay_file)


def rename_detailed_csv_files(method_name: str, date_keys: List[str], result_folder: Optional[str] = None) -> None:
    """
    생성된 상세 CSV 파일들을 방법별로 이름 변경
    integrated_learning_and_scheduling.py에서 생성되는 파일명과 동일하게 변경
    기존 파일이 있으면 덮어쓰기
    """
    # ==== [AGENT-EDIT BEGIN: 소스 없음 시 기존 상세 파일 삭제 방지] ====
    def _has_any_source_files() -> bool:
        for date_key in date_keys:
            # 결과 폴더 우선
            if result_folder:
                for pattern in [
                    f"detailed_assembly_decoding_schedule_processes_{date_key}.csv",
                    f"detailed_assembly_schedule_info_{date_key}.csv",
                    f"detailed_assembly_bayselect_info_{date_key}.csv",
                ]:
                    candidate = os.path.join(result_folder, pattern)
                    if os.path.exists(candidate):
                        return True
            # cwd fallback
            for pattern in [
                f"detailed_assembly_decoding_schedule_processes_{date_key}.csv",
                f"detailed_assembly_schedule_info_{date_key}.csv",
                f"detailed_assembly_bayselect_info_{date_key}.csv",
            ]:
                if os.path.exists(pattern):
                    return True
        return False

    if result_folder and os.path.exists(result_folder):
        if _has_any_source_files():
            import glob
            existing_files = glob.glob(os.path.join(result_folder, f"detailed_{method_name}_*"))
            for existing_file in existing_files:
                try:
                    os.remove(existing_file)
                except Exception as e:
                    print(f"     ⚠️ 파일 삭제 실패: {existing_file} - {e}")
        else:
            # 소스가 없으면 기존 detailed_{method} 유지
            return
    # ==== [AGENT-EDIT END] ====

    for date_key in date_keys:
        #  파일 경로 설정 (폴더 고려)
        if result_folder:
            base_old_process = os.path.join(result_folder, f"detailed_assembly_decoding_schedule_processes_{date_key}.csv")
            fallback_old_process = f"detailed_assembly_decoding_schedule_processes_{date_key}.csv"
            old_process_file = base_old_process if os.path.exists(base_old_process) else fallback_old_process
            new_process_file = os.path.join(result_folder, f"detailed_{method_name}_schedule_processes_{date_key}.csv")
        else:
            old_process_file = f"detailed_assembly_decoding_schedule_processes_{date_key}.csv"
            new_process_file = f"detailed_{method_name}_schedule_processes_{date_key}.csv"

        if os.path.exists(old_process_file):
            if os.path.exists(new_process_file):
                os.remove(new_process_file)
            shutil.move(old_process_file, new_process_file)

        # 스케줄 상세 정보 파일
        if result_folder:
            base_old_schedule = os.path.join(result_folder, f"detailed_assembly_schedule_info_{date_key}.csv")
            fallback_old_schedule = f"detailed_assembly_schedule_info_{date_key}.csv"
            old_schedule_file = base_old_schedule if os.path.exists(base_old_schedule) else fallback_old_schedule
            new_schedule_file = os.path.join(result_folder, f"detailed_{method_name}_schedule_info_{date_key}.csv")
        else:
            old_schedule_file = f"detailed_assembly_schedule_info_{date_key}.csv"
            new_schedule_file = f"detailed_{method_name}_schedule_info_{date_key}.csv"

        if os.path.exists(old_schedule_file):
            if os.path.exists(new_schedule_file):
                os.remove(new_schedule_file)
            shutil.move(old_schedule_file, new_schedule_file)

        # 베이 선택 상세 정보 파일
        if result_folder:
            base_old_bay = os.path.join(result_folder, f"detailed_assembly_bayselect_info_{date_key}.csv")
            fallback_old_bay = f"detailed_assembly_bayselect_info_{date_key}.csv"
            old_bay_file = base_old_bay if os.path.exists(base_old_bay) else fallback_old_bay
            new_bay_file = os.path.join(result_folder, f"detailed_{method_name}_bayselect_info_{date_key}.csv")
        else:
            old_bay_file = f"detailed_assembly_bayselect_info_{date_key}.csv"
            new_bay_file = f"detailed_{method_name}_bayselect_info_{date_key}.csv"

        if os.path.exists(old_bay_file):
            if os.path.exists(new_bay_file):
                os.remove(new_bay_file)
            shutil.move(old_bay_file, new_bay_file)


__all__ = [
    "rename_excel_detailed_csv_files",
    "rename_actionmasking_detailed_csv_files",
    "rename_detailed_csv_files",
]
