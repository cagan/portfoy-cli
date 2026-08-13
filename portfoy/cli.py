"""TEFAS Portföy Takip CLI - argparse arayuzu.

Kullanim ornekleri (kurulu ise `portfoy`, degilse `python main.py`):
    portfoy add TLY 1500
    portfoy status
    portfoy report
    portfoy list
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import __version__, analytics, charts, config, console, excel_report, storage
from . import mailer, tefas_client
from .formatting import fmt_money, fmt_price, fmt_units
from .mailer import MailError
from .storage import Portfolio, StorageError

EXIT_OK = 0
EXIT_ERROR = 1


# --------------------------------------------------------------------------
# Ortak yardimcilar
# --------------------------------------------------------------------------
def _load_portfolio(args) -> Portfolio:
    return storage.load(args.data_file)


def _save_portfolio(portfolio: Portfolio, args) -> None:
    path = storage.save(portfolio, args.data_file)
    logging.debug("Portföy kaydedildi: %s", path)


def _collect_analysis(portfolio: Portfolio, args) -> analytics.PortfolioAnalysis | None:
    """Fiyatlari ceker ve analizi uretir. Portfoy bossa None doner."""
    if portfolio.is_empty():
        path = args.data_file or config.PORTFOLIO_FILE
        durum = "dosya henüz oluşmamış" if not path.exists() else "dosyada kayıtlı fon yok"
        print(
            f"Portföy boş ({durum}).\n"
            f"Adetler şuraya kaydedilir: {path}\n\n"
            "Fonlarınızı bir kez girin, sonraki çalıştırmalarda hatırlanır:\n"
            + "\n".join(
                f"  {console.invocation()} add {code} <adet> --date GG.AA.YYYY --price <fiyat>"
                for code in config.DEFAULT_FUNDS
            )
        )
        return None

    print(f"TEFAS'tan veri çekiliyor: {', '.join(portfolio.codes)} ...")
    histories, failures = tefas_client.fetch_many(
        portfolio.codes,
        lookback_days=args.days,
        use_cache=not args.no_cache,
    )

    if not histories:
        print(
            "\nHiçbir fonun verisi alınamadı. İnternet bağlantınızı kontrol edin "
            "veya TEFAS geçici olarak erişilemiyor olabilir.",
            file=sys.stderr,
        )
        for code, message in sorted(failures.items()):
            print(f"  - {code}: {message}", file=sys.stderr)
        return None

    return analytics.analyze(portfolio, histories, failures)


# --------------------------------------------------------------------------
# Komutlar
# --------------------------------------------------------------------------
def cmd_add(args) -> int:
    portfolio = _load_portfolio(args)
    try:
        code = storage.normalize_code(args.code)
        on = storage.parse_date(args.date) if args.date else None
        existing = portfolio.positions.get(code)
        previous = existing.units if existing else None

        if args.accumulate:
            total = portfolio.add_lot(code, args.units, on, args.price)
            print(f"{code}: {fmt_units(previous or 0)} + {fmt_units(args.units)} "
                  f"→ {fmt_units(total)} adet")
        else:
            if existing and len(existing.lots) > 1:
                # Islem gecmisini sessizce silmek alis tarihlerini kaybettirir.
                print(
                    f"DİKKAT: {code} için {len(existing.lots)} işlem kaydı vardı, "
                    "hepsi tek kayda indirildi.\n"
                    "  (geçmişi korumak için: --accumulate)"
                )
            portfolio.set_units(code, args.units, on, args.price)
            if previous is not None and previous != args.units:
                # Sessizce ezmek portfoyu kaybettirir - degisimi acikca goster.
                print(
                    f"DİKKAT: {code} zaten kayıtlıydı, adedi değiştirildi.\n"
                    f"  {fmt_units(previous)} → {fmt_units(args.units)} adet\n"
                    f"  (üzerine yazmak yerine eklemek için: --accumulate)"
                )
            else:
                print(f"{code} kaydedildi → {fmt_units(args.units)} adet")

        _print_lot_hint(portfolio, code, on, args.price)
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    path = storage.save(portfolio, args.data_file)
    print(f"Kayıt dosyası: {path}")
    if previous is not None:
        print(f"Önceki hali  : {storage.backup_path(path)}")
    return EXIT_OK


def _print_lot_hint(portfolio, code: str, on, price) -> None:
    """Alis tarihi verildiyse ozetler, verilmediyse neden gerektigini soyler."""
    if on is not None:
        detail = f"  Alış: {on:%d.%m.%Y}"
        if price:
            detail += f" · {fmt_price(price)} ₺"
        print(detail)
        # Ileri tarih neredeyse her zaman yazim hatasi (gun/ay yer degistirmis).
        # Kirpma bu tarihi baz alacagi icin sessizce gecmek dogru olmaz.
        if on > date.today():
            print(f"  DİKKAT: {on:%d.%m.%Y} gelecekte. Gün ve ayı karıştırmış olabilirsiniz.")
        return

    position = portfolio.positions.get(code)
    if position is None or position.has_dates:
        return

    print(
        f"  Not: {code} için alış tarihi yok. Haftalık/aylık getiri, fon portföye\n"
        "  girmeden önceki günleri de kapsar. Düzeltmek için:\n"
        # Adet burada dogrudan komuta yapistirilacak: Turkce binlik ayraci
        # kullanilirsa argparse sayiyi sessizce yanlis okur.
        f"    {console.invocation()} add {code} {position.units:g} "
        "--date GG.AA.YYYY --price <birim fiyat>"
    )


def cmd_lots(args) -> int:
    """Kayitli alis/satis islemlerini gosterir (TEFAS'a baglanmadan)."""
    portfolio = _load_portfolio(args)
    codes = [storage.normalize_code(args.code)] if args.code else portfolio.codes
    if not codes:
        print("Portföy boş.")
        return EXIT_OK

    for code in codes:
        position = portfolio.positions.get(code)
        if position is None:
            print(f"{code} portföyde bulunamadı.", file=sys.stderr)
            return EXIT_ERROR

        print(f"\n{code} — {fmt_units(position.units)} adet")
        for lot in position.lots:
            tarih = lot.date.strftime("%d.%m.%Y") if lot.date else "tarih yok"
            fiyat = f"{fmt_price(lot.price)} ₺" if lot.price is not None else "fiyat yok"
            tur = "alış" if lot.units > 0 else "satış"
            print(f"  {tarih:>12}  {fmt_units(abs(lot.units)):>14} {tur:<6} {fiyat:>14}")

        cost = position.cost_basis
        if cost is not None:
            print(f"  {'toplam maliyet':>12}: {fmt_money(cost)} "
                  f"(ort. {fmt_price(position.average_cost)} ₺)")
    print()
    return EXIT_OK


def cmd_remove(args) -> int:
    portfolio = _load_portfolio(args)
    try:
        removed = portfolio.remove(args.code)
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not removed:
        print(f"{args.code.upper()} portföyde bulunamadı.", file=sys.stderr)
        return EXIT_ERROR

    _save_portfolio(portfolio, args)
    print(f"{args.code.upper()} portföyden silindi.")
    return EXIT_OK


def cmd_list(args) -> int:
    console.print_holdings(_load_portfolio(args))
    return EXIT_OK


def cmd_target(args) -> int:
    portfolio = _load_portfolio(args)
    portfolio.target_monthly_return = args.percent
    _save_portfolio(portfolio, args)
    print(f"Hedef aylık getiri %{args.percent:g} olarak ayarlandı.")
    return EXIT_OK


def cmd_status(args) -> int:
    analysis = _collect_analysis(_load_portfolio(args), args)
    if analysis is None:
        return EXIT_ERROR
    console.render(analysis)
    return EXIT_OK


def cmd_help(args) -> int:
    console.print_help()
    return EXIT_OK


def _produce_outputs(analysis, args) -> list[Path]:
    """Excel ve grafikleri üretir; e-postaya da bunlar eklenir."""
    output_dir = args.output_dir or config.OUTPUT_DIR
    produced: list[Path] = []

    if not args.no_excel:
        excel_path = excel_report.build_report(analysis, output_dir)
        if excel_path:
            produced.append(excel_path)
        else:
            print("Uyarı: Excel raporu oluşturulamadı.", file=sys.stderr)

    if not args.no_charts:
        produced.extend(
            charts.render_all(
                analysis,
                output_dir=output_dir,
                theme=args.theme,
                show=getattr(args, "show", False),
                period=args.period,
            )
        )

    return produced


def cmd_report(args) -> int:
    analysis = _collect_analysis(_load_portfolio(args), args)
    if analysis is None:
        return EXIT_ERROR

    console.render(analysis)

    produced = _produce_outputs(analysis, args)

    if produced:
        print("Oluşturulan dosyalar:")
        for item in produced:
            print(f"  • {item}")

    # Dosyalar yazildiktan SONRA e-posta: uretilen dosyalar eke gidiyor.
    # Gonderim basarisiz olsa bile rapor diske yazilmis durumda.
    mail_sent = _maybe_send_mail(analysis, produced, args)

    if not produced and not mail_sent:
        print("Hiçbir çıktı üretilmedi.", file=sys.stderr)
        return EXIT_ERROR

    return EXIT_OK


def _maybe_send_mail(analysis, attachments: list[Path], args) -> bool:
    """Ayarlar tamsa raporu e-postayla gönderir. Gönderildiyse True döner."""
    if args.no_email:
        return False

    settings = mailer.load_config()
    if not settings.is_ready:
        if args.email:  # kullanici acikca istedi ama ayar eksik -> sebebini soyle
            print(
                "E-posta gönderilemedi: " + ", ".join(settings.missing_fields()) + " eksik.\n"
                f"Kurulum: {console.invocation()} mail-setup --user ornek@gmail.com",
                file=sys.stderr,
            )
        return False

    try:
        recipients = mailer.send_report(analysis, attachments, settings)
    except MailError as exc:
        print(f"E-posta gönderilemedi: {exc}", file=sys.stderr)
        return False

    print(f"E-posta gönderildi → {', '.join(recipients)}")
    return True


def cmd_mail_setup(args) -> int:
    import getpass

    current = mailer.load_config()
    user = args.user or current.user
    if not user:
        print("Gönderen hesabı belirtin: --user ornek@gmail.com", file=sys.stderr)
        return EXIT_ERROR

    recipients = args.to or current.recipients or [config.DEFAULT_MAIL_TO or user]

    password = None
    if not args.no_password_prompt:
        prompt = (
            f"{user} için uygulama şifresi "
            "(Gmail: myaccount.google.com/apppasswords, boş bırakırsanız değişmez): "
        )
        try:
            password = getpass.getpass(prompt).strip() or None
        except (EOFError, KeyboardInterrupt):
            print("\nİptal edildi.", file=sys.stderr)
            return EXIT_ERROR

    path, location = mailer.save_config(
        user=user,
        recipients=recipients,
        password=password,
        host=args.host,
        port=args.port,
    )

    print(f"Ayarlar kaydedildi: {path}")
    print(f"  Gönderen : {user}")
    print(f"  Alıcı    : {', '.join(recipients)}")
    print(f"  Sunucu   : {args.host}:{args.port}")
    print(f"  Şifre    : {location}")
    print(f"\nDenemek için: {console.invocation()} mail-test")
    return EXIT_OK


def cmd_mail_test(args) -> int:
    settings = mailer.load_config()
    missing = settings.missing_fields()
    if missing:
        print("Eksik ayar: " + ", ".join(missing), file=sys.stderr)
        print(f"Kurulum: {console.invocation()} mail-setup --user ornek@gmail.com", file=sys.stderr)
        return EXIT_ERROR

    print(f"Sunucu   : {settings.host}:{settings.port}")
    print(f"Gönderen : {settings.from_address}")
    print(f"Alıcı    : {', '.join(settings.recipients)}")

    analysis = _collect_analysis(_load_portfolio(args), args)
    if analysis is None:
        return EXIT_ERROR

    # Test, gercek raporun aynisini gondersin: ekler dahil. Aksi halde test
    # gecer ama asil e-postada eklerin bozuk oldugu fark edilmez.
    produced = _produce_outputs(analysis, args)
    if produced:
        print("\nEklenen dosyalar:")
        for item in produced:
            print(f"  • {item.name}")

    try:
        recipients = mailer.send_report(analysis, produced, settings)
    except MailError as exc:
        print(f"\nBaşarısız: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"\nTest e-postası gönderildi → {', '.join(recipients)}")
    return EXIT_OK


def cmd_clear_cache(args) -> int:
    removed = tefas_client.clear_cache()
    print(f"{removed} önbellek dosyası silindi.")
    return EXIT_OK


# --------------------------------------------------------------------------
# Argüman ayristirma
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portfoy",
        description="TEFAS yatırım fonu portföyü takip ve raporlama aracı.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Örnekler:\n"
            + "\n".join(f"  {line}" for line in console.help_examples())
            + f"\n\nGruplanmış komut listesi için: {console.invocation()} help\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--data-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Portföy JSON dosyası (varsayılan: {config.PORTFOLIO_FILE})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Ayrıntılı günlük kaydı (hata ayıklama) göster.",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- add ---
    p_add = subparsers.add_parser("add", help="Fon ekle veya adedini güncelle.")
    p_add.add_argument("code", help="Fon kodu (örn: TLY)")
    p_add.add_argument("units", type=float, help="Elinizdeki pay adedi")
    p_add.add_argument(
        "--accumulate",
        action="store_true",
        help="Mevcut adedin üzerine ekle (negatif değer düşer).",
    )
    p_add.add_argument(
        "--date",
        metavar="TARİH",
        help=(
            "İşlem tarihi (2026-08-03 / 03.08.2026). Verilmezse haftalık ve aylık "
            "getiri, fon portföye girmeden önceki günleri de kapsar."
        ),
    )
    p_add.add_argument(
        "--price",
        type=float,
        metavar="FİYAT",
        help="İşlemdeki birim fiyat; maliyet ve gerçek kar/zarar bundan hesaplanır.",
    )
    p_add.set_defaults(func=cmd_add)

    # --- lots ---
    p_lots = subparsers.add_parser(
        "lots", help="Kayıtlı alış/satış işlemlerini göster."
    )
    p_lots.add_argument("code", nargs="?", help="Fon kodu (boş bırakılırsa hepsi)")
    p_lots.set_defaults(func=cmd_lots)

    # --- remove ---
    p_remove = subparsers.add_parser("remove", help="Fonu portföyden çıkar.")
    p_remove.add_argument("code", help="Fon kodu")
    p_remove.set_defaults(func=cmd_remove)

    # --- list ---
    p_list = subparsers.add_parser("list", help="Kayıtlı fonları göster (veri çekmeden).")
    p_list.set_defaults(func=cmd_list)

    # --- target ---
    p_target = subparsers.add_parser("target", help="Aylık getiri hedefini ayarla.")
    p_target.add_argument("percent", type=float, help="Hedef aylık getiri, yüzde (örn: 12)")
    p_target.set_defaults(func=cmd_target)

    # --- veri cekmeli komutlar icin ortak flag'ler ---
    fetch_flags = argparse.ArgumentParser(add_help=False)
    fetch_flags.add_argument(
        "--days",
        type=int,
        default=config.LOOKBACK_DAYS,
        metavar="N",
        help=f"Kaç günlük geçmiş çekilsin (varsayılan: {config.LOOKBACK_DAYS})",
    )
    fetch_flags.add_argument(
        "--no-cache",
        action="store_true",
        help="Önbelleği atla, veriyi TEFAS'tan yeniden çek.",
    )

    # --- status ---
    p_status = subparsers.add_parser(
        "status", parents=[fetch_flags], help="Güncel durumu terminalde tablo olarak göster."
    )
    p_status.set_defaults(func=cmd_status)

    # --- rapor uretimi icin ortak flag'ler (report + mail-test) ---
    report_flags = argparse.ArgumentParser(add_help=False)
    report_flags.add_argument(
        "--output-dir", type=Path, default=None, metavar="PATH",
        help=f"Çıktı klasörü (varsayılan: {config.OUTPUT_DIR})",
    )
    report_flags.add_argument("--no-excel", action="store_true", help="Excel çıktısını atla.")
    report_flags.add_argument("--no-charts", action="store_true", help="Grafikleri atla.")
    report_flags.add_argument(
        "--theme", choices=("light", "dark"), default="light", help="Grafik teması."
    )
    report_flags.add_argument(
        "--period",
        choices=tuple(config.PERIODS),
        default="aylik",
        help="Katkı grafiğinin periyodu (varsayılan: aylik).",
    )

    # --- report ---
    p_report = subparsers.add_parser(
        "report",
        parents=[fetch_flags, report_flags],
        help="Tablo + Excel + grafikleri üret ve e-posta gönder.",
    )
    p_report.add_argument(
        "--show", action="store_true", help="Grafikleri ekranda da aç."
    )
    p_report.add_argument(
        "--email",
        action="store_true",
        help="E-postayı zorla; ayar eksikse sebebini yazdır.",
    )
    p_report.add_argument(
        "--no-email", action="store_true", help="Bu çalıştırmada e-posta gönderme."
    )
    p_report.set_defaults(func=cmd_report)

    # --- mail-setup ---
    p_mail = subparsers.add_parser(
        "mail-setup",
        help="E-posta gönderimini yapılandır (şifre gizli sorulur).",
    )
    p_mail.add_argument("--user", help="Gönderen SMTP hesabı (örn: ornek@gmail.com)")
    p_mail.add_argument(
        "--to", action="append", metavar="ADRES",
        help="Alıcı adresi; birden çok kez verilebilir (varsayılan: gönderen hesabın kendisi)",
    )
    p_mail.add_argument("--host", default=config.DEFAULT_SMTP_HOST, help="SMTP sunucusu")
    p_mail.add_argument("--port", type=int, default=config.DEFAULT_SMTP_PORT, help="SMTP portu")
    p_mail.add_argument(
        "--no-password-prompt", action="store_true",
        help="Şifre sorma (ortam değişkeni kullanacaksanız).",
    )
    p_mail.set_defaults(func=cmd_mail_setup)

    # --- mail-test ---
    p_mail_test = subparsers.add_parser(
        "mail-test",
        parents=[fetch_flags, report_flags],
        help="Ayarları doğrulamak için ekleriyle birlikte deneme e-postası gönder.",
    )
    p_mail_test.set_defaults(func=cmd_mail_test)

    # --- clear-cache ---
    p_cache = subparsers.add_parser("clear-cache", help="Yerel veri önbelleğini sil.")
    p_cache.set_defaults(func=cmd_clear_cache)

    # --- help ---
    p_help = subparsers.add_parser("help", help="Bütün komutları gruplanmış halde göster.")
    p_help.set_defaults(func=cmd_help)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Komutsuz calistirma: argparse'in duz listesi yerine gruplanmis yardim.
    if not getattr(args, "command", None):
        console.print_help()
        return EXIT_OK

    config.ensure_dirs()

    try:
        return args.func(args)
    except StorageError as exc:
        print(f"Portföy dosyası hatası: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nİptal edildi.", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # son savunma hatti - cokme yerine anlamli mesaj
        logging.getLogger(__name__).debug("Beklenmedik hata", exc_info=True)
        print(f"Beklenmedik hata: {exc}", file=sys.stderr)
        print("Ayrıntı için --verbose bayrağıyla tekrar çalıştırın.", file=sys.stderr)
        return EXIT_ERROR
