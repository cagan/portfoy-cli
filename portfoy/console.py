"""Terminal ciktisi. `rich` varsa renkli tablo, yoksa duz metin."""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__
from .analytics import PortfolioAnalysis
from . import config
from .formatting import (
    fmt_money, fmt_money_change, fmt_number, fmt_pct, fmt_points, fmt_price, fmt_units
)

try:  # rich opsiyonel: kurulu degilse duz metne duseriz
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _RICH = True
except ImportError:  # pragma: no cover
    _RICH = False


def _tone(value: float | None) -> str:
    if value is None:
        return "dim"
    return "green" if value >= 0 else "red"


def _return_text(row, period: str) -> str:
    """Getiri hucresi. Fon periyodun tamaminda elde degilse yildizla isaretlenir."""
    text = fmt_pct(row.returns.get(period))
    return f"{text}*" if row.is_partial(period) else text


# --------------------------------------------------------------------------
def render(analysis: PortfolioAnalysis) -> None:
    if _RICH:
        _render_rich(analysis)
    else:
        _render_plain(analysis)


def _render_rich(analysis: PortfolioAnalysis) -> None:
    console = Console()

    # SIMPLE_HEAD: dikey cizgiler yok -> dar terminallerde de sigar.
    table = Table(
        title=f"TEFAS Portföyü · {analysis.as_of:%d.%m.%Y}",
        title_style="bold",
        header_style="bold",
        box=box.SIMPLE_HEAD,
        expand=False,
        pad_edge=False,
    )
    table.add_column("Fon", style="bold", no_wrap=True)
    table.add_column("Adet", justify="right", no_wrap=True)
    table.add_column("Fiyat", justify="right", no_wrap=True)
    table.add_column("Değer ₺", justify="right", no_wrap=True)
    table.add_column("Ağırlık", justify="right", no_wrap=True)
    table.add_column("Günlük", justify="right", no_wrap=True)
    table.add_column("Haftalık", justify="right", no_wrap=True)
    table.add_column("Aylık", justify="right", no_wrap=True)

    # Dar terminallerde katki sutununu gizle: turetilmis veri, Excel'de ve
    # katki grafiginde zaten var. Kirpilmis sayi gostermektense hic gosterme.
    show_contribution = console.width >= 96
    if show_contribution:
        table.add_column("Katkı", justify="right", no_wrap=True)

    for row in analysis.rows:
        monthly_contribution = row.contributions.get("aylik")
        cells = [
            row.code,
            fmt_units(row.units),
            fmt_price(row.price),
            fmt_number(row.value, 0),
            f"%{fmt_number(row.weight_pct, 1)}",
            Text(_return_text(row, "gunluk"), style=_tone(row.returns.get("gunluk"))),
            Text(_return_text(row, "haftalik"), style=_tone(row.returns.get("haftalik"))),
            Text(_return_text(row, "aylik"), style=_tone(row.returns.get("aylik"))),
        ]
        if show_contribution:
            cells.append(
                Text(fmt_points(monthly_contribution), style=_tone(monthly_contribution))
            )
        table.add_row(*cells)

    console.print()
    console.print(table)

    # Fon adlari: yanlis bir fon kodu girildiginde hemen goze carpsin.
    names = Text()
    for row in analysis.rows:
        names.append(f"  {row.code}  ", style="bold")
        names.append(f"{row.title}\n", style="dim")
    console.print(names, end="")

    # Yildizin ne demek oldugu tablonun hemen altinda yazsin; okuyucu rakami
    # TEFAS'in tam periyot getirisiyle karistirmasin.
    partial = analysis.partial_rows()
    if partial:
        note = Text()
        for row in partial:
            note.append(f"  * {row.code}  ", style="yellow")
            note.append(f"{row.partial_note}\n", style="dim")
        console.print(note, end="")

    # --- Ozet paneli ------------------------------------------------------
    lines = Text()
    lines.append("Toplam Portföy Değeri   ", style="bold")
    lines.append(fmt_money(analysis.total_value), style="bold cyan")
    lines.append("\n")

    for period, label in config.PERIOD_LABELS.items():
        value = analysis.weighted_returns.get(period)
        lines.append(f"Ağırlıklı {label:<9}     ", style="bold")
        lines.append(fmt_pct(value), style=_tone(value))
        change = analysis.value_change(period)
        if change is not None:
            lines.append(f"   ({fmt_money_change(change)})", style="dim")
        covered = analysis.coverage.get(period, 0.0)
        if 0 < covered < 0.999:
            lines.append(f"   [ağırlığın %{covered * 100:.0f}'i]", style="yellow")
        clipped = analysis.partial_rows(period)
        if clipped:
            lines.append(
                f"   [{', '.join(row.code for row in clipped)} kısmi]", style="yellow"
            )
        lines.append("\n")

    profit = analysis.total_profit
    if profit is not None:
        lines.append("Toplam Kar/Zarar        ", style="bold")
        lines.append(fmt_money_change(profit), style=_tone(profit))
        lines.append(f"   ({fmt_pct(analysis.total_profit_pct)})", style="dim")
        lines.append("\n")

    lines.append("\n")
    lines.append("Hedef (aylık)           ", style="bold")
    lines.append(f"%{fmt_number(analysis.target_monthly_return)}")
    lines.append("\n")

    gap = analysis.target_gap
    if gap is None:
        lines.append("Durum                   ", style="bold")
        lines.append("aylık veri eksik, hedef değerlendirilemedi", style="yellow")
    else:
        lines.append("Durum                   ", style="bold")
        if gap >= 0:
            lines.append(f"HEDEF TUTTU  (+{fmt_number(gap)} yüzde puan)", style="bold green")
        else:
            lines.append(f"HEDEFİN ALTINDA  ({fmt_number(gap)} yüzde puan)", style="bold red")

    console.print(
        Panel(
            lines,
            title="Özet",
            border_style="grey50",
            expand=False,
        )
    )

    if analysis.failures:
        warn = Text()
        for code, message in sorted(analysis.failures.items()):
            warn.append(f"{code}: ", style="bold yellow")
            warn.append(f"{message}\n")
        console.print(
            Panel(warn, title="Verisi alınamayan fonlar", border_style="yellow", expand=False)
        )
    console.print()


