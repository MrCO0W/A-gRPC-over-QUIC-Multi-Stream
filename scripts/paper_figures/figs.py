"""Result figures for the journal manuscript (English and Korean labels).

python3 figs.py <out-dir> <en|ko>

All numbers come from the DATA block below, which was copied from the
results/summary.csv, results/stream_sweep.csv and results/ramp.csv (adds=70).
"""
import sys, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT, LANG = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({
    "font.family": "Noto Sans CJK JP" if LANG == "ko" else "DejaVu Sans",
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
    "legend.fontsize": 7.2, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e3e3e3", "grid.linewidth": 0.6,
    "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
})

# ------------------------------------------------------------------ DATA
ORDER = ["rest", "tcp1", "tcpn", "quic1", "quicn"]
NAME = {
    "en": {"rest": "REST", "tcp1": "TCP multi-stream", "tcpn": "TCP multi-connection",
           "quic1": "QUIC multi-stream", "quicn": "QUIC multi-connection"},
    "ko": {"rest": "REST", "tcp1": "TCP 다중 스트림", "tcpn": "TCP 다중 연결",
           "quic1": "QUIC 다중 스트림", "quicn": "QUIC 다중 연결"},
}[LANG]
COLOR = {"rest": "#eda100", "tcp1": "#6aa3e8", "tcpn": "#e05a2b", "quic1": "#16509b", "quicn": "#9c2f12"}
STYLE = {"rest": (0, (1, 1.2)), "tcp1": "-", "tcpn": (0, (4, 2)), "quic1": "-", "quicn": (0, (6, 2, 1.5, 2))}
MARK = {"rest": "o", "tcp1": "s", "tcpn": "D", "quic1": "^", "quicn": "v"}

LOSS4 = [0, 1, 2, 5]
TAIL = {  # 99th-percentile batch RTT, ms (8 streams)
    "rest": [43.5, 325.5, 340.0, 378.5], "tcp1": [42.0, 129.7, 164.1, 369.5],
    "tcpn": [42.5, 369.1, 373.1, 380.2], "quic1": [42.5, 87.9, 116.4, 150.0],
    "quicn": [42.9, 109.9, 110.0, 113.8]}
N = [2, 4, 8, 16]
SWEEP = {  # measurements/s per stream, single connection
    ("tcp1", 0): [486.7, 485.3, 486.0, 485.4], ("quic1", 0): [482.0, 480.7, 479.3, 479.0],
    ("tcp1", 1): [426.0, 401.8, 326.3, 254.8], ("quic1", 1): [464.2, 462.4, 462.7, 445.1],
    ("tcp1", 5): [271.7, 217.6, 183.3, 133.9], ("quic1", 5): [393.8, 400.2, 382.8, 288.4]}
ADD = {  # 99th percentile of 70 additions, ms
    "rest": [170.8, 648.8, 1418.7, 3299.2], "tcp1": [85.2, 286.3, 304.1, 703.8],
    "tcpn": [171.4, 282.0, 1178.7, 1243.6], "quic1": [86.4, 165.4, 149.3, 188.3],
    "quicn": [132.0, 296.9, 367.9, 469.6]}
PEER = {  # peer progress ratio, %
    "rest": [102.4, 102.4, 96.1, 105.9], "tcp1": [102.6, 89.5, 92.0, 84.4],
    "tcpn": [99.4, 98.7, 98.0, 102.1], "quic1": [100.7, 97.6, 100.6, 100.2],
    "quicn": [97.8, 100.6, 96.7, 99.0]}
RLOSS = [0.5, 1, 2, 5]
RATIO = {  # multi-stream / multi-connection rate, %
    "tcp": {0.5: [102.7, 97.9, 87.4, 67.4], 1: [101.3, 96.3, 77.9, 61.0],
            2: [106.1, 88.3, 72.1, 55.0], 5: [106.5, 84.4, 71.7, 52.5]},
    "quic": {0.5: [100.4, 100.2, 99.9, 98.0], 1: [100.2, 100.1, 100.1, 95.9],
             2: [99.6, 100.9, 100.1, 88.0], 5: [100.8, 101.6, 97.0, 72.9]}}
BLOSS = [0, 0.1, 0.5, 1, 2, 5]
BULK = {  # goodput, Mbit/s, 8 streams, link-saturating load
    "rest": [47.5, 42.6, 28.6, 20.8, 14.5, 7.9], "tcp1": [46.5, 10.7, 4.7, 3.1, 2.2, 1.0],
    "tcpn": [46.6, 34.6, 22.0, 16.7, 11.9, 6.6], "quic1": [44.5, 23.1, 9.9, 7.4, 5.4, 3.5],
    "quicn": [34.2, 30.9, 27.0, 24.0, 20.9, 15.2]}

