"""Matplotlib grafikleri: dagilim, getiri katkisi (selale) ve portfoy degisimi."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import matplotlib

from . import config
from .analytics import PortfolioAnalysis
from .formatting import (
    fmt_money,
    fmt_money_change,
    fmt_number,
    fmt_pct,
    fmt_points,
)

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
# 2) Getiri katkisi (selale / waterfall)
# --------------------------------------------------------------------------
def contribution_chart(
    analysis: PortfolioAnalysis,
    output_dir: Path,
    period: str = "aylik",
    theme: str = "light",
    show: bool = False,
) -> Path | None:
    """Toplam getiriye hangi fon ne kadar katki sagladi - selale grafik.

    Eskiden pasta ciziliyordu; pasta yalnizca butun katkilar pozitifken
    anlamliydi ve negatif ciktiginda bambaska bir grafige (yatay bar) atliyordu.
    Ayni raporun iki farkli sekle girmesi takibi zorlastiriyordu.

    Selale her iki durumu da tek bicimde anlatir: sifirdan baslar, her fon
    kendinden onceki birikimin ustune basamak ekler (veya dusurur) ve en sagdaki
    TOPLAM cubugu portfoyun agirlikli getirisine iner. Boylece "kim yukari
    itti, kim asagi cekti, sonuc ne oldu" tek bakista okunur.
    """
    label = config.PERIOD_LABELS.get(period, period)
    # `all_rows`: pencere icinde KAPANAN fon da bir basamak olmali. Yoksa
    # basamaklarin toplami en sagdaki TOPLAM cubuguna inmez ve grafik, kendi
    # altyazisiyla celisir.
    rows = [row for row in analysis.all_rows if row.contributions.get(period) is not None]
    if not rows:
        logger.warning("%s katki grafigi icin veri yok.", label)
        return None

    contributions = [row.contributions[period] for row in rows]
    path = output_dir / f"getiri_katkisi_{period}.png"
    return _contribution_waterfall(
        rows, contributions, analysis, path, label, theme, show, period
    )


def _contribution_waterfall(
    rows, contributions, analysis, path, label, theme, show, period_key
) -> Path:
    _prepare_backend(show)
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter

    palette = _style(theme)

    # Buyukten kucuge: once yukari iten fonlar, sonra asagi cekenler. Basamaklar
    # bu sirada tek bir tepe cizer, zikzak yapmaz.
    order = sorted(range(len(rows)), key=lambda i: contributions[i], reverse=True)
    # Yildiz: fon periyodun tamaminda portfoyde degildi.
    codes = [
        rows[i].code + ("*" if rows[i].is_partial(period_key) else "") for i in order
    ]
    values = [contributions[i] for i in order]
    total = sum(values)

    count = len(values) + 1  # +1: TOPLAM cubugu
    fig, ax = _base_figure(palette, figsize=(max(7.2, 1.15 * count + 2.0), 6.0))

    # Her cubuk, kendinden onceki birikimin uzerinde durur; TOPLAM sifirdan.
    bottoms: list[float] = []
    tops: list[float] = []
    cursor = 0.0
    for value in values:
        bottoms.append(min(cursor, cursor + value))
        tops.append(max(cursor, cursor + value))
        cursor += value
    bottoms.append(min(0.0, total))
    tops.append(max(0.0, total))

    # Renk isareti tasir, fonu degil: mavi = artiya katki, kirmizi = eksiye.
    # (Yesil/kirmizi ayrimi renk korlugunde kayboluyor - bkz. config.PALETTES.)
    colors = [palette["up"] if value >= 0 else palette["down"] for value in values]
    colors.append(palette["neutral_bar"])

    positions = list(range(count))
    heights = [top - bottom for bottom, top in zip(bottoms, tops)]

    span = max(max(tops) - min(bottoms), abs(total), 0.01)
    # Sifira cok yakin katkilar hic cizilmesin istemiyoruz: goze carpmayacak
    # kadar ince bir sirit birakiyoruz ki fon grafikte yok sayilmasin.
    floor = span * 0.004
    heights = [max(height, floor) for height in heights]

    bar_width = 0.62
    ax.bar(
        positions, heights, bottom=bottoms, width=bar_width, color=colors,
        # 2px yuzey boslugu: bitisik cubuklar birbirine yapismasin.
        edgecolor=palette["surface"], linewidth=2,
    )

    # Basamaklari birlestiren kilavuz cizgiler: birikimin nereden devam ettigi.
    cursor = 0.0
    for index, value in enumerate(values):
        cursor += value
        ax.plot(
            [index + bar_width / 2, index + 1 - bar_width / 2],
            [cursor, cursor],
            color=palette["axis"], linewidth=1.0, linestyle=(0, (3, 2)), zorder=1,
        )

    ax.axhline(0, color=palette["axis"], linewidth=1.2, zorder=2)

    # Dogrudan etiket: her basamagin degeri cubugun disinda, isaretli.
    offset = span * 0.035
    for index, (value, bottom, top) in enumerate(
        zip(values + [total], bottoms, tops)
    ):
        above = value >= 0
        ax.text(
            index,
            (top + offset) if above else (bottom - offset),
            fmt_points(value),
            ha="center",
            va="bottom" if above else "top",
            fontsize=10,
            fontweight="bold" if index == count - 1 else "normal",
            color=palette["text_primary"],
        )

    ax.set_xticks(positions)
    ax.set_xticklabels(
        codes + ["TOPLAM"], color=palette["text_secondary"], fontsize=10.5
    )
    ax.get_xticklabels()[-1].set_fontweight("bold")
    ax.get_xticklabels()[-1].set_color(palette["text_primary"])
    ax.tick_params(axis="both", colors=palette["muted"], labelsize=9, length=0)
    ax.set_ylabel("Katkı (yüzde puan)", color=palette["text_secondary"], fontsize=9.5)
    # Eksen etiketleri de Turkce ondalik ayraci kullansin.
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt_number(v, 1)))

    for spine_name, spine in ax.spines.items():
        spine.set_visible(spine_name == "bottom")
        spine.set_color(palette["axis"])
    ax.yaxis.grid(True, color=palette["grid"], linewidth=0.8)
    ax.set_axisbelow(True)

    # Bosluk sadece etiket olan tarafa: hepsi pozitifken alta pay ayirmak
    # cubuklari yukari sikistirip grafigin yarisini bos birakiyordu.
    pad = span * 0.20
    low, high = min(bottoms), max(tops)
    ax.set_xlim(-0.7, count - 0.3)
    ax.set_ylim(
        (low - pad) if low < 0 else 0.0,
        (high + pad) if high > 0 else 0.0,
    )

    # Efsane: renk yalnizca isareti anlatiyor, seri kimligi degil.
    entries = []
    if any(value >= 0 for value in values):
        entries.append((palette["up"], "Artı katkı"))
    if any(value < 0 for value in values):
        entries.append((palette["down"], "Eksi katkı"))
    entries.append((palette["neutral_bar"], "Portföy toplamı"))
    _legend(
        ax,
        [Patch(facecolor=color) for color, _text in entries],
        [text for _color, text in entries],
        palette,
    )

    _title(
        fig,
        f"{label} Getiri Katkısı",
        f"{analysis.window_label(period_key)} · ağırlıklı getiri {fmt_pct(total)}"
        + (" · * alıştan itibaren" if analysis.partial_rows(period_key) else ""),
        palette,
    )
    return _finish(fig, path, palette, show)


# --------------------------------------------------------------------------
# 3) Portfoy degisimi (yuzde + TL)
# --------------------------------------------------------------------------
def portfolio_change_chart(
    analysis: PortfolioAnalysis,
    output_dir: Path,
    periods: tuple[str, ...] = ("haftalik", "aylik"),
    theme: str = "light",
    show: bool = False,
) -> Path | None:
    """Portfoyun periyot bazli degisimi: yuzde ve TL yan yana.

    Yuzde ile TL farkli olceklerde; ikisini tek eksene koymak (cift eksen)
    grafigi yalan soyletir. Bunun yerine her periyot bir kart: buyuk rakam
    yuzde, altinda TL karsiligi, en altta yuzdeyi kartlar arasinda ORTAK
    olcekte gosteren bir sirit. Boylece hem rakamlar hem oran karsilastirilir.
    """
    entries = [
        (
            config.PERIOD_LABELS.get(period, period),
            analysis.weighted_returns.get(period),
            analysis.value_change(period),
            analysis.window_label(period),
            analysis.flow_note(period),
        )
        for period in periods
    ]
    entries = [item for item in entries if item[1] is not None]
    if not entries:
        logger.warning("Portfoy degisim grafigi icin veri yok.")
        return None

    _prepare_backend(show)
    from matplotlib.patches import FancyBboxPatch, Rectangle

    palette = _style(theme)
    count = len(entries)
    fig, ax = _base_figure(palette, figsize=(max(6.4, 3.7 * count + 1.0), 4.4))

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    gap = 0.045
    width = (1.0 - gap * (count - 1)) / count
    # Ortak olcek: en buyuk mutlak yuzde siriti tam doldurur, digerleri oranli.
    scale = max(abs(pct) for _label, pct, _amount, _window, _flow in entries) or 1.0

    for index, (label, pct, amount, window, flow) in enumerate(entries):
        left = index * (width + gap)
        center = left + width / 2
        tone = palette["up"] if pct >= 0 else palette["down"]

        ax.add_patch(
            FancyBboxPatch(
                (left, 0.06), width, 0.88,
                boxstyle="round,pad=0,rounding_size=0.02",
                facecolor=palette["page"], edgecolor=palette["grid"],
                linewidth=1.0, clip_on=False,
            )
        )
        ax.text(
            center, 0.86, label,
            ha="center", va="center", fontsize=12,
            color=palette["text_secondary"],
        )
        # Hangi pencere olculdu: "Aylık" tek basina belirsizdi.
        ax.text(
            center, 0.79, window.split(" · ", 1)[-1],
            ha="center", va="center", fontsize=9, color=palette["muted"],
        )
        ax.text(
            center, 0.63, fmt_pct(pct),
            ha="center", va="center", fontsize=30, fontweight="bold", color=tone,
        )
        ax.text(
            center, 0.42, fmt_money_change(amount),
            ha="center", va="center", fontsize=14,
            fontweight="bold", color=palette["text_primary"],
        )
        ax.text(
            center, 0.325, "portföy değeri değişimi",
            ha="center", va="center", fontsize=9, color=palette["muted"],
        )
        # Akis notu en altta, soluk ve kucuk: getiri DEGIL, portfoye giren/cikan
        # para. Getiri rakamiyla ayni buyuklukte yazmak ikisini tek bir buyukluk
        # gibi okuturdu. Tutar ve kaynak iki satira ayriliyor; kart dar oldugunda
        # (3 periyot yan yana) tek satir tasardi.
        if flow:
            tutar, _, kaynak = flow.partition(" (")
            ax.text(
                center, 0.265,
                f"{tutar}\n({kaynak}" if kaynak else tutar,
                ha="center", va="center", fontsize=7.5,
                linespacing=1.4, color=palette["muted"],
            )

        # Sifir ortali sirit: sag = arti, sol = eksi. Uzunluk ortak olcekte.
        track_half = width * 0.36
        bar_y, bar_h = 0.15, 0.045
        ax.add_patch(
            Rectangle(
                (center - track_half, bar_y), track_half * 2, bar_h,
                facecolor=palette["grid"], edgecolor="none",
            )
        )
        length = track_half * (abs(pct) / scale)
        ax.add_patch(
            Rectangle(
                (center if pct >= 0 else center - length, bar_y), length, bar_h,
                facecolor=tone, edgecolor="none",
            )
        )
        ax.plot(
            [center, center], [bar_y - 0.012, bar_y + bar_h + 0.012],
            color=palette["axis"], linewidth=1.0,
        )

    _title(
        fig,
        "Portföy Değişimi",
        f"Toplam {fmt_money(analysis.total_value)} · {analysis.as_of:%d.%m.%Y}"
        + " · kayan pencereler, ay başından değil",
        palette,
    )
    return _finish(fig, output_dir / "portfoy_degisimi.png", palette, show)


# --------------------------------------------------------------------------
# 4) Takvim ayi karnesi
# --------------------------------------------------------------------------
def monthly_track_chart(
    analysis: PortfolioAnalysis,
    record: list,
    output_dir: Path,
    theme: str = "light",
    show: bool = False,
) -> Path | None:
    """Kapanmis takvim aylarinin getirisi, hedef cizgisiyle birlikte.

    Kayan 1 aylik rakam "su an nerede duruyorum"u soyler ama asla bir ayi
    KAPATMAZ; "hedefi tutturabiliyor muyum" sorusunun cevabi kapanmis aylarin
    dizisidir. Bu grafik o diziyi gosterir.

    Renk hedefe gore: hedefi tutan ay mavi, tutmayan kirmizi. Tarama dokusu
    olan aylar TEMSILI'dir - portfoy kaydi olmadigi icin fonlarin gercek ay
    getirileri bugunku agirliklarla canlandirilmistir, gerceklesmis sicil
    degildir (GIPS bu ayrimi zorunlu tutar).
    """
    if not record:
        logger.warning("Takvim ayi karnesi icin veri yok.")
        return None

    _prepare_backend(show)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter

    palette = _style(theme)
    target = analysis.target_monthly_return
    count = len(record)
    fig, ax = _base_figure(palette, figsize=(max(7.6, 0.92 * count + 2.4), 5.8))

    values = [item.ret for item in record]
    positions = list(range(count))
    colors = [palette["up"] if item.ret >= target else palette["down"] for item in record]
    # Doku ikinci kodlamadir: temsili ayi renkten bagimsiz ayirt ettirir.
    hatches = ["///" if not item.is_actual else None for item in record]

    bars = ax.bar(
        positions, values, width=0.66, color=colors,
        edgecolor=palette["surface"], linewidth=2,
    )
    for bar, hatch, item in zip(bars, hatches, record):
        if hatch:
            bar.set_hatch(hatch)
        if not item.closed:  # devam eden ay: icini bosalt, kapanmadigi belli olsun
            bar.set_alpha(0.45)

    ax.axhline(0, color=palette["axis"], linewidth=1.2, zorder=2)
    ax.axhline(
        target, color=palette["text_primary"], linewidth=1.4,
        linestyle=(0, (5, 3)), zorder=3,
    )

    span = max(max(values), target, 0.01) - min(min(values), 0.0)
    offset = span * 0.03
    for index, item in zip(positions, record):
        above = item.ret >= 0
        ax.text(
            index,
            item.ret + (offset if above else -offset),
            fmt_number(item.ret, 1),
            ha="center", va="bottom" if above else "top",
            fontsize=9, color=palette["text_primary"],
        )

    ax.set_xticks(positions)
    ax.set_xticklabels(
        [item.short_label for item in record],
        color=palette["text_secondary"], fontsize=9.5,
    )
    ax.tick_params(axis="both", colors=palette["muted"], labelsize=9, length=0)
    ax.set_ylabel("Aylık getiri (%)", color=palette["text_secondary"], fontsize=9.5)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt_number(v, 0)))

    for spine_name, spine in ax.spines.items():
        spine.set_visible(spine_name == "bottom")
        spine.set_color(palette["axis"])
    ax.yaxis.grid(True, color=palette["grid"], linewidth=0.8)
    ax.set_axisbelow(True)

    pad = span * 0.18
    ax.set_xlim(-0.7, count - 0.3)
    ax.set_ylim(min(0.0, min(values)) - pad, max(max(values), target) + pad)

    entries = [
        (Patch(facecolor=palette["up"]), f"Hedefi tuttu (≥ %{fmt_number(target, 0)})"),
        (Patch(facecolor=palette["down"]), "Hedefin altında"),
        (Line2D([], [], color=palette["text_primary"], linestyle=(0, (5, 3))),
         f"Hedef %{fmt_number(target, 0)}"),
    ]
    if any(not item.is_actual for item in record):
        entries.append(
            (Patch(facecolor=palette["muted"], hatch="///"), "Temsili (geriye dönük)")
        )
    if any(not item.closed for item in record):
        # Soluk cubuk = ay heniz kapanmadi. Bilgi x ekseninde ikinci satirdayken
        # efsanenin uzerine biniyordu; efsaneye tasindi.
        entries.append((Patch(facecolor=palette["up"], alpha=0.45), "Ay devam ediyor"))
    _legend(ax, [handle for handle, _ in entries], [text for _, text in entries], palette)

    closed = [item for item in record if item.closed]
    hit = sum(1 for item in closed if item.ret >= target)
    # Bilesik ortalama: aylik hedef bilesik anlamda konur, aritmetik ortalama
    # ard arda gelen aylarin gercek sonucunu vermez.
    if closed:
        product = 1.0
        for item in closed:
            product *= 1.0 + item.ret / 100.0
        geometric = (product ** (1.0 / len(closed)) - 1.0) * 100.0
        summary = (
            f"{len(closed)} kapanmış ayda {hit} kez hedef tuttu · "
            f"aylık ortalama (bileşik) {fmt_pct(geometric)}"
        )
    else:
        summary = "Henüz kapanmış ay yok"
    if all(not item.is_actual for item in record):
        summary += " · tamamı temsili"

    _title(fig, "Takvim Ayı Karnesi", summary, palette)
    return _finish(fig, output_dir / "aylik_karne.png", palette, show)


# --------------------------------------------------------------------------
def render_all(
    analysis: PortfolioAnalysis,
    output_dir: Path | None = None,
    theme: str = "light",
    show: bool = False,
    periods: tuple[str, ...] | list[str] = ("haftalik", "aylik"),
    track_record: list | None = None,
) -> list[Path]:
    """Butun grafikleri uretir; biri patlarsa digerleri yine de uretilir."""
    output_dir = output_dir or config.OUTPUT_DIR
    periods = tuple(periods)
    created: list[Path] = []

    builders = [lambda: allocation_pie(analysis, output_dir, theme, show)]
    builders += [
        # Varsayilan deger dongu degiskenini yakalar; late binding tuzagi yok.
        (lambda period=period: contribution_chart(
            analysis, output_dir, period, theme, show
        ))
        for period in periods
    ]
    builders.append(
        lambda: portfolio_change_chart(analysis, output_dir, periods, theme, show)
    )
    if track_record:
        builders.append(
            lambda: monthly_track_chart(analysis, track_record, output_dir, theme, show)
        )

    for builder in builders:
        try:
            result = builder()
        except Exception as exc:  # grafik hatasi raporu durdurmasin
            logger.warning("Grafik olusturulamadi: %s", exc)
            continue
        if result is not None:
            created.append(result)
    return created