def _render_plain(analysis: PortfolioAnalysis) -> None:
    header = (
        f"{'Fon':<6}{'Adet':>14}{'Fiyat':>13}{'Değer':>18}"
        f"{'Ağırlık':>10}{'Günlük':>11}{'Haftalık':>11}{'Aylık':>11}"
    )
    print()
    print(f"TEFAS Portföyü · {analysis.as_of:%d.%m.%Y}")
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for row in analysis.rows:
        print(
            f"{row.code:<6}{fmt_units(row.units):>14}{fmt_number(row.price, 6):>13}"
            f"{fmt_money(row.value):>18}{'%' + fmt_number(row.weight_pct, 1):>10}"
            f"{_return_text(row, 'gunluk'):>11}"
            f"{_return_text(row, 'haftalik'):>11}"
            f"{_return_text(row, 'aylik'):>11}"
        )
    print("-" * len(header))

    for row in analysis.partial_rows():
        print(f"  * {row.code}: {row.partial_note}")

    print(f"Toplam Portföy Değeri : {fmt_money(analysis.total_value)}")
    for period, label in config.PERIOD_LABELS.items():
        print(
            f"Ağırlıklı {label:<9}   : "
            f"{fmt_pct(analysis.weighted_returns.get(period))}"
        )
    profit = analysis.total_profit
    if profit is not None:
        print(f"Toplam Kar/Zarar      : {fmt_money_change(profit)} "
              f"({fmt_pct(analysis.total_profit_pct)})")
    print(f"Hedef (aylık)         : %{fmt_number(analysis.target_monthly_return)}")
    gap = analysis.target_gap
    if gap is None:
        print("Durum                 : aylık veri eksik")
    else:
        durum = "HEDEF TUTTU" if gap >= 0 else "HEDEFİN ALTINDA"
        print(f"Durum                 : {durum} ({fmt_number(gap)} yüzde puan)")

    if analysis.failures:
        print("\nVerisi alınamayan fonlar:")
        for code, message in sorted(analysis.failures.items()):
            print(f"  - {code}: {message}")
    print()


