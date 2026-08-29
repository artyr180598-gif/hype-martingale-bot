"""
Графики спектрального анализа:
- chart_spectrum: тепловая карта «таймфреймы × группы факторов» + итоговая шкала
- chart_trend_matrix: матрица трендов по таймфреймам
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from src.analysis.spectrum import GROUP_RU, GROUP_WEIGHTS, SpectrumReport  # noqa: E402
from src.core.timeutil import tf_label  # noqa: E402

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.labelsize": 9,
        "axes.titlesize": 10,
        "xtick.labelsize": 8,
        "ytick.labelsize": 9,
        "font.family": "DejaVu Sans",
    }
)


def chart_spectrum(report: SpectrumReport, path: str | Path) -> Path:
    """Тепловая карта спектра: строки — группы факторов, столбцы — таймфреймы."""
    tf_scores = {t.timeframe: t.score for t in report.timeframes}
    tfs = [t for t in tf_scores]
    groups = [g for g in GROUP_WEIGHTS if g != "timeframes"]

    # матрица: для групп берём общий балл группы, для таймфреймов — их собственный
    rows = groups + ["timeframes"]
    matrix = np.zeros((len(rows), max(len(tfs), 1)))
    for i, g in enumerate(rows):
        val = report.group_scores.get(g, 0.0)
        if g == "timeframes":
            for j, tf in enumerate(tfs):
                matrix[i, j] = tf_scores.get(tf, 0.0)
        else:
            matrix[i, :] = val

    fig = plt.figure(figsize=(9.2, 5.6))
    gs = fig.add_gridspec(2, 1, height_ratios=[4.2, 1.0], hspace=0.42)
    ax = fig.add_subplot(gs[0])

    cmap = plt.get_cmap("RdYlGn")
    norm = matplotlib.colors.TwoSlopeNorm(vmin=-1.0, vcenter=0.0, vmax=1.0)
    im = ax.imshow(matrix, cmap=cmap, norm=norm, aspect="auto")

    ax.set_xticks(range(max(len(tfs), 1)))
    ax.set_xticklabels([tf_label(t) for t in tfs] if tfs else ["—"])
    ax.set_yticks(range(len(rows)))
    labels = [GROUP_RU.get(r, r) for r in rows]
    for i, r in enumerate(rows):
        if r == "timeframes":
            labels[i] = "Таймфреймы"
    ax.set_yticklabels(labels)

    for i in range(len(rows)):
        for j in range(matrix.shape[1]):
            v = matrix[i, j]
            ax.text(
                j, i, f"{v:+.2f}", ha="center", va="center",
                fontsize=8, color="black" if abs(v) < 0.62 else "white",
            )

    total = report.total_score
    head = "▲" if report.direction == "LONG" else ("▼" if report.direction == "SHORT" else "◆")
    ax.set_title(
        f"{head} {report.symbol} — спектр {total:+.2f} | {report.direction} | "
        f"confluence {report.confluence:.0f}/100 | цена {report.price:.8g}",
        loc="left", fontweight="bold",
    )
    fig.colorbar(im, ax=ax, fraction=0.028, pad=0.02)

    # нижняя шкала: вклад каждой группы в итог
    ax2 = fig.add_subplot(gs[1])
    contrib = {
        GROUP_RU.get(g, g): report.group_scores.get(g, 0.0) * GROUP_WEIGHTS[g] / sum(GROUP_WEIGHTS.values())
        for g in GROUP_WEIGHTS
    }
    names = list(contrib)
    vals = [contrib[n] for n in names]
    colors = ["#16a34a" if v >= 0 else "#dc2626" for v in vals]
    ax2.barh(range(len(names)), vals, color=colors, alpha=0.85, height=0.62)
    ax2.set_yticks(range(len(names)))
    ax2.set_yticklabels(names, fontsize=7)
    ax2.invert_yaxis()
    ax2.axvline(0, color="#6b7280", lw=0.8)
    ax2.grid(axis="x", alpha=0.35)
    ax2.set_title("Вклад групп в итоговый сигнал", loc="left", fontsize=9)
    ax2.tick_params(axis="x", labelsize=7)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out
