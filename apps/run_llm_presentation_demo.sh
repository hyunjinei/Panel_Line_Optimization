#!/usr/bin/env bash
set -euo pipefail

# [AGENT-ADD] Presentation-only LLM emergency rescheduling demo runner.
cd "$(dirname "$0")/.."

if [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
  source "$HOME/anaconda3/etc/profile.d/conda.sh"
  conda activate accord_env
fi

export PBS_FORCE_DEBUG="${PBS_FORCE_DEBUG:-0}"
export PBS_DEBUG_VERBOSE="${PBS_DEBUG_VERBOSE:-0}"

exec streamlit run apps/streamlit_llm_presentation_demo.py \
  --server.address 0.0.0.0 \
  --server.port "${PBS_PRESENTATION_PORT:-8503}"
