"""Loss-scope illustration: how far one loss spreads in four layouts.
python3 fig_scope.py <out-dir> <en|ko>"""
import sys, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

OUT, LANG = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.family": "Noto Sans CJK JP" if LANG == "ko" else "DejaVu Sans",
                     "font.size": 7.5, "pdf.fonttype": 42})
L = {
    "en": dict(a="(a) TCP, 1 connection × N streams", b="(b) QUIC, 1 connection × N streams",
               c="(c) TCP, N connections × 1 stream", d="(d) QUIC, N connections × 1 stream",
               arr="retransmission arrives", leg_ok="delivered", leg_hold="held until retransmission",
               leg_loss="lost packet", leg_unit="unit added at run time",
               all_="one loss stalls all N flows", one="only the flow with the loss stalls",
               unit_s="unit added = stream", unit_c="unit added = connection"),
    "ko": dict(a="(a) TCP, 연결 1개 × 스트림 N개", b="(b) QUIC, 연결 1개 × 스트림 N개",
               c="(c) TCP, 연결 N개 × 스트림 1개", d="(d) QUIC, 연결 N개 × 스트림 1개",
               arr="재전송 도착", leg_ok="전달됨", leg_hold="재전송 완료까지 보류",
               leg_loss="손실", leg_unit="운용 중 추가되는 단위",
               all_="손실 1회가 N개 흐름 전부를 멈춘다", one="손실이 발생한 흐름만 멈춘다",
               unit_s="추가 단위 = 스트림", unit_c="추가 단위 = 연결"),
}[LANG]
COL = ["#2f6fd0", "#e05a2b", "#2e9d55", "#e0b100"]
HOLD_BG = "#fbe3e0"


def panel(ax, title, shared, foot, unit, stall_all=False):
    ax.set_xlim(-0.15, 12.15); ax.set_ylim(-2.0, 5.2); ax.axis("off")
    ax.text(0, 5.0, title, fontsize=7.8, fontweight="bold", va="bottom")
    ax.add_patch(FancyBboxPatch((0, 0), 1.3, 4.4, boxstyle="round,pad=0.05", fc="white", ec="#999", lw=0.8))
    ax.text(0.65, 2.2, "Edge", ha="center", va="center", fontsize=6.5)
    ax.add_patch(FancyBboxPatch((10.7, 0), 1.3, 4.4, boxstyle="round,pad=0.05", fc="white", ec="#999", lw=0.8))
    ax.text(11.35, 2.2, "Cloud", ha="center", va="center", fontsize=6.5)
    if shared:
        ax.add_patch(FancyBboxPatch((1.6, 0.05), 8.8, 4.3, boxstyle="round,pad=0.05", fc="#f4f4f4", ec="#aaa", lw=0.8))
    else:
        for r in range(4):
            ax.add_patch(FancyBboxPatch((1.6, 0.18 + r * 1.07), 8.8, 0.85, boxstyle="round,pad=0.03", fc="#f4f4f4", ec="#aaa", lw=0.8))
    xs = [2.0, 2.6, 3.4, 4.0, 4.6, 5.2, 6.4, 7.0, 7.6, 8.6, 9.2, 9.8]
    loss_x, arr_x = 3.4, 6.0
    lossrow = 2
    held_rows = range(4) if stall_all else [lossrow]
    for r in held_rows:
        ax.add_patch(Rectangle((3.15, 0.35 + (3 - r) * 1.07), 2.6, 0.55, fc=HOLD_BG, ec="none"))
    for r in range(4):
        y = 0.62 + (3 - r) * 1.07
        ax.plot([1.7, 10.3], [y, y], color=COL[r], lw=0.6, alpha=0.5)
        for x in xs:
            if r == lossrow and x == loss_x:
                ax.plot(x, y, marker="x", color="#d23", ms=7, mew=1.8)
                continue
            held = (r in held_rows) and loss_x < x < arr_x
            ax.add_patch(Rectangle((x - 0.18, y - 0.18), 0.36, 0.36,
                                   fc="white" if held else COL[r], ec=COL[r] if held else "none", lw=0.8))
    ax.plot([arr_x, arr_x], [0.05, 4.4], color="#666", lw=0.8, ls=(0, (2, 2)))
    ax.text(arr_x, 4.5, L["arr"], ha="center", fontsize=6)
    ax.plot([1.6, 10.4], [-0.25, -0.25], color="#999", lw=0.8, ls=(0, (4, 3)))
    ax.text(1.6, -0.55, foot, fontsize=6.6, va="top", color="#222")
    ax.text(1.6, -1.25, unit, fontsize=6.3, va="top", color="#555")


fig, axs = plt.subplots(2, 2, figsize=(6.8, 3.6))
panel(axs[0, 0], L["a"], True, L["all_"], L["unit_s"], stall_all=True)
panel(axs[0, 1], L["b"], True, L["one"], L["unit_s"])
panel(axs[1, 0], L["c"], False, L["one"], L["unit_c"])
panel(axs[1, 1], L["d"], False, L["one"], L["unit_c"])
fig.subplots_adjust(wspace=0.06, hspace=0.25, bottom=0.12)
h = [Rectangle((0, 0), 1, 1, fc="#2f6fd0"), Rectangle((0, 0), 1, 1, fc=HOLD_BG, ec="#2f6fd0"),
     plt.Line2D([], [], marker="x", color="#d23", ls="none", mew=1.8),
     plt.Line2D([], [], color="#999", ls=(0, (4, 3)))]
fig.legend(h, [L["leg_ok"], L["leg_hold"], L["leg_loss"], L["leg_unit"]], ncol=4, frameon=False,
           loc="lower center", bbox_to_anchor=(0.5, 0.0), fontsize=6.8)
fig.savefig(os.path.join(OUT, "fig_scope.pdf"), bbox_inches="tight", pad_inches=0.03)
fig.savefig(os.path.join(OUT, "fig_scope.png"), dpi=220, bbox_inches="tight", pad_inches=0.03)
print("ok")
