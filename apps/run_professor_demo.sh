#!/usr/bin/env bash
set -euo pipefail

# [AGENT-EDIT] Resolve paths from this script so the demo works on the server copy too.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PORT="${1:-8501}"

if [[ -f "${HOME}/anaconda3/etc/profile.d/conda.sh" ]]; then
  source "${HOME}/anaconda3/etc/profile.d/conda.sh"
  conda activate accord_env
elif [[ -f "/home/hyunjin/accord_env/bin/activate" ]]; then
  source "/home/hyunjin/accord_env/bin/activate"
fi

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/xdg-cache}"

echo "[PBS DEMO] Streamlit GUI 시작"
echo "[PBS DEMO] URL: http://localhost:${PORT}"
echo "[PBS DEMO] 교수님 캡처용 추천 시나리오: 긴급 4번째 투입 / 선후행 변경 / 일일 상한 / C/Seam 완화"

streamlit run apps/streamlit_scheduler_chat.py --server.address 0.0.0.0 --server.port "$PORT"
