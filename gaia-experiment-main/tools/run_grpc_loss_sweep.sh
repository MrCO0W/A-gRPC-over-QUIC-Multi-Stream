#!/usr/bin/env bash
# ==============================================================================
# Reproducible Runner: gRPC over TCP vs QUIC Loss-Rate Comparison Sweep (0%–5%)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PY="${REPO_ROOT}/.venv/bin/python"
if [ ! -f "${PY}" ]; then
  PY="python3"
fi

HERDR_SESSION="${HERDR_SESSION:-mam-gaia-exp}"

echo "========================================================================"
echo " 🚀 Starting gRPC over TCP vs QUIC Loss-Rate Sweep (0%–5% Loss)"
echo " Session: ${HERDR_SESSION}"
echo "========================================================================"

# 1. Ensure binaries are built
make -C apps/grpc-perf build
make -C apps/grpc-quic-perf build

# 2. Run 12-run sweep
for loss in 0 1 2 3 4 5; do
  for proto in tcp quic; do
    echo ""
    echo ">>> [Sweep] Running: Protocol=${proto^^}, Loss=${loss}% <<<"
    "${PY}" .agents/skills/gaia-run-analyze/scripts/run.py \
      -t "config/topo/topo_grpc_comparison_loss${loss}" \
      -x "config/xp/xp_grpc_comparison_${proto}" \
      --herdr-session "${HERDR_SESSION}" \
      --allow-custom-cmd \
      --skip-preflight \
      -o "output/grpc_loss_comparison/${proto}_loss${loss}" \
      --json
  done
done

echo ""
echo "========================================================================"
echo " 📊 Aggregating Results & Generating Publication Figures..."
echo "========================================================================"
"${PY}" tools/analyze_grpc_loss_comparison.py

echo "✓ Sweep and analysis complete! Results saved to docs/figures/grpc_loss_comparison/"
