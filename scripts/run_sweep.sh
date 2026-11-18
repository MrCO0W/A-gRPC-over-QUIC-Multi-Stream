#!/usr/bin/env bash
#
# Drive the full test sweep through the GAIA workflow CLI.
#
# Every cell of the matrix is executed `repeats` times with an explicit output
# directory, so the analyser can find results without guessing at timestamps
# and a re-run of one cell never clobbers another.
#
#   sudo ./run_sweep.sh                       # use scripts/manifest.json
#   sudo MANIFEST=/path/manifest.json ./run_sweep.sh
#   sudo REPEATS=1 ./run_sweep.sh             # quick smoke pass
#
# ONLY takes an extended regular expression matched against the scenario name
# (xp_test_<regime>_<mode>_l<loss>) and restricts the sweep to matching cells.
# Combined with REPEATS it tops up a few cells without re-running the matrix,
# because a rep whose rpcbench.json already exists is skipped:
#
#   sudo ONLY='xp_test_tput_(grpcm|h3m)_l0p[12]$' REPEATS=10 ./run_sweep.sh
#     -> reps 1-3 skipped, reps 4-10 executed, 4 cells x 7 = 28 runs
#
# Mininet needs root, hence sudo. Results land in <gaia>/output/test/.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$HERE/manifest.json}"
ONLY="${ONLY:-}"

if [[ ! -f "$MANIFEST" ]]; then
  echo "error: manifest not found at $MANIFEST" >&2
  echo "       run gen_scenarios.py first" >&2
  exit 2
fi

PY="${PY:-python3}"
GAIA="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['gaia'])" "$MANIFEST")"
RPCBENCH="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['rpcbench'])" "$MANIFEST")"
REPEATS="${REPEATS:-$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['repeats'])" "$MANIFEST")}"

OUT_ROOT="${OUT_ROOT:-$GAIA/output/test}"

echo "gaia      : $GAIA"
echo "rpcbench  : $RPCBENCH"
echo "repeats   : $REPEATS"
[[ -n "$ONLY" ]] && echo "only      : $ONLY"
echo "output    : $OUT_ROOT"
echo

for f in cloudgw edgegw; do
  if [[ ! -x "$RPCBENCH/bin/$f" ]]; then
    echo "error: $RPCBENCH/bin/$f is missing - run 'make build' in rpcbench first" >&2
    exit 2
  fi
done
if [[ ! -f "$RPCBENCH/certs/ca.pem" ]]; then
  echo "error: $RPCBENCH/certs/ca.pem is missing - run 'make certs' in rpcbench first" >&2
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
  echo "       names look like xp_test_<regime>_<mode>_l<loss>, e.g. xp_test_tput_h3m_l0p1" >&2
  exit 2
fi
IDX=0
FAILED=0

while IFS=$'\t' read -r NAME TOPO XP; do
  IDX=$((IDX + 1))
  for rep in $(seq 1 "$REPEATS"); do
    OUT="$OUT_ROOT/$NAME/rep$rep"
    if [[ -f "$OUT/rpcbench.json" ]]; then
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
    if [[ -f "$OUT/rpcbench.json" ]]; then
      LINE=$("$PY" - "$OUT/rpcbench.json" <<'PYEOF'
import json,sys
d=json.load(open(sys.argv[1]))
t=d["totals"]
print("  ok  goodput=%7.2f Mbps  msg/s=%9.0f  rtt_p50=%7.2f ms  rtt_p99=%8.2f ms  stall_corr=%.2f"
      % (t["goodput_mbps"], t["messages_per_s"], d["rtt"]["p50_ms"], d["rtt"]["p99_ms"],
         d["hol"]["stall_correlation"]))
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
echo "now run: python3 $HERE/analyze.py --results $OUT_ROOT --manifest $MANIFEST --out <report-dir>"
[[ $FAILED -eq 0 ]]
