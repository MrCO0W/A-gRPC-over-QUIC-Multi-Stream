#!/usr/bin/env bash
#
# Drive Experiment C (downlink command delivery) through the GAIA workflow CLI.
# Same mechanics as run_sweep.sh; only the manifest, the binaries, the result
# file name (ctrlbench.json) and the progress line differ.
#
# Every cell of the matrix is executed `repeats` times with an explicit output
# directory, so the analyser can find results without guessing at timestamps
# and a re-run of one cell never clobbers another.
#
#   sudo ./run_ctrl_sweep.sh                       # use scripts/manifest_ctrl.json
#   sudo MANIFEST=/path/manifest_ctrl.json ./run_ctrl_sweep.sh
#   sudo REPEATS=1 ./run_ctrl_sweep.sh             # quick smoke pass
#
# ONLY takes an extended regular expression matched against the scenario name
# (xp_test_ctrl_<regime>_<app>_<proto>[_i<ms>]_l<loss>) and restricts the
# sweep to matching cells.
# Combined with REPEATS it tops up a few cells without re-running the matrix,
# because a rep whose ctrlbench.json already exists is skipped:
#
#   sudo ONLY='_idle_(sse|grpc)_' ./run_ctrl_sweep.sh
#   sudo ONLY='_l5$' REPEATS=8 ./run_ctrl_sweep.sh   # top up the 5 % cells
#
# Mininet needs root, hence sudo. Results land in <gaia>/output/test_ctrl/.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$HERE/manifest_ctrl.json}"
ONLY="${ONLY:-}"

if [[ ! -f "$MANIFEST" ]]; then
  echo "error: manifest not found at $MANIFEST" >&2
  echo "       run gen_ctrl_scenarios.py first" >&2
  exit 2
fi

PY="${PY:-python3}"
GAIA="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['gaia'])" "$MANIFEST")"
RPCBENCH="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['rpcbench'])" "$MANIFEST")"
REPEATS="${REPEATS:-$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['repeats'])" "$MANIFEST")}"

OUT_ROOT="${OUT_ROOT:-$GAIA/output/test_ctrl}"

echo "gaia      : $GAIA"
echo "rpcbench  : $RPCBENCH"
echo "repeats   : $REPEATS"
[[ -n "$ONLY" ]] && echo "only      : $ONLY"
echo "output    : $OUT_ROOT"
echo

for f in ctrlgw ctrledge; do
  if [[ ! -x "$RPCBENCH/bin/$f" ]]; then
    echo "error: $RPCBENCH/bin/$f is missing - run './build.sh build' in rpcbench first" >&2
    exit 2
  fi
done
if [[ ! -f "$RPCBENCH/certs/ca.pem" ]]; then
  echo "error: $RPCBENCH/certs/ca.pem is missing - run './build.sh certs' in rpcbench first" >&2
  exit 2
fi

mkdir -p "$OUT_ROOT"
SUMMARY="$OUT_ROOT/sweep.log"
: > "$SUMMARY"

# Preflight once per topology rather than once per run: the reachability gate
# is a property of the topology, and at 5 % loss it is slow enough to matter.
mapfile -t TOPOS < <("$PY" -c "
import json,re,sys
m=json.load(open(sys.argv[1]))
only=sys.argv[2]
runs=[r for r in m['runs'] if not only or re.search(only, r['name'])]
for t in sorted({r['topo'] for r in runs}): print(t)
" "$MANIFEST" "$ONLY")

cd "$GAIA" || exit 2

for t in "${TOPOS[@]}"; do
  echo "== preflight $(basename "$t")"
  if ! "$PY" cli.py preflight -t "$t" --count 3 --timeout 2 >/dev/null 2>&1; then
    echo "   WARNING preflight did not pass for $t (continuing; high loss can trip it)" | tee -a "$SUMMARY"
  fi
done
echo

TOTAL=$("$PY" -c "
import json,re,sys
m=json.load(open(sys.argv[1]))
only=sys.argv[2]
print(len([r for r in m['runs'] if not only or re.search(only, r['name'])]))
" "$MANIFEST" "$ONLY")
if [[ "$TOTAL" -eq 0 ]]; then
  echo "error: ONLY='$ONLY' matched no scenario in the manifest" >&2
  echo "       names look like xp_test_ctrl_<regime>_<app>_<proto>[_i<ms>]_l<loss>, e.g. xp_test_ctrl_busy_grpc_h3_l1" >&2
  exit 2
fi
IDX=0
FAILED=0

while IFS=$'\t' read -r NAME TOPO XP; do
  IDX=$((IDX + 1))
  for rep in $(seq 1 "$REPEATS"); do
    OUT="$OUT_ROOT/$NAME/rep$rep"
    if [[ -f "$OUT/ctrlbench.json" ]]; then
      echo "[$IDX/$TOTAL rep$rep] $NAME - already done, skipping"
      continue
    fi
    mkdir -p "$OUT"
    echo "[$IDX/$TOTAL rep$rep] $NAME"
    START=$(date +%s)
    "$PY" cli.py exec -t "$TOPO" -x "$XP" -o "$OUT" \
        --skip-preflight --allow-custom-cmd > "$OUT/exec.log" 2>&1
    RC=$?
    ELAPSED=$(( $(date +%s) - START ))
    if [[ -f "$OUT/ctrlbench.json" ]]; then
      LINE=$("$PY" - "$OUT/ctrlbench.json" <<'PYEOF'
import json,sys
d=json.load(open(sys.argv[1]))
dl=d["downlink"]; c=d["control"]
print("  ok  delivered=%4d/%-4d  lat_p50=%8.1f ms  lat_p99=%8.1f ms  confirm_p99=%8.1f ms  req/cmd=%5.2f"
      % (dl["delivered"], dl["issued"], dl["latency"]["p50_ms"], dl["latency"]["p99_ms"],
         dl["confirm"]["p99_ms"], c["requests_per_command"]))
PYEOF
)
      echo "$LINE  (${ELAPSED}s)"
      echo -e "$NAME\trep$rep\tok\t$LINE" >> "$SUMMARY"
    else
      FAILED=$((FAILED + 1))
      echo "  FAILED rc=$RC after ${ELAPSED}s - see $OUT/exec.log"
      echo -e "$NAME\trep$rep\tFAILED rc=$RC" >> "$SUMMARY"
      tail -n 5 "$OUT/exec.log" | sed 's/^/      /'
    fi
    # Let Mininet fully release namespaces and ports between runs.
    sleep 2
  done
done < <("$PY" -c "
import json,re,sys
m=json.load(open(sys.argv[1]))
only=sys.argv[2]
for r in m['runs']:
    if only and not re.search(only, r['name']):
        continue
    print('\t'.join([r['name'], r['topo'], r['xp']]))
" "$MANIFEST" "$ONLY")

echo
echo "sweep finished: $((TOTAL * REPEATS)) runs, $FAILED failures"
echo "summary: $SUMMARY"
echo "now run: python3 $HERE/analyze_ctrl.py --results $OUT_ROOT --out <report-dir>"
[[ $FAILED -eq 0 ]]
