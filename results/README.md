# Results

Measured data behind every table and figure of the paper: 176 conditions, each run
10 times (1,760 runs). The aggregated tables are produced from `raw/` by
`scripts/analyze.py`, and rebuilding them reproduces these files exactly:

```bash
cd scripts
python3 analyze.py --results ../results/raw --out /tmp/report
```

## Aggregated tables

| File | Content |
|---|---|
| `summary.csv` | one row per condition (regime, configuration, loss, streams, ramp): medians over 10 repetitions |
| `runs.csv` | one row per run (1,760 rows) |
| `streams.csv` | per-stream statistics of every run |
| `stream_sweep.csv` | rate with one connection vs. one connection per stream, by stream count and loss (Figs. 7 and 9) |
| `ramp.csv` | flow-addition time (p50, p99, max over 70 additions) and peer progress (Fig. 8) |
| `report.md` | text report written by the analyser |
| `sweep.log` | log of the last `run_sweep.sh` invocation (a top-up of 308 runs; runs already complete were skipped). `runs.csv` lists all 1,760 runs |

Column meanings: `messages_per_s` measurement delivery rate; `rtt_p99_ms` 99th-percentile
batch round-trip time; `goodput_mbps` application goodput; `bytes_per_msg` bytes on the
wire per measurement; `mean_stalled_streams` average number of streams stalled together
in 100 ms windows with at least one stall; `ratio_pct` rate of the multi-stream layout
divided by that of the multi-connection layout on the same transport (not a delivery
success ratio). Loss rates are per direction; the round-trip loss is 1 − (1 − p)².

## Raw results

```text
raw/
└── xp_test_<regime>_<mode>[_n<streams>|_ramp]_l<loss>/
    └── rep1 … rep10/
        ├── rpcbench.json   full measurement record written by edgegw (input of analyze.py)
        ├── workloads.json  exact server and client commands of the run
        └── exec.log        GAIA execution log, including the tc/netem configuration applied
```

- `regime`: `hol` = telemetry load, `tput` = bulk load
- `mode`: `rest`, `grpc` (TCP multi-stream), `grpcm` (TCP multi-connection),
  `h3` (QUIC multi-stream), `h3m` (QUIC multi-connection), `qtun` (control arm)
- `n<streams>`: stream-count sweep (2, 4, 16); conditions without it use 8 streams
- `ramp`: flow-addition runs
- `l<loss>`: loss rate per direction in %, `p` for the decimal point (`l0p5` = 0.5 %)

The working name `ICACT2027` in paths, scenario names and schema strings was replaced
by `test` to match the repository; no measured value was changed.
