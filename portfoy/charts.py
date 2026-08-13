"""Matplotlib grafikleri: portfoy dagilimi ve getiri katkisi."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import matplotlib

from . import config
from .analytics import PortfolioAnalysis
from .formatting import fmt_money, fmt_number, fmt_pct, fmt_points

logger = logging.getLogger(__name__)

# Dilim ici etiketin okunabildigi en kucuk pay (yuzde). Bunun altindaki
# dilimler etiketsiz birakilmaz; disariya kilavuz cizgiyle yazilir.
MIN_LABEL_SHARE = 4.0

_OUTER_LABEL_X = 1.28  # dis etiket metninin yatay konumu (eksen birimi)
_LEADER_START_R = 1.02  # kilavuz cizginin dilim kenarindan ciktigi yaricap
_OUTER_MIN_GAP = 0.26  # iki dis etiket arasindaki en az dikey mesafe
_OUTER_LIMIT_Y = 1.15  # dis etiketlerin tasmamasi gereken dikey sinir


def _prepare_backend(show: bool) -> None:
    """Ekranda gostermeyecegiz ise basliksiz (headless) arka uc kullan."""
    if not show:
        matplotlib.use("Agg", force=True)


def _style(theme: str) -> dict:
    return config.PALETTES[theme]


def _base_figure(palette: dict, figsize=(8.0, 6.4)):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor(palette["surface"])
    ax.set_facecolor(palette["surface"])
    return fig, ax


def _finish(fig, path: Path, palette: dict, show: bool) -> Path:
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    # Ust seride baslik + alt basliga yer birak, cakismayi onle.
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.90))
    fig.savefig(path, dpi=200, facecolor=palette["surface"], bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    return path


def _title(fig, title: str, subtitle: str, palette: dict) -> None:
    """Baslik ve alt basligi figur koordinatlarinda yerlestirir."""
    fig.text(
        0.5, 0.975, title,
        ha="center", va="top",
        fontsize=15, fontweight="bold", color=palette["text_primary"],
    )
    fig.text(
        0.5, 0.925, subtitle,
        ha="center", va="top",
        fontsize=10, color=palette["text_secondary"],
    )


def _pie_labels(ax, wedges, labels, shares, palette) -> None:
    """Her dilimi etiketler: genisleri icine, inceleri disina.

    Ince dilimlerde ici etiket okunmadigi icin eskiden tamamen atiliyordu; bu
    da kucuk agirlikli fonlarin grafikte adsiz kalmasina yol aciyordu. Artik
    dilimin disina kilavuz cizgiyle yaziliyorlar.
    """
    outer: list[tuple[float, float, str]] = []  # (aci, kosinus, etiket)

    for wedge, label, share in zip(wedges, labels, shares):
        angle = math.radians((wedge.theta2 + wedge.theta1) / 2.0)
        if share >= MIN_LABEL_SHARE:
            ax.text(
                0.62 * math.cos(angle),
                0.62 * math.sin(angle),
                label,
                ha="center",
                va="center",
                fontsize=11,
                fontweight="bold",
                color="#ffffff",
            )
        else:
            outer.append((angle, math.cos(angle), label))

    if not outer:
        # Dis etiket yoksa pasta cerceveyi doldursun.
        ax.set_xlim(-1.05, 1.05)
        ax.set_ylim(-1.05, 1.05)
        return

    # Sag ve sol yariyi ayri ele al: ince dilimler cogunlukla yan yana dustugu
    # icin etiketleri dikeyde ayristirmak sart.
    used_sides: set[int] = set()
    for side in (1, -1):
        items = sorted(
            (item for item in outer if (item[1] >= 0) == (side > 0)),
            key=lambda item: math.sin(item[0]),
            reverse=True,
        )
        if not items:
            continue
        used_sides.add(side)

        positions: list[float] = []
        for angle, _cos, _label in items:
            y = math.sin(angle)
            if positions and y > positions[-1] - _OUTER_MIN_GAP:
                y = positions[-1] - _OUTER_MIN_GAP
            positions.append(y)

        # Asagi tasan grubu topluca yukari kaydir; en alttaki etiket kirpilmasin.
        overflow = -_OUTER_LIMIT_Y - min(positions)
        if overflow > 0:
            positions = [y + overflow for y in positions]

        for (angle, _cos, label), y in zip(items, positions):
            ax.annotate(
                label,
                xy=(
                    _LEADER_START_R * math.cos(angle),
                    _LEADER_START_R * math.sin(angle),
                ),
                xytext=(side * _OUTER_LABEL_X, y),
                ha="left" if side > 0 else "right",
                va="center",
                fontsize=10,
                fontweight="bold",
                color=palette["text_primary"],
                arrowprops={
                    "arrowstyle": "-",
                    "color": palette["axis"],
                    "linewidth": 1.0,
                    "shrinkA": 0,
                    "shrinkB": 3,
                },
            )

    # Dis etiketlere yer ac - ama sadece etiket dusen tarafa. Iki yani birden
    # genisletmek, etiketsiz tarafta bos serit birakip pastayi kaydiriyordu.
    ax.set_xlim(
        -1.85 if -1 in used_sides else -1.05,
        1.85 if 1 in used_sides else 1.05,
    )
    ax.set_ylim(-1.25, 1.25)


def _legend(ax, handles, labels, palette) -> None:
    """Efsane her zaman var (>=2 seri); tek satirda ve seri sirasinda."""
    ax.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=len(labels) if len(labels) <= 4 else 3,
        frameon=False,
        fontsize=9.5,
        labelcolor=palette["text_secondary"],
        handlelength=1.1,
        columnspacing=1.6,
    )


# --------------------------------------------------------------------------
# 1) Portfoy dagilimi
# --------------------------------------------------------------------------
def allocation_pie(
    analysis: PortfolioAnalysis,
    output_dir: Path,
    theme: str = "light",
    show: bool = False,
) -> Path | None:
    """Hangi fon portfoyun yuzde kacini olusturuyor."""
    rows = [row for row in analysis.rows if row.value > 0]
    if not rows:
        logger.warning("Dagilim grafigi icin veri yok.")
        return None

    _prepare_backend(show)
    palette = _style(theme)
    fig, ax = _base_figure(palette)

    values = [row.value for row in rows]
    colors = [config.series_color(i, theme) for i in range(len(rows))]

    wedges, _ = ax.pie(
        values,
        colors=colors,
        startangle=90,
        counterclock=False,
        # Dilimler arasinda 2px yuzey boslugu
        wedgeprops={"edgecolor": palette["surface"], "linewidth": 2},
    )

    # Dogrudan etiket: kod + agirlik. Dilim uzerindeki metin beyaz kalir;
    # eksen disindaki tum metin ink token'i tasir, seri rengini degil.
    _pie_labels(
        ax,
        wedges,
        [f"{row.code}\n%{fmt_number(row.weight_pct, 1)}" for row in rows],
        [row.weight_pct for row in rows],
        palette,
    )

    _legend(ax, wedges, [f"{row.code} — {fmt_money(row.value)}" for row in rows], palette)
    ax.set_aspect("equal", adjustable="box")
    _title(
        fig,
        "Portföy Dağılımı",
        f"Toplam {fmt_money(analysis.total_value)} · {analysis.as_of:%d.%m.%Y}",
        palette,
    )

    return _finish(fig, output_dir / "portfoy_dagilimi.png", palette, show)


# --------------------------------------------------------------------------
# 2) Getiri katkisi
# --------------------------------------------------------------------------
def contribution_chart(
    analysis: PortfolioAnalysis,
    output_dir: Path,
    period: str = "aylik",
    theme: str = "light",
    show: bool = False,
) -> Path | None:
    """Toplam getiriye hangi fon ne kadar katki sagladi.

    Katkilarin tamami pozitifse pasta grafik cizilir. Negatif katki varsa pasta
    matematiksel olarak anlamsiz olacagindan (negatif dilim cizilemez) otomatik
    olarak sifir eksenli yatay bar grafige gecilir.
    """
    label = config.PERIOD_LABELS.get(period, period)
    rows = [row for row in analysis.rows if row.contributions.get(period) is not None]
    if not rows:
        logger.warning("%s katki grafigi icin veri yok.", label)
        return None

    contributions = [row.contributions[period] for row in rows]
    has_negative = any(value < 0 for value in contributions)
    path = output_dir / f"getiri_katkisi_{period}.png"

    # Renk fonu (entity) takip eder: dagilim grafigindeki slot burada da gecerli.
    slots = {row.code: index for index, row in enumerate(analysis.rows)}

    if has_negative:
        return _contribution_bars(
            rows, contributions, slots, analysis, path, label, theme, show, period
        )
    return _contribution_pie(
        rows, contributions, slots, analysis, path, label, theme, show, period
    )


def _contribution_pie(
    rows, contributions, slots, analysis, path, label, theme, show, period_key
) -> Path | None:
    _prepare_backend(show)
    palette = _style(theme)
    fig, ax = _base_figure(palette)

    total = sum(contributions)
    if total <= 0:
        logger.warning("%s toplam katki sifir; grafik atlandi.", label)
        import matplotlib.pyplot as plt

        plt.close(fig)
        return None

    colors = [config.series_color(slots[row.code], theme) for row in rows]
    wedges, _ = ax.pie(
        contributions,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"edgecolor": palette["surface"], "linewidth": 2},
    )

    shares = [value / total * 100.0 for value in contributions]
    _pie_labels(
        ax,
        wedges,
        [
            f"{row.code}{'*' if row.is_partial(period_key) else ''}\n"
            f"%{fmt_number(share, 1)}"
            for row, share in zip(rows, shares)
        ],
        shares,
        palette,
    )

    _legend(
        ax,
        wedges,
        [
            # Yildiz: fon periyodun tamaminda portfoyde degildi, katkisi
            # alis tarihinden itibaren hesaplandi.
            f"{row.code}{'*' if row.is_partial(period_key) else ''} — "
            f"{fmt_points(value)} yp"
            for row, value in zip(rows, contributions)
        ],
        palette,
    )
    ax.set_aspect("equal", adjustable="box")
    _title(
        fig,
        f"{label} Getiri Katkısı",
        f"Ağırlıklı {label.lower()} getiri: {fmt_pct(total)} · {analysis.as_of:%d.%m.%Y}"
        + (" · * alıştan itibaren" if analysis.partial_rows(period_key) else ""),
        palette,
    )
    return _finish(fig, path, palette, show)


def _contribution_bars(
    rows, contributions, slots, analysis, path, label, theme, show, period_key
) -> Path:
    """Negatif katki varsa: sifir eksenli yatay bar."""
    _prepare_backend(show)
    palette = _style(theme)
    height = max(3.2, 0.72 * len(rows) + 2.2)
    fig, ax = _base_figure(palette, figsize=(8.0, height))

    order = sorted(range(len(rows)), key=lambda i: contributions[i])
    # Yildiz: fon periyodun tamaminda portfoyde degildi.
    codes = [
        rows[i].code + ("*" if rows[i].is_partial(period_key) else "") for i in order
    ]
    values = [contributions[i] for i in order]
    # Isareti sifir ekseninin hangi tarafinda oldugu tasir; renk fonu tanitir.
    colors = [config.series_color(slots[rows[i].code], theme) for i in order]

    positions = range(len(values))
    ax.barh(list(positions), values, height=0.58, color=colors)
    ax.axvline(0, color=palette["axis"], linewidth=1.2)

    ax.set_yticks(list(positions))
    ax.set_yticklabels(codes, color=palette["text_secondary"], fontsize=10)
    ax.tick_params(axis="x", colors=palette["muted"], labelsize=9)
    ax.set_xlabel("Katkı (yüzde puan)", color=palette["text_secondary"], fontsize=9.5)

    # Eksen etiketleri de Turkce ondalik ayraci kullansin.
    from matplotlib.ticker import FuncFormatter

    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt_number(v, 1)))

    for spine_name, spine in ax.spines.items():
        spine.set_visible(spine_name == "bottom")
        spine.set_color(palette["axis"])
    ax.xaxis.grid(True, color=palette["grid"], linewidth=0.8)
    ax.set_axisbelow(True)

    span = max(abs(min(values)), abs(max(values)), 0.01)
    ax.set_xlim(min(0, min(values)) - span * 0.28, max(0, max(values)) + span * 0.28)

    for index, value in zip(positions, values):
        offset = span * 0.04
        ax.text(
            value + (offset if value >= 0 else -offset),
            index,
            fmt_points(value),
            va="center",
            ha="left" if value >= 0 else "right",
            fontsize=9.5,
            color=palette["text_primary"],
        )

    total = sum(contributions)
    _title(
        fig,
        f"{label} Getiri Katkısı",
        f"Ağırlıklı {label.lower()} getiri: {fmt_pct(total)} · {analysis.as_of:%d.%m.%Y}"
        + (" · * alıştan itibaren" if analysis.partial_rows(period_key) else ""),
        palette,
    )
    return _finish(fig, path, palette, show)


# --------------------------------------------------------------------------
def render_all(
    analysis: PortfolioAnalysis,
    output_dir: Path | None = None,
    theme: str = "light",
    show: bool = False,
    period: str = "aylik",
) -> list[Path]:
    """Iki grafigi de uretir; biri patlarsa digeri yine de uretilir."""
    output_dir = output_dir or config.OUTPUT_DIR
    created: list[Path] = []
    for builder in (
        lambda: allocation_pie(analysis, output_dir, theme, show),
        lambda: contribution_chart(analysis, output_dir, period, theme, show),
    ):
        try:
            result = builder()
        except Exception as exc:  # grafik hatasi raporu durdurmasin
            logger.warning("Grafik olusturulamadi: %s", exc)
            continue
        if result is not None:
            created.append(result)
    return created