T = {
    "en": dict(loss="Loss rate (%)", rtt="99th-pct batch RTT (ms)", streams="Streams per connection",
               perstream="Rate per stream (meas./s)", addt="Time to first response (ms)",
               peer="Peer progress (%)", ratio="Rate ratio (%)", gp="Goodput (Mbit/s)",
               a="(a) Flow-addition time", b="(b) Progress of existing streams",
               ta="(a) TCP", tb="(b) QUIC", loss_lbl="loss {}%", ms="multi-stream"),
    "ko": dict(loss="손실률 (%)", rtt="99백분위 왕복 시간 (ms)", streams="연결당 스트림 수",
               perstream="스트림당 전송률 (측정치/초)", addt="첫 응답까지 (ms)",
               peer="기존 스트림 진행률 (%)", ratio="전송률 비 (%)", gp="처리량 (Mbit/s)",
               a="(a) 흐름 추가 시간", b="(b) 기존 스트림 진행률",
               ta="(a) TCP", tb="(b) QUIC", loss_lbl="손실 {}%", ms="다중 스트림"),
}[LANG]


def line(ax, x, y, k, **kw):
    ax.plot(x, y, color=COLOR[k], ls=STYLE[k], marker=MARK[k], ms=3.6, lw=1.4,
            label=NAME[k], **kw)


def save(fig, name):
    fig.savefig(os.path.join(OUT, name + ".pdf"))
    fig.savefig(os.path.join(OUT, name + ".png"), dpi=220)
    plt.close(fig)


# Fig: tail latency vs loss
fig, ax = plt.subplots(figsize=(4.6, 2.5))
for k in ORDER:
    line(ax, LOSS4, TAIL[k], k)
ax.set_xticks(LOSS4); ax.set_xlabel(T["loss"]); ax.set_ylabel(T["rtt"]); ax.set_ylim(0, 420)
ax.legend(ncol=1, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0))
save(fig, "fig_tail")

# Fig: per-stream rate vs streams (loss colour, transport line style)
LC = {0: "#6250d6", 1: "#eda100", 5: "#e34948"}
fig, ax = plt.subplots(figsize=(4.6, 2.6))
for (k, l), v in SWEEP.items():
    ax.plot(N, v, color=LC[l], ls="-" if k == "quic1" else (0, (4, 2)),
            marker="^" if k == "quic1" else "s", ms=3.6, lw=1.4,
            label=f"{NAME[k]}, {T['loss_lbl'].format(l)}")
ax.set_xscale("log", base=2); ax.set_xticks(N); ax.set_xticklabels(N)
ax.set_xlabel(T["streams"]); ax.set_ylabel(T["perstream"]); ax.set_ylim(100, 520)
ax.legend(ncol=1, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0))
save(fig, "fig_sweep")

# Fig: flow addition (a) p99 time (log) (b) peer progress
fig, (a1, a2) = plt.subplots(1, 2, figsize=(6.6, 2.5))
for k in ORDER:
    line(a1, LOSS4, ADD[k], k); line(a2, LOSS4, PEER[k], k)
a1.set_yscale("log"); a1.set_ylim(60, 5000)
a1.set_xticks(LOSS4); a2.set_xticks(LOSS4)
a1.set_xlabel(T["loss"]); a2.set_xlabel(T["loss"])
a1.set_ylabel(T["addt"]); a2.set_ylabel(T["peer"]); a2.set_ylim(80, 110)
a1.set_title(T["a"], loc="left"); a2.set_title(T["b"], loc="left")
h, l = a1.get_legend_handles_labels()
fig.legend(h, l, ncol=5, frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.06))
fig.tight_layout(rect=(0, 0.08, 1, 1))
save(fig, "fig_addition")

# Fig: rate ratio, (a) TCP (b) QUIC
RC = {0.5: "#c9c2f0", 1: "#9a8de4", 2: "#6250d6", 5: "#2e1f8f"}
fig, axs = plt.subplots(1, 2, figsize=(6.6, 2.5), sharey=True)
for ax, tr, ttl in [(axs[0], "tcp", T["ta"]), (axs[1], "quic", T["tb"])]:
    for l in RLOSS:
        ax.plot(N, RATIO[tr][l], color=RC[l], marker="o", ms=3.4, lw=1.4, label=T["loss_lbl"].format(l))
    ax.axhline(100, color="#888", lw=0.8, ls=(0, (3, 2)))
    ax.set_xscale("log", base=2); ax.set_xticks(N); ax.set_xticklabels(N)
    ax.set_xlabel(T["streams"]); ax.set_title(ttl, loc="left"); ax.set_ylim(45, 112)
axs[0].set_ylabel(T["ratio"])
h, l = axs[0].get_legend_handles_labels()
fig.legend(h, l, ncol=4, frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.06))
fig.tight_layout(rect=(0, 0.08, 1, 1))
save(fig, "fig_ratio")

# Fig: bulk goodput
fig, ax = plt.subplots(figsize=(4.6, 2.5))
xs = list(range(len(BLOSS)))
for k in ORDER:
    line(ax, xs, BULK[k], k)
ax.set_xticks(xs); ax.set_xticklabels([str(v) for v in BLOSS])
ax.set_xlabel(T["loss"]); ax.set_ylabel(T["gp"]); ax.set_ylim(0, 52)
ax.legend(ncol=1, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0))
save(fig, "fig_bulk")
print("ok", OUT, LANG)
