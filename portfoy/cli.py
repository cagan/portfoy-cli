"""TEFAS Portföy Takip CLI - argparse arayuzu.

Kullanim ornekleri (kurulu ise `portfoy`, degilse `python main.py`):
    portfoy add TLY 1500
    portfoy status
    portfoy report
    portfoy list
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from datetime import date, datetime, time as _time
from pathlib import Path

from . import __version__, analytics, charts, config, console, excel_report, storage
from . import bekleyen, mailer, snapshots, tefas_client, valor
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
        # Kapanmis pozisyon varsa dosya bos degil: fiyat cekilecek acik fon yok.
        # "Kayitli fon yok" demek, duran islem gecmisini yok saymak olurdu.
        closed = portfolio.closed_codes
        if closed:
            print(
                f"Açık pozisyon yok — rapor üretilemiyor.\n"
                f"Kapanmış pozisyon: {', '.join(closed)}\n"
                f"İşlem geçmişi ve gerçekleşen kâr/zarar: "
                f"{console.invocation()} lots {closed[0]}"
            )
            return None
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

    # Karne penceresi buyutulduyse cekme penceresi de buyumeli; aksi halde
    # `--months 12` sessizce 3 ay dondururdu.
    months = getattr(args, "months", config.TRACK_RECORD_MONTHS)
    lookback = max(args.days, config.lookback_for(months))

    # Acik pozisyonlarin YANI SIRA yakin zamanda kapanmis olanlar da cekilir:
    # pencere icinde satilan fonun satis gunune kadarki hareketi portfoy
    # getirisine giriyor (bkz. storage.codes_for_pricing).
    fetch_codes = portfolio.codes_for_pricing(lookback)
    print(f"TEFAS'tan veri çekiliyor: {', '.join(fetch_codes)} ...")
    histories, failures = tefas_client.fetch_many(
        fetch_codes,
        lookback_days=lookback,
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

    # Gerceklesmis bekleyen emirleri fiyat gelir gelmez isle. Analizden ONCE
    # olmali: cozulen emir portfoyu degistirir, analiz guncel hali gormeli.
    yollar = config.yan_dosyalar(args.data_file)
    for cozulen in bekleyen.coz_ve_kaydet(
        portfolio, histories,
        bekleyen_yolu=yollar["bekleyen"],
        portfoy_yolu=args.data_file,
    ):
        print(f"Bekleyen emir gerçekleşti → {cozulen.ozet()}")

    # Kalan bekleyenleri duyur: adetler tabloda hala goruntulenir (fiyat riski
    # devam ettigi icin dogru) ama bir kismi fiilen satilmis durumdadir.
    try:
        kalanlar = bekleyen.yukle(yollar["bekleyen"])
    except bekleyen.BekleyenHatasi:
        kalanlar = []
    if kalanlar:
        print(f"\n{len(kalanlar)} bekleyen emir (adetler gerçekleşmeye kadar "
              f"portföyde görünür):")
        for emir in kalanlar:
            print(f"  • {emir.ozet()}")
        print()

    analysis = analytics.analyze(portfolio, histories, failures)

    # Gunun fotografini gecmise yaz: takvim ayi karnesinin "gerceklesen" kolu
    # ancak boyle birikir. Kayit basarisiz olursa rapor yine de uretilir.
    if not getattr(args, "no_snapshot", False):
        snapshots.record(analysis, config.yan_dosyalar(args.data_file)["gecmis"])

    return analysis


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
            _print_close_summary(portfolio, code)
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

        _print_lot_hint(portfolio, code, on, args.price, args.units)
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    path = storage.save(portfolio, args.data_file)
    print(f"Kayıt dosyası: {path}")
    if previous is not None:
        print(f"Önceki hali  : {storage.backup_path(path)}")
    return EXIT_OK


def _print_close_summary(portfolio, code: str) -> None:
    """Pozisyon tamamen kapandiysa gerceklesmis kar/zarari yazar.

    Kayit silinmedigi icin bu rakam sonradan da sorulabilir (`lots`); burada
    gostermek, satisi girer girmez sonucu gormeyi sagliyor.
    """
    position = portfolio.positions.get(code)
    if position is None or not position.is_closed:
        return

    closed_on = position.closed_on
    tarih = f" ({closed_on:%d.%m.%Y})" if closed_on else ""
    print(f"  {code} pozisyonu KAPANDI{tarih} - raporlarda artık yer almayacak.")

    proceeds = position.realized_proceeds
    if proceeds is not None:
        print(f"  Toplam satış hasılatı : {fmt_money(proceeds)}")

    profit = position.realized_profit
    if profit is None:
        print(
            "  Gerçekleşmiş kâr/zarar: hesaplanamıyor — eşleşen alış veya satış\n"
            f"  lotlarından birinde fiyat yok ({console.invocation()} lots {code})."
        )
        return

    cost = position.realized_cost
    oran = f" (%{profit / cost * 100:+.2f})" if cost else ""
    print(f"  Gerçekleşmiş kâr/zarar: {fmt_money(profit)}{oran}")


def _print_lot_hint(portfolio, code: str, on, price, units: float | None = None) -> None:
    """Islem tarihi verildiyse ozetler, verilmediyse neden gerektigini soyler."""
    if on is not None:
        tur = "Satış" if units is not None and units < 0 else "Alış"
        detail = f"  {tur}: {on:%d.%m.%Y}"
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
    # Kapanmis pozisyonlar da listelenir: `lots` islem gecmisini gosterir,
    # elde kalani degil - satilan fonun kaydini saklamanin asil amaci bu.
    codes = [storage.normalize_code(args.code)] if args.code else portfolio.all_codes
    if not codes:
        print("Portföy boş.")
        return EXIT_OK

    for code in codes:
        position = portfolio.positions.get(code)
        if position is None:
            print(f"{code} portföyde bulunamadı.", file=sys.stderr)
            return EXIT_ERROR

        if position.is_closed:
            closed_on = position.closed_on
            tarih = f", {closed_on:%d.%m.%Y}" if closed_on else ""
            print(f"\n{code} — KAPANDI{tarih}")
        else:
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

        profit = position.realized_profit
        if profit is not None:
            realized_cost = position.realized_cost
            oran = f" (%{profit / realized_cost * 100:+.2f})" if realized_cost else ""
            print(f"  {'gerçekleşen':>12}: {fmt_money(profit)}{oran}")
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
    console.render(analysis, _track_record(analysis, args))
    return EXIT_OK


def cmd_help(args) -> int:
    console.print_help()
    return EXIT_OK


def _valor_takvimi_ve_kural(code: str, args) -> tuple:
    """Fonun fiyat gecmisinden takvim, tablodan (yoksa kategoriden) kural."""
    try:
        history = tefas_client.fetch_history(code, lookback_days=config.LOOKBACK_DAYS)
    except tefas_client.TefasError as exc:
        raise ValueError(
            f"{code} fiyat geçmişi alınamadı ({exc}). İş günü takvimi bu "
            f"seriden türetildiği için valör hesaplanamıyor."
        ) from None

    takvim = valor.IslemTakvimi.seriden([ts.date() for ts in history.prices.index])
    kurallar = valor.yukle(config.yan_dosyalar(args.data_file)["valor"])
    kural = kurallar.get(code)
    if kural is None:
        kural = valor.kategori_kurali(tefas_client.fetch_category(code))
    return history, takvim, kural, kurallar


def cmd_emir(args) -> int:
    """Emir zamanindan gerceklesme gununu turetip bekleyen emir olusturur."""
    satis_yonu = bool(args.sat)
    if (args.units is None) == (args.tutar is None):
        print("Hata: Adet ya da --tutar'dan tam olarak birini girin. Alışta "
              "aracı kurum TL ister ve adedi işlem günü fiyatı yayımlanınca "
              "hesaplar; o durumda --tutar kullanın.", file=sys.stderr)
        return EXIT_ERROR
    if args.tutar is not None and satis_yonu:
        print("Hata: TL tutarıyla satış emri kabul edilmiyor. Adet bilinmeden "
              "beklemedeki satış rezervesi sayılamaz ve aynı paylar iki kez "
              "satılabilir; satış emrini adetle girin.", file=sys.stderr)
        return EXIT_ERROR
    if (args.units is not None and args.units <= 0) or (
            args.tutar is not None and args.tutar <= 0):
        print("Hata: Adet/tutar pozitif olmalı; yönü --sat/--al belirler.",
              file=sys.stderr)
        return EXIT_ERROR
    # Fiyat, TL'li emirde BOLENDIR: sifir cozulmeyi patlatir, negatif ise
    # adedi negatife cevirip alisi sessizce satisa dondururdu.
    if args.price is not None and args.price <= 0:
        print("Hata: --price pozitif olmalı.", file=sys.stderr)
        return EXIT_ERROR
    portfolio = _load_portfolio(args)
    try:
        code = storage.normalize_code(args.code)
        # Adet/tutar pozitifligi ve "tam olarak biri" kurali fonksiyonun
        # basinda dogrulandi; buraya None gelmis olamaz.
        emir_gunu = storage.parse_date(args.date)

        # Saat ZORUNLU (ya acik saat ya da kesim tarafı). "Herhalde erkendi"
        # varsayimi, bu araci en pahali sekilde yaniltan senaryonun kendisi:
        # kesim sonrasi verilen emir bir sonraki is gunune kayar ve
        # gerceklesme fiyati bir gun otelenir.
        kesim_sonrasi = None
        emir_zamani: datetime | date = emir_gunu
        if args.saat:
            try:
                saat = datetime.strptime(args.saat, "%H:%M").time()
            except ValueError:
                raise ValueError(f"Saat 'SS:DD' biçiminde olmalı: {args.saat!r}") from None
            emir_zamani = datetime.combine(emir_gunu, saat)
        elif args.kesim_sonrasi:
            kesim_sonrasi = True
        elif args.kesim_oncesi:
            kesim_sonrasi = False
        else:
            raise ValueError(
                "Emri saat kaçta verdiğinizi belirtin: --saat SS:DD, ya da "
                "--kesim-oncesi / --kesim-sonrasi. TEFAS'ta kesim 13:30; "
                "sonrasında verilen emir ertesi iş gününe kayar."
            )

        history, takvim, kural, _ = _valor_takvimi_ve_kural(code, args)
        satis = bool(args.sat)
        cozum = valor.cozumle(
            kural, takvim, emir_zamani, satis=satis, kesim_sonrasi=kesim_sonrasi
        )
    except (ValueError, valor.ValorHatasi) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    tur = "SATIŞ" if satis else "ALIŞ"
    miktar = (f"{fmt_units(args.units)} adet" if args.units is not None
              else f"{fmt_money(args.tutar)} (adet fiyat gelince)")
    print(f"\n{code} · {tur} · {miktar}")
    print(f"Emir: {emir_gunu:%d.%m.%Y}" + (f" {args.saat}" if args.saat else ""))
    for satir in cozum.anlat(satis=satis):
        print(f"  {satir}")

    # Mutabakat: nakit tarihi kullanicinin ekraninda GORDUGU tek olgudur ve
    # turetilen zincirin dogrulanabilir tek ucudur.
    if args.nakit:
        try:
            beklenen = storage.parse_date(args.nakit)
        except ValueError as exc:
            print(f"Hata: {exc}", file=sys.stderr)
            return EXIT_ERROR
        uyari = valor.nakit_uyusmazligi(cozum, beklenen)
        if uyari:
            print(f"\nUYUŞMAZLIK: {uyari}", file=sys.stderr)
            print("Emir kaydedilmedi. Emir saatini düzeltin (işlem gününü, "
                  "dolayısıyla fiyat gününü kaydırır) veya "
                  f"'{console.invocation()} valor {code}' ile fonun nakit "
                  "kuralını güncelleyin.", file=sys.stderr)
            return EXIT_ERROR
        print("  ✓ Nakit tarihi tutuyor — türetilen zincir doğrulandı.")

    try:
        yollar = config.yan_dosyalar(args.data_file)
        emirler = bekleyen.yukle(yollar["bekleyen"])
        if satis:
            bekleyen.satis_dogrula(portfolio, emirler, code, args.units)
        emir = bekleyen.emir_olustur(
            code,
            None if args.units is None else (-args.units if satis else args.units),
            cozum, emir_zamani, fiyat=args.price, tutar=args.tutar,
        )
        emirler.append(emir)
        yol = bekleyen.kaydet(emirler, yollar["bekleyen"])
    except bekleyen.BekleyenHatasi as exc:
        print(f"\nHata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"\nBekleyen emir kaydedildi: {yol}")
    if cozum.gerceklesme > history.latest_date:
        print(f"  {cozum.gerceklesme:%d.%m.%Y} değerleme fiyatı yayımlandığında "
              "işlem kaydına dönüşecek.")
        if args.tutar is not None:
            # Alista adet HENUZ YOK; "portfoyde kalir" demek yanlis olurdu.
            print(f"  Adet o gün {fmt_money(args.tutar)} ÷ kapanış fiyatı "
                  "olarak hesaplanacak; o güne kadar portföye girmez.")
        elif satis:
            print("  Adetler o güne kadar portföyde kalır — fiyat riski sizde.")
    return EXIT_OK


def cmd_bekleyen(args) -> int:
    """Bekleyen emirleri listeler; --sil ile iptal eder."""
    yollar = config.yan_dosyalar(args.data_file)
    try:
        emirler = bekleyen.yukle(yollar["bekleyen"])
    except bekleyen.BekleyenHatasi as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.sil:
        kalan = [e for e in emirler if e.id != args.sil]
        if len(kalan) == len(emirler):
            print(f"'{args.sil}' kimlikli bekleyen emir yok.", file=sys.stderr)
            return EXIT_ERROR
        bekleyen.kaydet(kalan, yollar["bekleyen"])
        print(f"Bekleyen emir silindi: {args.sil}")
        return EXIT_OK

    if not emirler:
        print("Bekleyen emir yok.")
        return EXIT_OK

    print(f"\n{len(emirler)} bekleyen emir\n")
    for emir in emirler:
        print(f"  {emir.id}  {emir.ozet()}")
        detay = f"         emir {emir.emir_zamani} · tarih {emir.tarih_kaynagi}"
        if emir.fiyat is not None:
            detay += f" · fiyat girildi {fmt_price(emir.fiyat)} ₺"
        print(detay)
        if emir.valor_supheli:
            print("         NOT: türetildiğinde valör kuralı henüz "
                  "doğrulanmamıştı; nakit tarihini aracı kurum ekranıyla "
                  "karşılaştırın.")
    print(f"\nİptal için: {console.invocation()} bekleyen --sil <kimlik>\n")
    return EXIT_OK


def cmd_valor(args) -> int:
    """Valor kurallarini gosterir ve duzenler."""
    valor_yolu = config.yan_dosyalar(args.data_file)["valor"]
    kurallar = valor.yukle(valor_yolu)

    if args.code is None:
        if not kurallar:
            print("Kayıtlı valör kuralı yok; kategori varsayılanları kullanılıyor.")
            print(f"Bir fonun kuralını görmek için: {console.invocation()} valor TMV")
            return EXIT_OK
        print("\nKayıtlı valör kuralları (iş günü)\n")
        for kod in sorted(kurallar):
            k = kurallar[kod]
            durum = "ŞÜPHELİ" if k.supheli else f"doğrulandı {k.dogrulandi:%d.%m.%Y}"
            print(f"  {kod:<5} alış T+{k.alis_valor}/nakit T+{k.alis_nakit}  "
                  f"satış T+{k.satis_valor}/nakit T+{k.satis_nakit}  "
                  f"[{k.kaynak}, {durum}]")
        print()
        return EXIT_OK

    try:
        code = storage.normalize_code(args.code)
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return EXIT_ERROR

    kural = kurallar.get(code)
    if kural is None:
        kategori = tefas_client.fetch_category(code)
        kural = valor.kategori_kurali(kategori)
        print(f"{code} için kayıtlı kural yok; "
              f"kategori varsayılanı kullanılıyor ({kategori or 'kategori bilinmiyor'}).")

    degisti = False
    for ad, yeni in (
        ("alis_valor", args.alis_valor), ("alis_nakit", args.alis_nakit),
        ("satis_valor", args.satis_valor), ("satis_nakit", args.satis_nakit),
    ):
        if yeni is not None:
            if not 0 <= yeni <= 10:
                print(f"Hata: {ad} 0-10 iş günü arasında olmalı.", file=sys.stderr)
                return EXIT_ERROR
            # `dogrulandi=None`: dogrulama ESKI rakamlar icindi. Korunsaydi
            # hic teyit edilmemis yeni rakam yesil "DOĞRULANDI" rozetiyle
            # gorunurdu - `supheli` mekanizmasinin tamami bu ayrim icin var.
            kural = dataclasses.replace(
                kural, **{ad: yeni}, kaynak="elle", dogrulandi=None
            )
            degisti = True

    if args.dogrula:
        kural = valor.dogrula(kural)
        degisti = True

    if degisti:
        kurallar[code] = kural
        yol = valor.kaydet(kurallar, valor_yolu)
        print(f"Kaydedildi: {yol}")

    durum = "ŞÜPHELİ — doğrulanmadı" if kural.supheli else f"doğrulandı {kural.dogrulandi:%d.%m.%Y}"
    print(f"\n{code} valör kuralı ({kural.kaynak}, {durum})")
    print(f"  Alış : T+{kural.alis_valor} fiyat · T+{kural.alis_nakit} nakit")
    print(f"  Satış: T+{kural.satis_valor} fiyat · T+{kural.satis_nakit} nakit")
    if kural.supheli:
        print(f"\n  Doğrulamak için önce bir emrin nakit tarihini karşılaştırın,")
        print(f"  sonra: {console.invocation()} valor {code} --dogrula\n")
    return EXIT_OK


def cmd_web(args) -> int:
    """Web arayuzunu baslatir.

    Bagimliliklar (fastapi, uvicorn, jinja2) opsiyoneldir: CLI'ı yalnizca
    terminalden kullanan birine web yigini kurdurmak dogru olmaz. Eksikse
    kurulum komutunu soyleyip cikiyoruz.
    """
    try:
        from .web import run
    except ImportError as exc:
        print(
            f"Web arayüzü için ek paketler gerekiyor ({exc.name}).\n"
            "  pip install 'portfoy-cli[web]'\n"
            "veya:\n"
            "  pip install fastapi uvicorn jinja2 python-multipart",
            file=sys.stderr,
        )
        return EXIT_ERROR

    adres = f"http://{args.host}:{args.port}"
    print(f"Portföy arayüzü: {adres}")
    if args.host in {"127.0.0.1", "localhost"}:
        print("Yalnızca bu bilgisayardan erişilebilir. Durdurmak için Ctrl+C.")
    else:
        # Baglanma adresini degistirmek bilincli bir karar olmali: arayuzde
        # kimlik dogrulama YOK, portfoyu goren herkes degistirebilir.
        # `run()` bu adresi izinli konak listesine ekler; eklemeseydi sunucu
        # calisir ama konak kalkani her istegi 403'lerdi.
        print(
            f"UYARI: {args.host} adresine bağlanılıyor. Arayüzde parola koruması "
            "yoktur;\n"
            "ağdaki herkes portföyü görebilir ve değiştirebilir.",
            file=sys.stderr,
        )

    if args.open:
        import threading
        import webbrowser

        # Sunucu ayaga kalkmadan acilan sekme bos sayfa gosterir; kisa gecikme
        # tek basina yeterli degil ama pratikte calisiyor ve basarisiz olursa
        # kullanici adresi zaten yukarida goruyor.
        threading.Timer(1.0, lambda: webbrowser.open(adres)).start()

    try:
        run(
            host=args.host,
            port=args.port,
            data_file=getattr(args, "web_data_file", None) or args.data_file,
            output_dir=args.output_dir,
        )
    except KeyboardInterrupt:
        print("\nArayüz durduruldu.")
    except OSError as exc:
        print(f"Sunucu başlatılamadı: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _track_record(analysis, args) -> list:
    """Takvim ayi karnesi; `--no-track` verildiyse bos liste."""
    if getattr(args, "no_track", False):
        return []
    return analytics.monthly_track_record(
        analysis,
        snapshots.load(config.yan_dosyalar(args.data_file)["gecmis"]),
        months=getattr(args, "months", config.TRACK_RECORD_MONTHS),
    )


def _produce_outputs(analysis, args, track_record: list | None = None) -> list[Path]:
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
                periods=tuple(args.period),
                track_record=track_record or [],
            )
        )

    return produced


def cmd_report(args) -> int:
    analysis = _collect_analysis(_load_portfolio(args), args)
    if analysis is None:
        return EXIT_ERROR

    # Karne bir kez hesaplanir; hem terminale hem grafige ayni liste gider.
    track_record = _track_record(analysis, args)
    console.render(analysis, track_record)

    produced = _produce_outputs(analysis, args, track_record)

    if produced:
        print("Oluşturulan dosyalar:")
        for item in produced:
            print(f"  • {item}")

    # Dosyalar yazildiktan SONRA e-posta: uretilen dosyalar eke gidiyor.
    # Gonderim basarisiz olsa bile rapor diske yazilmis durumda.
    mail_sent = _maybe_send_mail(analysis, produced, args, track_record)

    if not produced and not mail_sent:
        print("Hiçbir çıktı üretilmedi.", file=sys.stderr)
        return EXIT_ERROR

    return EXIT_OK


def _maybe_send_mail(analysis, attachments: list[Path], args, track_record=None) -> bool:
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
        recipients = mailer.send_report(analysis, attachments, settings, track_record)
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
    track_record = _track_record(analysis, args)
    produced = _produce_outputs(analysis, args, track_record)
    if produced:
        print("\nEklenen dosyalar:")
        for item in produced:
            print(f"  • {item.name}")

    try:
        recipients = mailer.send_report(analysis, produced, settings, track_record)
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

    # --- emir ---
    p_emir = subparsers.add_parser(
        "emir",
        help="Emir zamanından gerçekleşme gününü türetip bekleyen emir oluştur.",
    )
    p_emir.add_argument("code", help="Fon kodu")
    p_emir.add_argument("units", type=float, nargs="?",
                        help="Adet (pozitif; yön için --sat/--al). "
                             "Alışta bilinmiyorsa --tutar kullanın.")
    p_emir.add_argument("--tutar", type=float, metavar="TL",
                        help="Adet yerine TL tutarı (yalnızca alış). Adet, "
                             "işlem günü fiyatı yayımlanınca hesaplanır.")
    yon = p_emir.add_mutually_exclusive_group(required=True)
    yon.add_argument("--sat", action="store_true", help="Satış emri")
    yon.add_argument("--al", action="store_true", help="Alış emri")
    p_emir.add_argument("--tarih", dest="date", required=True,
                        metavar="GG.AA.YYYY", help="Emri verdiğiniz gün")
    p_emir.add_argument("--saat", metavar="SS:DD",
                        help="Emri verdiğiniz saat (TEFAS kesimi 13:30)")
    kesim = p_emir.add_mutually_exclusive_group()
    kesim.add_argument("--kesim-oncesi", action="store_true",
                       help="Saat bilinmiyorsa: emir 13:30'dan önce verildi")
    kesim.add_argument("--kesim-sonrasi", action="store_true",
                       help="Saat bilinmiyorsa: emir 13:30'dan sonra verildi")
    p_emir.add_argument("--nakit", metavar="GG.AA.YYYY",
                        help="Aracı kurumun gösterdiği nakit tarihi (mutabakat)")
    p_emir.add_argument("--price", type=float, metavar="FİYAT",
                        help="Gerçekleşen fiyat biliniyorsa; TEFAS beklenmez")
    p_emir.set_defaults(func=cmd_emir)

    # --- bekleyen ---
    p_bek = subparsers.add_parser("bekleyen", help="Bekleyen emirleri göster.")
    p_bek.add_argument("--sil", metavar="KİMLİK", help="Bekleyen emri iptal et")
    p_bek.set_defaults(func=cmd_bekleyen)

    # --- valor ---
    p_val = subparsers.add_parser("valor", help="Fon valör kurallarını göster/düzenle.")
    p_val.add_argument("code", nargs="?", help="Fon kodu (boşsa hepsi)")
    p_val.add_argument("--alis-valor", type=int, metavar="N")
    p_val.add_argument("--alis-nakit", type=int, metavar="N")
    p_val.add_argument("--satis-valor", type=int, metavar="N")
    p_val.add_argument("--satis-nakit", type=int, metavar="N")
    p_val.add_argument("--dogrula", action="store_true",
                       help="Kuralı doğrulanmış işaretle (artık şüpheli sayılmaz)")
    p_val.set_defaults(func=cmd_valor)

    # --- web ---
    p_web = subparsers.add_parser("web", help="Web arayüzünü başlat.")
    p_web.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bağlanılacak adres (varsayılan: 127.0.0.1 — yalnızca bu bilgisayar)",
    )
    p_web.add_argument(
        "--port", type=int, default=8000, help="Port (varsayılan: 8000)"
    )
    p_web.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Excel ve grafiklerin yazılacağı klasör (varsayılan: {config.OUTPUT_DIR})",
    )
    p_web.add_argument(
        "--open", action="store_true", help="Tarayıcıyı otomatik aç."
    )
    # `--data-file` genel bir bayrak ama `portfoy web --data-file X` yazmak
    # dogal geliyor; alt komutta da kabul edip genel degeri eziyoruz.
    p_web.add_argument(
        "--data-file",
        type=Path,
        default=None,
        metavar="PATH",
        dest="web_data_file",
        help="Portföy JSON dosyası (genel --data-file ile aynı işi görür).",
    )
    p_web.set_defaults(func=cmd_web)

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
        "--months",
        type=int,
        default=config.TRACK_RECORD_MONTHS,
        metavar="N",
        help=(
            "Takvim ayı karnesinde kaç kapanmış ay gösterilsin "
            f"(varsayılan: {config.TRACK_RECORD_MONTHS})"
        ),
    )
    fetch_flags.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Bu çalıştırmayı geçmişe kaydetme (takvim ayı karnesini beslemez).",
    )
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
        "--no-track", action="store_true", help="Takvim ayı karnesini atla."
    )
    report_flags.add_argument(
        "--theme", choices=("light", "dark"), default="light", help="Grafik teması."
    )
    report_flags.add_argument(
        "--period",
        nargs="+",
        choices=tuple(config.PERIODS),
        default=["haftalik", "aylik"],
        metavar="PERIYOT",
        help=(
            "Katkı ve değişim grafiklerinin periyotları; birden fazla verilebilir "
            "(varsayılan: haftalik aylik)."
        ),
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