def print_holdings(portfolio) -> None:
    """Fiyat cekmeden sadece kayitli adetleri ve alis bilgisini listeler."""
    if portfolio.is_empty():
        print(f"Portföy boş. Örnek: {invocation()} add TLY 1000 --date 2026-08-03")
        return

    undated = portfolio.undated_codes()
    hint = (
        "Alış tarihi olmayan fonlarda periyot getirileri, fon portföye girmeden "
        "önceki günleri de kapsar.\n"
        f"Düzeltmek için: {invocation()} add {undated[0]} <adet> --date GG.AA.YYYY "
        "--price <fiyat>"
    ) if undated else None

    if _RICH:
        console = Console()
        table = Table(title="Kayıtlı Portföy", header_style="bold", box=box.SIMPLE_HEAD)
        table.add_column("Fon", style="bold")
        table.add_column("Adet", justify="right")
        table.add_column("Alış", justify="center")
        table.add_column("Ort. Maliyet", justify="right")
        table.add_column("İşlem", justify="right")
        for code in portfolio.codes:
            position = portfolio.positions[code]
            acquired = position.acquired_on
            average = position.average_cost
            table.add_row(
                code,
                fmt_units(position.units),
                acquired.strftime("%d.%m.%Y") if acquired else "—",
                fmt_price(average) if average else "—",
                str(len(position.lots)),
            )
        console.print(table)
        console.print(
            f"Hedef aylık getiri: %{fmt_number(portfolio.target_monthly_return)}",
            style="dim",
        )
        if hint:
            console.print(hint, style="yellow")
    else:
        print("Kayıtlı Portföy")
        for code in portfolio.codes:
            position = portfolio.positions[code]
            acquired = position.acquired_on
            print(
                f"  {code:<6} {fmt_number(position.units, 4):>14}"
                f"  alış: {acquired.strftime('%d.%m.%Y') if acquired else '—'}"
            )
        print(f"Hedef aylık getiri: %{fmt_number(portfolio.target_monthly_return)}")
        if hint:
            print(hint)


# --------------------------------------------------------------------------
# Yardim ekrani
# --------------------------------------------------------------------------
# Komut adlari ve bayraklar Ingilizce, aciklamalar Turkce. argparse'in kendi
# --help ciktisi komutlari duz bir liste olarak basiyor; burada islevlerine gore
# gruplayip hangi bayragin hangi komutta gecerli oldugunu da gosteriyoruz.
def _portfolio_hint() -> str:
    try:
        return str(config.PORTFOLIO_FILE.relative_to(config.BASE_DIR))
    except ValueError:  # dosya proje disina tasinmissa tam yolu goster
        return str(config.PORTFOLIO_FILE)


HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Portföy",
        (
            ("add CODE UNITS", "Fon ekler; fon kayıtlıysa adedini değiştirir"),
            ("add ... --date GG.AA.YYYY", "Alış tarihi; periyot getirisi bu tarihten itibaren sayılır"),
            ("add ... --price FİYAT", "Alış birim fiyatı; maliyet ve kar/zarar hesaplanır"),
            ("add CODE UNITS --accumulate", "Mevcut adedin üzerine ekler (negatif değer düşer)"),
            ("remove CODE", "Fonu portföyden çıkarır"),
            ("list", "Kayıtlı fonları gösterir (TEFAS'a bağlanmadan)"),
            ("lots [CODE]", "Kayıtlı alış/satış işlemlerini gösterir"),
            ("target PERCENT", "Aylık getiri hedefini ayarlar, örn: target 12"),
        ),
    ),
    (
        "Rapor",
        (
            ("status", "Güncel fiyatlarla tabloyu terminalde gösterir"),
            ("report", "Tablo + Excel + grafik üretir, ayarlıysa e-posta gönderir"),
        ),
    ),
    (
        "E-posta",
        (
            ("mail-setup --user ADRES", "Gönderimi yapılandırır (şifre gizli sorulur)"),
            ("mail-test", "Gerçek rapordaki eklerle deneme e-postası gönderir"),
        ),
    ),
    (
        "Bakım",
        (
            ("clear-cache", "Yerel TEFAS veri önbelleğini siler"),
            ("help", "Bu ekranı gösterir"),
        ),
    ),
)


