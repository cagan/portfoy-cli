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
    """Getiri hucresi, isaretleriyle.

    `*` fon periyodun tamaminda elde degildi (getiri alistan itibaren).
    `†` periyot icinde alim/satim var; yuzde ile TL ayni tabandan cikmaz
        (bkz. config.COLUMN_BASIS_NOTE). Iki isaret AYRI seyler soyler ve ayni
        hucrede birlikte gorunebilirler.
    """
    text = fmt_pct(row.returns.get(period))
    if row.is_partial(period):
        text += "*"
    return text + row.flow_mark(period)


# --------------------------------------------------------------------------
def _track_summary(track_record: list, target: float) -> str:
    """Karneyi tek satirlik ozete indirger: kac ay tuttu, bilesik ortalama.

    Aritmetik degil BILESIK ortalama: aylik hedef ard arda gelen aylarin
    carpimiyla tutturulur, toplamiyla degil.
    """
    closed = [item for item in track_record if item.closed]
    if not closed:
        return ""
    hit = sum(1 for item in closed if item.ret >= target)
    product = 1.0
    for item in closed:
        product *= 1.0 + item.ret / 100.0
    geometric = (product ** (1.0 / len(closed)) - 1.0) * 100.0
    note = " (temsili)" if all(not item.is_actual for item in closed) else ""
    return (
        f"{len(closed)} ayda {hit} kez tuttu · "
        f"bileşik ort. {fmt_pct(geometric)}{note}"
    )


def render(analysis: PortfolioAnalysis, track_record: list | None = None) -> None:
    if _RICH:
        _render_rich(analysis, track_record or [])
    else:
        _render_plain(analysis, track_record or [])


def _render_rich(analysis: PortfolioAnalysis, track_record: list) -> None:
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

    closed_label = analysis.closed_label
    son_holding = len(analysis.rows) - 1
    for index, row in enumerate(analysis.rows):
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
        # Kapanan satir govdeden AYRI dursun: son holding satirindan sonra
        # ayirici cizgi. Tablo "elimde ne var"i anlatmaya devam etsin.
        table.add_row(*cells, end_section=bool(closed_label) and index == son_holding)

    # Kapanan pozisyonlar: holding DEGIL, o yuzden adet/fiyat/deger/agirlik
    # kolonlari bos. Ama periyot kolonlarinda TL degisimi var; boylece kolonu
    # toplayan kullanici ozetteki rakama variyor.
    #
    # TL, "Katkı" kolonuna DEGIL periyot kolonlarina yaziliyor: katki sutunu dar
    # terminalde gizleniyor ve kapanan pozisyonun rakami tam da gizlenmemesi
    # gereken sey - PHE'nin 28 bin TL'lik kaybinin gorunurlugu bu isin butun
    # meselesi.
    if closed_label:
        change_cells = [
            Text(
                fmt_money_change(analysis.closed_value_change(period)),
                style=_tone(analysis.closed_value_change(period)),
            )
            for period in ("gunluk", "haftalik", "aylik")
        ]
        closed_cells = [
            Text("Kapanan", style="dim bold"),
            Text(closed_label, style="dim"),
            "", "", "",
            *change_cells,
        ]
        if show_contribution:
            katki = analysis.closed_contribution("aylik")
            closed_cells.append(Text(fmt_points(katki), style=_tone(katki)))
        table.add_row(*closed_cells)

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

    # Kolonlarin NEYI olctugu: "%" birim fiyat getirisi, "₺" portfoye yansiyan
    # kazanc. Donem icinde islem yapilan fonda ikisi ayrisir ve aciklama
    # olmadan bu "tutarsizlik" gibi okunuyor.
    legend = Text()
    legend.append(f"  {config.COLUMN_BASIS_NOTE}\n", style="dim")
    if any(row.flow_periods for row in analysis.all_rows):
        legend.append(f"  {config.FLOW_MARK_NOTE}\n", style="dim")
    console.print(legend, end="")

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
            # "agirligin" DEGIL: kapsam artik donem basi tabanindan okunuyor
            # (bkz. PortfolioAnalysis.coverage), getirinin bolundugu buyukluk o.
            # Eksik fonun ADI da yazilir: "%88" tek basina hangi fonun disarida
            # kaldigini soylemiyor, kapanmis bir fonun kaybi da boyle yutulur.
            missing = analysis.uncovered_codes(period)
            detail = f" — {', '.join(missing)} yok" if missing else ""
            lines.append(
                f"   [dönem başı değerin %{covered * 100:.0f}'i{detail}]",
                style="yellow",
            )
        clipped = analysis.partial_rows(period)
        if clipped:
            lines.append(
                f"   [{', '.join(row.code for row in clipped)} kısmi]", style="yellow"
            )
        lines.append("\n")
        # Akis notu AYRI satirda: 197 bin TL'lik cikis bir kayip degil, portfoyden
        # nakde donen paradir. Getiri rakaminin yanina yapistirilirsa ikisi tek
        # bir buyukluk gibi okunur.
        flow = analysis.flow_note(period)
        if flow:
            lines.append(f"    ↳ {flow}\n", style="dim")

    # Kapanan fon tablodan dustugu icin "hesaba katilmamis" saniliyor; tam
    # tersini soyluyoruz.
    closed_note = analysis.closed_note
    if closed_note:
        lines.append(f"{closed_note}\n", style="yellow")

    profit = analysis.total_profit
    if profit is not None:
        lines.append("Toplam Kar/Zarar        ", style="bold")
        lines.append(fmt_money_change(profit), style=_tone(profit))
        lines.append(f"   ({fmt_pct(analysis.total_profit_pct)})", style="dim")
        lines.append("\n")

    window = analysis.window_label("aylik")
    if analysis.window("aylik"):
        # Kafa karisikliginin kaynagi buydu: "Aylık" ay basindan bu yana diye
        # okunabiliyordu. Olculen pencereyi acikca yaziyoruz.
        lines.append("Ölçüm                   ", style="bold")
        lines.append(f"{window} (ay başından değil)", style="dim")
        lines.append("\n")
        # Toplam yuzdenin PAYDASI: donem basindaki sermaye. Donem icinde giren
        # para tabana degil, yalnizca paya katiliyor (bkz.
        # analytics._flow_adjusted_change).
        lines.append("Taban                   ", style="bold")
        lines.append(config.TOTAL_BASIS_SHORT, style="dim")
        lines.append("\n")

    summary = _track_summary(track_record, analysis.target_monthly_return)
    if summary:
        lines.append("Takvim ayı karnesi      ", style="bold")
        lines.append(summary, style="dim")
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


