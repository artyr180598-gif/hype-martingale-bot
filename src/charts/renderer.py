"""
Рендер графиков (matplotlib, светлая тема для Telegram).

- chart_analysis: свечи + EMA + SuperTrend + уровни входа/стопа/целей + RSI + объём
- chart_scan_overview: панель найденных монет (диаграмма)
"""

from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from src.analysis.engine import AnalysisResult
from src.core.timeutil import tf_label
from src.data.indicators import compute_all

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "#fafbfc",
        "axes.edgecolor": "#d0d4da",
        "axes.grid": True,
        "grid.color": "#e8eaee",
        "grid.linewidth": 0.7,
        "axes.labelsize": 9,
        "axes.titlesize": 10,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "font.family": "DejaVu Sans",
    }
)

C_UP = "#16a34a"
C_DOWN = "#dc2626"
C_BLUE = "#2563eb"
C_ORANGE = "#d97706"
C_PURPLE = "#7c3aed"
C_GRAY = "#6b7280"


def _price_fmt(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.5f}"
    return f"{price:.8f}"


def chart_analysis(
    df: pd.DataFrame,
    result: AnalysisResult | None = None,
    path: str | Path | None = None,
    width: float = 10.5,
    height: float = 7.6,
) -> Path:
    """Основной график анализа: цена, уровни, RSI, объём."""
    df = df.tail(150).reset_index(drop=True)
    x = np.arange(len(df))

    fig = plt.figure(figsize=(width, height))
    gs = fig.add_gridspec(3, 1, height_ratios=[3.1, 1.0, 0.9], hspace=0.12)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)

    title = result.symbol if result else "Анализ"
    if result:
        arrow = "▲" if result.direction == "LONG" else ("▼" if result.direction == "SHORT" else "◆")
        title = f"{arrow} {result.symbol} — {result.direction} | {result.score:.0f}/100 ({result.tier})"

    # Свечи
    for i in range(len(df)):
        o, c = df["open"].iloc[i], df["close"].iloc[i]
        color = C_UP if c >= o else C_DOWN
        ax1.plot([x[i], x[i]], [df["low"].iloc[i], df["high"].iloc[i]], color=color, lw=0.8, zorder=2)
        body_lo, body_hi = min(o, c), max(o, c)
        ax1.add_patch(Rectangle((x[i] - 0.32, body_lo), 0.64, max(body_hi - body_lo, 1e-12), facecolor=color, edgecolor=color, zorder=3))

    close = df["close"]
    if "ema_20" not in df.columns or df["ema_20"].isna().all():
        df = compute_all(df)
    ax1.plot(x, df["ema_20"], color=C_BLUE, lw=1.2, label="EMA 20")
    ax1.plot(x, df["ema_50"], color=C_PURPLE, lw=1.2, label="EMA 50")
    if "st_trend" in df.columns:
        st = df["st_trend"].ffill()
        valid = st.notna()
        ax1.plot(x[valid], st[valid], color=C_ORANGE, lw=1.4, label="SuperTrend")

    # Уровни плана
    if result and result.plan:
        p = result.plan
        zone_color = C_UP if p.direction == "LONG" else C_DOWN
        ax1.axhspan(p.entry_zone[0], p.entry_zone[1], color=zone_color, alpha=0.10, label="Зона входа")
        ax1.axhline(p.entry_zone[0], color=zone_color, lw=1.0, ls="--", alpha=0.8)
        ax1.axhline(p.entry_zone[1], color=zone_color, lw=1.0, ls="--", alpha=0.8)
        ax1.axhline(p.stop_loss, color=C_DOWN if p.direction == "LONG" else C_UP, lw=1.1, ls=":", alpha=0.9)
        ax1.annotate(
            f"Стоп {_price_fmt(p.stop_loss)}",
            xy=(x[-1], p.stop_loss), xytext=(x[-1] + 2, p.stop_loss),
            fontsize=7.5, color=C_DOWN if p.direction == "LONG" else C_UP, va="center",
        )
        for i, t in enumerate(p.targets[:3]):
            ax1.axhline(t, color=C_UP if p.direction == "LONG" else C_DOWN, lw=1.0, ls="--", alpha=0.7)
            ax1.annotate(
                f"Ц{i+1} {_price_fmt(t)}", xy=(x[-1], t), xytext=(x[-1] + 2, t),
                fontsize=7.5, color=C_UP if p.direction == "LONG" else C_DOWN, va="center",
            )

    # Support / resistance
    if result and result.support:
        ax1.axhline(result.support, color=C_GRAY, lw=0.9, ls="-", alpha=0.6)
    if result and result.resistance:
        ax1.axhline(result.resistance, color=C_GRAY, lw=0.9, ls="-", alpha=0.6)

    ax1.set_title(title, loc="left", fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7, ncol=4, framealpha=0.9)
    ax1.set_ylabel("Цена")

    # RSI
    if "rsi_14" in df.columns:
        rsi = df["rsi_14"]
        ax2.plot(x, rsi, color=C_PURPLE, lw=1.2)
        ax2.axhline(70, color=C_DOWN, lw=0.8, ls="--", alpha=0.6)
        ax2.axhline(30, color=C_UP, lw=0.8, ls="--", alpha=0.6)
        ax2.fill_between(x, 30, 70, color=C_PURPLE, alpha=0.04)
        ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI")

    # Объём
    colors = [C_UP if df["close"].iloc[i] >= df["open"].iloc[i] else C_DOWN for i in range(len(df))]
    ax3.bar(x, df["volume"], color=colors, width=0.7, alpha=0.8)
    ax3.set_ylabel("Объём")

    # Подписи времени
    if "ts" in df.columns:
        times = pd.to_datetime(df["ts"], unit="ms", utc=True)
        every = max(1, len(df) // 6)
        ticks = list(range(0, len(df), every))
        ax3.set_xticks(ticks)
        ax3.set_xticklabels([times.iloc[i].strftime("%d.%m %H:%M") for i in ticks], rotation=0)
        ax3.set_xlim(-0.5, len(df) - 0.5)

    out = Path(path) if path else Path("data/charts") / f"{result.symbol if result else 'analysis'}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def chart_gem_overview(gems: list[dict], path: str | Path) -> Path:
    """Панель найденных монет: оценка и движение за 24ч."""
    if not gems:
        gems = [{"symbol": "—", "score": 0, "price_24h_pct": 0, "direction": "WAIT"}]
    gems = gems[:15]
    symbols = [g["symbol"].replace("USDT", "") for g in gems]
    scores = [g["score"] for g in gems]
    pcts = [g.get("price_24h_pct", 0.0) for g in gems]

    fig, ax = plt.subplots(figsize=(10, 5.2))
    y = np.arange(len(symbols))[::-1]
    colors = [C_UP if p >= 0 else C_DOWN for p in pcts]
    ax.barh(y, scores, color=colors, alpha=0.85, height=0.55)
    for yi, s, p in zip(y, scores, pcts):
        ax.text(s + 1, yi, f"{s:.0f}  ({p:+.1f}%)", va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(symbols, fontsize=9)
    ax.set_xlim(0, 105)
    ax.set_title("Найденные монеты — рейтинг и движение за 24ч", loc="left", fontweight="bold")
    ax.grid(axis="x", alpha=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def chart_to_buffer(df: pd.DataFrame, result: AnalysisResult | None = None) -> io.BytesIO:
    """График в буфер (для отправки в Telegram)."""
    path = chart_analysis(df, result, path=None)  # type: ignore[arg-type]
    buf = io.BytesIO(path.read_bytes())
    path.unlink(missing_ok=True)
    return buf