def _flag_sections() -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    periods = "|".join(config.PERIODS)
    return (
        (
            "Genel bayraklar",
            (
                ("--data-file PATH", f"Başka bir portföy dosyası kullan (varsayılan: {_portfolio_hint()})"),
                ("--verbose", "Ayrıntılı hata ayıklama günlüğü"),
            ),
        ),
        (
            "Veri çekme bayrakları — status, report, mail-test",
            (
                ("--days N", f"Kaç günlük geçmiş çekilsin (varsayılan: {config.LOOKBACK_DAYS})"),
                ("--no-cache", "Önbelleği atla, veriyi TEFAS'tan yeniden çek"),
            ),
        ),
        (
            "Çıktı bayrakları — report, mail-test",
            (
                ("--output-dir PATH", "Excel ve grafiklerin yazılacağı klasör"),
                ("--no-excel", "Excel çıktısını atla"),
                ("--no-charts", "Grafikleri atla"),
                ("--theme light|dark", "Grafik teması (varsayılan: light)"),
                ("--period " + periods, "Katkı grafiğinin periyodu (varsayılan: aylik)"),
            ),
        ),
        (
            "Yalnızca report",
            (
                ("--show", "Grafikleri ekranda da aç"),
                ("--email", "E-postayı zorla; ayar eksikse sebebini yazdır"),
                ("--no-email", "Bu çalıştırmada e-posta gönderme"),
            ),
        ),
    )


def invocation() -> str:
    """Kullanicinin gercekte yazdigi komut: `portfoy` ya da `python main.py`.

    Ornekler kurulu console script ile depodan calistirma arasinda dogru
    bicimi gostersin diye sys.argv[0]'dan turetiliyor.
    """
    name = Path(sys.argv[0] or "").name
    if not name:
        return "portfoy"
    return f"python {name}" if name.endswith(".py") else name


def help_examples() -> tuple[str, ...]:
    run = invocation()
    return (
        f"{run} add TMV 40631 --date 03.08.2026 --price 8.613999",
        f"{run} add DFI 300 --accumulate --date 05.08.2026 --price 12.4",
        f"{run} add DFI -300 --accumulate --date 07.08.2026   # kısmi satış",
        f"{run} lots TMV",
        f"{run} status",
        f"{run} report --show --theme dark",
        f"{run} --data-file ~/alt.json status  # ikinci bir portföy",
    )


def print_help() -> None:
    """Butun komutlari islevlerine gore gruplanmis halde tek ekranda basar."""
    sections = (*HELP_SECTIONS, *_flag_sections())
    if _RICH:
        _print_help_rich(sections)
    else:
        _print_help_plain(sections)


def _print_help_rich(sections) -> None:
    console = Console()
    title = Text()
    title.append("portfoy", style="bold cyan")
    title.append(f"  v{__version__}", style="dim")
    title.append("   TEFAS yatırım fonu portföyü takip ve raporlama aracı", style="dim")

    console.print()
    console.print(title)
    console.print()

    for heading, entries in sections:
        console.print(Text(heading, style="bold"))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="cyan", no_wrap=True)
        grid.add_column()
        for name, description in entries:
            grid.add_row(f"  {name}", description)
        console.print(grid)
        console.print()

    console.print(Text("Örnekler", style="bold"))
    for line in help_examples():
        console.print(Text(f"  {line}", style="dim"))
    console.print()
    console.print(Text(f"Bir komutun tüm seçenekleri için: {invocation()} <command> --help",
                       style="dim"))
    console.print()


def _print_help_plain(sections) -> None:
    width = max(
        len(name) for _, entries in sections for name, _ in entries
    )

    print()
    print(f"portfoy v{__version__} — TEFAS yatırım fonu portföyü takip ve raporlama aracı")
    for heading, entries in sections:
        print(f"\n{heading}")
        for name, description in entries:
            print(f"  {name:<{width}}  {description}")

    print("\nÖrnekler")
    for line in help_examples():
        print(f"  {line}")
    print(f"\nBir komutun tüm seçenekleri için: {invocation()} <command> --help\n")