def _render_plain(analysis: PortfolioAnalysis, track_record: list) -> None:
    header = (
        f"{'Fon':<6}{'Adet':>14}{'Fiyat':>13}{'Değer':>18}"
        f"{'Ağırlık':>10}{'Günlük':>13}{'Haftalık':>13}{'Aylık':>13}"
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
            f"{_return_text(row, 'gunluk'):>13}"
            f"{_return_text(row, 'haftalik'):>13}"
            f"{_return_text(row, 'aylik'):>13}"
        )
    # Kapanan pozisyonlar: govdenin ALTINDA, ayirici cizginin ardinda. Holding
    # degil (adet/fiyat/deger/agirlik bos) ama periyot kolonlarindaki TL'ler
    # ozetteki rakami tutturuyor.
    closed_label = analysis.closed_label
    if closed_label:
        print(
            f"{'Kapanan':<6}{closed_label:>14}{'':>13}{'':>18}{'':>10}"
            + "".join(
                f"{fmt_money_change(analysis.closed_value_change(period)):>13}"
                for period in ("gunluk", "haftalik", "aylik")
            )
        )
    print("-" * len(header))

    for row in analysis.partial_rows():
        print(f"  * {row.code}: {row.partial_note}")

    print(f"  {config.COLUMN_BASIS_NOTE}")
    if any(row.flow_periods for row in analysis.all_rows):
        print(f"  {config.FLOW_MARK_NOTE}")

    print(f"Toplam Portföy Değeri : {fmt_money(analysis.total_value)}")
    for period, label in config.PERIOD_LABELS.items():
        print(
            f"Ağırlıklı {label:<9}   : "
            f"{fmt_pct(analysis.weighted_returns.get(period))}"
            f"  ({fmt_money_change(analysis.value_change(period))})"
        )
        flow = analysis.flow_note(period)
        if flow:
            print(f"  akış                : {flow}")
    closed_note = analysis.closed_note
    if closed_note:
        print(f"Kapanan pozisyon      : {closed_note}")
    profit = analysis.total_profit
    if profit is not None:
        print(f"Toplam Kar/Zarar      : {fmt_money_change(profit)} "
              f"({fmt_pct(analysis.total_profit_pct)})")
    if analysis.window("aylik"):
        print(f"Ölçüm                 : {analysis.window_label('aylik')} (ay başından değil)")
    print(f"Taban                 : {config.TOTAL_BASIS_NOTE}")
    summary = _track_summary(track_record, analysis.target_monthly_return)
    if summary:
        print(f"Takvim ayı karnesi    : {summary}")
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
    # Kapanmis pozisyonlar listede yer almaz (elde adet yok) ama tamamen
    # gorunmez de olmamali: islem gecmisleri duruyor ve sorulabiliyor.
    # Bu hesap `is_empty()` kontrolunden ONCE yapilir: her seyini satmis
    # kullanicida portfoy "bos" sayilir ve tam da gecmisin en cok arandigi anda
    # kayitlarin durdugunu ogrenmenin hicbir yolu kalmazdi.
    closed = portfolio.closed_codes
    closed_note = (
        f"Kapanmış pozisyon: {', '.join(closed)} "
        f"({invocation()} lots {closed[0]} ile işlem geçmişi ve gerçekleşen kâr/zarar)"
    ) if closed else None

    if portfolio.is_empty():
        if closed_note:
            print(f"Açık pozisyon yok. {closed_note}")
        else:
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
        if closed_note:
            console.print(closed_note, style="dim")
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
        if closed_note:
            print(closed_note)
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
        "Emir ve valör",
        (
            ("emir CODE UNITS --sat", "Satış emri; gerçekleşme gününü valörden türetir"),
            ("emir ... --tarih GG.AA.YYYY", "Emri verdiğiniz gün"),
            ("emir ... --saat SS:DD", "Emir saati; kesim 13:30, sonrası ertesi güne kayar"),
            ("emir ... --nakit GG.AA.YYYY", "Aracı kurumun nakit tarihi — zinciri doğrular"),
            ("bekleyen", "Gerçekleşmeyi bekleyen emirleri gösterir"),
            ("valor [CODE]", "Fon valör kurallarını gösterir/düzenler"),
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
        "Web arayüzü",
        (
            ("web", "Tarayıcı arayüzünü başlatır (http://127.0.0.1:8000)"),
            ("web --open", "Arayüzü açar ve tarayıcıyı da başlatır"),
            ("web --port N", "Başka bir port kullanır"),
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
        f"{run} emir PHE 69991 --sat --tarih 01.09.2026 --saat 15:00 --nakit 04.09.2026",
        f"{run} status",
        f"{run} report --show --theme dark",
        f"{run} web --open                       # tarayıcı arayüzü",
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
