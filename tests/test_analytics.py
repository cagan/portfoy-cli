"""Ağırlıklı portföy getirisi ve TL karşılıkları."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from portfoy import analytics, config
from portfoy.storage import Portfolio
from portfoy.tefas_client import FundHistory


def gecmis(kod: str, fiyatlar: list[tuple[date, float]]) -> FundHistory:
    seri = pd.Series(
        [f for _, f in fiyatlar],
        index=pd.DatetimeIndex([pd.Timestamp(g) for g, _ in fiyatlar]),
    ).sort_index()
    return FundHistory(code=kod, title=kod, source="test", prices=seri)


@pytest.fixture
def ayrisan_portfoy():
    """Biri kazanan biri kaybeden iki fon — sapma burada ortaya çıkar.

    A: 1.000 adet, 100 → 110 (+%10), bitiş değeri 110.000
    B: 1.000 adet, 100 →  50 (-%50), bitiş değeri  50.000
    Başlangıç toplam 200.000, bitiş 160.000 → gerçek getiri -%20.
    """
    pf = Portfolio()
    pf.add_lot("AAA", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("BBB", 1000, date(2026, 1, 1), 100.0)
    gunler = [date(2026, 8, 31), date(2026, 9, 1)]
    histories = {
        "AAA": gecmis("AAA", [(gunler[0], 100.0), (gunler[1], 110.0)]),
        "BBB": gecmis("BBB", [(gunler[0], 100.0), (gunler[1], 50.0)]),
    }
    return analytics.analyze(pf, histories)


def test_agirlik_baslangic_degerine_gore(ayrisan_portfoy):
    """Regresyon: bitiş ağırlığı kazananı fazla sayıp getiriyi yukarı sapıtıyordu.

    Bitiş ağırlığıyla: %10 x (110/160) + (-%50) x (50/160) = -%8,75  ← yanlış
    Başlangıç ağırlığıyla: (160.000 - 200.000) / 200.000 = -%20     ← doğru
    """
    assert ayrisan_portfoy.weighted_returns["gunluk"] == pytest.approx(-20.0)


def test_yuzde_ve_tl_birebir_tutar(ayrisan_portfoy):
    """Arayüzde yan yana gösteriliyorlar; çelişmemeleri şart."""
    a = ayrisan_portfoy
    for periyot in ("gunluk", "haftalik", "aylik"):
        getiri, tl = a.weighted_returns.get(periyot), a.value_change(periyot)
        if getiri is None or tl is None:
            continue
        # Taban artik türetilmiyor, doğrudan `period_base` okunuyor: eski
        # "değer - değişim" türetmesi adedin pencere boyunca sabit kaldığını
        # varsayıyordu ve kapanmış pozisyonda çöküyordu.
        baslangic = sum(
            row.period_base(periyot)
            for row in a.all_rows
            if row.period_base(periyot) is not None
        )
        assert getiri == pytest.approx(tl / baslangic * 100.0)
        assert (getiri >= 0) == (tl >= 0), f"{periyot}: işaret çelişkisi"


def test_tek_fonda_agirliklama_farketmez():
    """Tek fonda iki yöntem aynı sonucu verir — regresyon güvencesi."""
    pf = Portfolio()
    pf.add_lot("AAA", 100, date(2026, 1, 1), 10.0)
    a = analytics.analyze(pf, {"AAA": gecmis("AAA", [
        (date(2026, 8, 31), 10.0), (date(2026, 9, 1), 11.0)])})
    assert a.weighted_returns["gunluk"] == pytest.approx(10.0)
    assert a.value_change("gunluk") == pytest.approx(100.0)


def test_fon_bazli_tl_degisimi():
    pf = Portfolio()
    pf.add_lot("AAA", 1000, date(2026, 1, 1), 100.0)
    a = analytics.analyze(pf, {"AAA": gecmis("AAA", [
        (date(2026, 8, 31), 100.0), (date(2026, 9, 1), 110.0)])})
    satir = a.rows[0]
    assert satir.value == pytest.approx(110_000)
    assert satir.value_change("gunluk") == pytest.approx(10_000)


def test_verisi_eksik_fon_disarida_kalir():
    """Kapsam dışı fon hem paydan hem paydadan çıkar."""
    pf = Portfolio()
    pf.add_lot("AAA", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("YOK", 500, date(2026, 1, 1), 10.0)
    a = analytics.analyze(
        pf,
        {"AAA": gecmis("AAA", [(date(2026, 8, 31), 100.0), (date(2026, 9, 1), 110.0)])},
        {"YOK": "fiyat verisi yok"},
    )
    assert a.weighted_returns["gunluk"] == pytest.approx(10.0)
    assert a.coverage["gunluk"] == pytest.approx(1.0)   # kalan tek fon


def test_bos_portfoy_getiri_uretmez():
    a = analytics.analyze(Portfolio(), {})
    assert a.weighted_returns.get("gunluk") is None
    assert a.value_change("gunluk") is None


# --------------------------------------------------------------------------
# Akış düzeltmeli getiri: pencere içinde kapanan/açılan pozisyonlar
# --------------------------------------------------------------------------
GUNLER = [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]


@pytest.fixture
def satilan_fonlu_portfoy():
    """Gerçek vakanın küçültülmüş hâli: bir fon pencere içinde tamamen satıldı.

    KAL: 1.000 adet, 100 → 101 → 102 → 103  (hep elde)
    SAT: 1.000 adet, 100 → 100 → 100 →  90  (son gün %10 düşüp satıldı)

    Günlük pencere (02.09 → 03.09):
      KAL 1.000 x (103-102) = +1.000
      SAT 1.000 x ( 90-100) = -10.000   ← satılmış olması bunu silmez
      taban = 1.000x102 + 1.000x100 = 202.000 → -9.000 / 202.000 = -%4,455
    """
    pf = Portfolio()
    pf.add_lot("KAL", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("SAT", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("SAT", -1000, date(2026, 9, 3), 90.0)
    histories = {
        "KAL": gecmis("KAL", list(zip(GUNLER, [100.0, 101.0, 102.0, 103.0]))),
        "SAT": gecmis("SAT", list(zip(GUNLER, [100.0, 100.0, 100.0, 90.0]))),
    }
    return analytics.analyze(pf, histories)


def test_kapanan_pozisyon_gunluk_getiriye_katilir(satilan_fonlu_portfoy):
    """Asıl düzeltme: satılan fon tablodan düşüyor ama getiriden düşmüyor.

    Eskiden yalnızca açık pozisyonlara bakılıyordu; SAT yok sayılınca günlük
    getiri +%0,98 (yalnız KAL) çıkıyor, portföyün gerçekte yaşadığı -%4,46
    kayboluyordu (survivorship bias).
    """
    a = satilan_fonlu_portfoy
    assert [row.code for row in a.rows] == ["KAL"]          # tablo değişmedi
    assert [row.code for row in a.closed_rows] == ["SAT"]
    assert a.closed_rows[0].units == 0
    assert a.closed_rows[0].closed_on == date(2026, 9, 3)

    assert a.value_change("gunluk") == pytest.approx(-9_000.0)
    assert a.weighted_returns["gunluk"] == pytest.approx(-9_000 / 202_000 * 100)


def test_kapanan_pozisyonun_periyot_istatistikleri_dolu(satilan_fonlu_portfoy):
    """Adet 0 ama satırın kendi TL değişimi ve tabanı hesaplanabilir olmalı."""
    kapanan = satilan_fonlu_portfoy.closed_rows[0]
    assert kapanan.value_change("gunluk") == pytest.approx(-10_000.0)
    assert kapanan.period_base("gunluk") == pytest.approx(100_000.0)


def test_toplam_deger_kapanandan_etkilenmez(satilan_fonlu_portfoy):
    """`total_value` hâlâ 'fonlardaki değer' — satıştan çıkan nakit modellenmez."""
    assert satilan_fonlu_portfoy.total_value == pytest.approx(103_000.0)


def test_tl_ve_yuzde_kapanan_pozisyonla_da_ayni_tabandan_cikar(satilan_fonlu_portfoy):
    """Korunması gereken invaryant: pay ve payda tek bir hesaptan gelir."""
    a = satilan_fonlu_portfoy
    for periyot in ("gunluk", "haftalik", "aylik"):
        getiri, tl = a.weighted_returns.get(periyot), a.value_change(periyot)
        if getiri is None or tl is None:
            continue
        taban = sum(
            row.period_base(periyot)
            for row in a.all_rows
            if row.period_base(periyot) is not None
        )
        assert getiri == pytest.approx(tl / taban * 100.0)
        assert (getiri >= 0) == (tl >= 0), f"{periyot}: işaret çelişkisi"


def test_pencere_icinde_alinan_fon_tabana_girmez_kazanci_girer():
    """Alış günü elde olmayan fonun tabanı 0'dır ama sonraki günler sayılır.

    YENI 02.09'da alınıyor: 02.09'un hareketi ona ait değil (alış o günün
    fiyatından), 03.09'unki ait.
    """
    # Haftalık pencerenin tabanı (03.09 - 7 gün = 27.08) seride BULUNMALI;
    # 4 günlük seride o pencere hiç oluşmaz ve test sessizce boşa çıkardı.
    uzun = [date(2026, 8, g) for g in (25, 26, 27, 28, 31)] + [
        date(2026, 9, g) for g in (1, 2, 3)
    ]
    pf = Portfolio()
    pf.add_lot("ESKI", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("YENI", 1000, date(2026, 9, 2), 50.0)
    a = analytics.analyze(pf, {
        "ESKI": gecmis("ESKI", list(zip(uzun, [100.0] * 8))),
        "YENI": gecmis("YENI", list(zip(uzun, [50.0] * 7 + [55.0]))),
    })
    yeni = next(row for row in a.rows if row.code == "YENI")
    assert yeni.period_base("gunluk") == pytest.approx(50_000.0)  # 02.09'da elde
    assert yeni.value_change("gunluk") == pytest.approx(5_000.0)

    # Haftalık pencere 31.08'de başlıyor: o gün YENI henüz elde değildi.
    assert yeni.period_base("haftalik") == pytest.approx(0.0)
    assert yeni.value_change("haftalik") == pytest.approx(5_000.0)
    assert a.weighted_returns["haftalik"] == pytest.approx(5_000 / 100_000 * 100)


def test_fiyatsiz_lotlar_hesabi_bozmaz():
    """Eski kayıtlarda fiyat yok; hesap yalnızca adet ve TEFAS serisini kullanır."""
    pf = Portfolio()
    pf.add_lot("ESKI", 1000)                       # tarihsiz, fiyatsız
    pf.add_lot("ESKI", 500, date(2026, 9, 2))      # tarihli ama fiyatsız
    a = analytics.analyze(pf, {
        "ESKI": gecmis("ESKI", list(zip(GUNLER, [100.0, 100.0, 100.0, 110.0]))),
    })
    satir = a.rows[0]
    # 03.09 başında 1.500 adet elde: 1.500 x 10 TL = 15.000
    assert satir.value_change("gunluk") == pytest.approx(15_000.0)
    assert satir.period_base("gunluk") == pytest.approx(150_000.0)
    assert a.weighted_returns["gunluk"] == pytest.approx(10.0)


def test_akis_notu_yon_ve_tutari_verir(satilan_fonlu_portfoy):
    a = satilan_fonlu_portfoy
    assert a.net_flow("gunluk") == pytest.approx(-90_000.0)
    assert a.flow_note("gunluk") == "90.000,00 ₺ çıkış (SAT satışı)"
    assert a.closed_note == "SAT 03.09'da kapandı — getiri ve TL değişimine dahil"


def test_akis_notu_alis_ve_satisi_netler():
    """Aynı pencerede hem alış hem satış varsa rakam NET olduğunu söylemeli."""
    pf = Portfolio()
    pf.add_lot("SAT", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("SAT", -1000, date(2026, 9, 3), 90.0)
    pf.add_lot("AL", 100, date(2026, 9, 3), 100.0)
    a = analytics.analyze(pf, {
        "SAT": gecmis("SAT", list(zip(GUNLER, [100.0, 100.0, 100.0, 90.0]))),
        "AL": gecmis("AL", list(zip(GUNLER, [100.0, 100.0, 100.0, 100.0]))),
    })
    assert a.net_flow("gunluk") == pytest.approx(-80_000.0)
    assert a.flow_note("gunluk") == "net 80.000,00 ₺ çıkış (SAT satışı, AL alışı)"


def test_akis_yoksa_not_yok(ayrisan_portfoy):
    """0,00 ₺ yazmakla 'akış yok' demek arasındaki fark arayüzde önemli."""
    assert ayrisan_portfoy.net_flow("gunluk") is None
    assert ayrisan_portfoy.flow_note("gunluk") is None
    assert ayrisan_portfoy.closed_note is None


def test_cok_once_kapanan_fon_hesaba_girmez():
    """Fiyatı çekilmeyen (lookback dışı) kapanmış fon analize hiç girmez.

    `codes_for_pricing` onu seçmediği için `histories`te yoktur; analiz de
    sessizce atlamalı, hata üretmemeli.
    """
    pf = Portfolio()
    pf.add_lot("KAL", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("COKESKI", 500, date(2020, 1, 1), 10.0)
    pf.add_lot("COKESKI", -500, date(2020, 6, 1), 12.0)
    a = analytics.analyze(pf, {
        "KAL": gecmis("KAL", list(zip(GUNLER, [100.0, 100.0, 100.0, 110.0]))),
    })
    assert a.closed_rows == []
    assert a.weighted_returns["gunluk"] == pytest.approx(10.0)
    assert a.value_change("gunluk") == pytest.approx(10_000.0)


def test_serisi_erken_biten_fonun_fiyatsiz_lotu_akisa_uydurulmaz():
    """Bayat fiyatla akış uydurma: lot, fonun kendi serisinin sonunu aşarsa atlanır.

    Pencerenin bitiş günü EN UZUN seriden okunuyor. O gün fiyat açıklamamış bir
    fonun serisi daha erken biter; `_price_asof(allow_last=True)` yine de bir
    şey döndürür ama o fiyat lotun gününe ait DEĞİLDİR.
    """
    pf = Portfolio()
    pf.add_lot("UZUN", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("KISA", 1000, date(2026, 1, 1), 10.0)
    pf.add_lot("KISA", 500, date(2026, 9, 3))          # fiyatsız, seri dışı gün
    a = analytics.analyze(pf, {
        "UZUN": gecmis("UZUN", list(zip(GUNLER, [100.0, 100.0, 100.0, 100.0]))),
        # KISA serisi 02.09'da bitiyor: 03.09'un fiyatı bilinmiyor.
        "KISA": gecmis("KISA", list(zip(GUNLER[:3], [10.0, 10.0, 10.0]))),
    })
    assert a.net_flow("gunluk") is None
    assert a.flow_note("gunluk") is None


def test_seri_icindeki_fiyatsiz_lot_o_gunun_fiyatiyla_akisa_girer():
    """Sınırın diğer tarafı: lot serinin içindeyse fiyata düşmek doğru."""
    pf = Portfolio()
    pf.add_lot("AAA", 1000, date(2026, 1, 1), 10.0)
    pf.add_lot("AAA", 500, date(2026, 9, 3))           # fiyatsız ama seri içinde
    a = analytics.analyze(pf, {
        "AAA": gecmis("AAA", list(zip(GUNLER, [10.0, 10.0, 10.0, 12.0]))),
    })
    assert a.net_flow("gunluk") == pytest.approx(6_000.0)   # 500 x 12
    assert a.flow_note("gunluk") == "6.000,00 ₺ giriş (AAA alışı)"


# --------------------------------------------------------------------------
# Katkı: özetle aynı kesirden çıkmalı
# --------------------------------------------------------------------------
def test_katki_toplami_agirlikli_getiriye_esit(satilan_fonlu_portfoy):
    """Regresyon: katkı `ağırlık × getiri` iken özetle ÇELİŞİYORDU.

    Eski hesap bugünkü ağırlığı kullanıyor ve yalnız açık pozisyonları
    görüyordu — bu modülün geri kalanında reddedilen formülün ta kendisi.
    Gerçek portföyde günlük katkı toplamı +%0,77, özet ise -%0,04 diyordu:
    işaret bile tersti. `charts.contribution_chart` bu toplamı altyazıda
    "ağırlıklı getiri" diye basıyordu.
    """
    a = satilan_fonlu_portfoy
    for periyot in ("gunluk", "haftalik", "aylik"):
        ozet = a.weighted_returns.get(periyot)
        if ozet is None:
            continue
        toplam = sum(
            row.contributions[periyot]
            for row in a.all_rows
            if row.contributions.get(periyot) is not None
        )
        assert toplam == pytest.approx(ozet), f"{periyot}: katkı toplamı özeti tutmuyor"


def test_kapanan_fonun_katkisi_hesaba_giriyor(satilan_fonlu_portfoy):
    """Kapanan fon katkı listesinde OLMALI, yoksa şelale toplamı tutmaz."""
    kapanan = satilan_fonlu_portfoy.closed_rows[0]
    # -10.000 / 202.000 x 100
    assert kapanan.contributions["gunluk"] == pytest.approx(-10_000 / 202_000 * 100)


# --------------------------------------------------------------------------
# Kapsam: kapanmış satırın eksik verisi sessizce yutulmamalı
# --------------------------------------------------------------------------
def test_serisi_taban_gunune_ulasmayan_kapanmis_fon_kapsami_dusurur():
    """Regresyon: kapanmış satırın bugünkü değeri 0 olduğu için kapsam 1,0 kalıyordu.

    Sonuç: gerçekleşmiş bir kayıp "0,00 ₺ / %0,00" olarak, üstelik "kapsam tam"
    damgasıyla raporlanıyor, konsoldaki uyarı hiç tetiklenmiyordu.
    """
    pf = Portfolio()
    pf.add_lot("KAL", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("SAT", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("SAT", -1000, date(2026, 9, 3), 90.0)
    a = analytics.analyze(pf, {
        "KAL": gecmis("KAL", list(zip(GUNLER, [100.0, 100.0, 100.0, 100.0]))),
        # SAT serisi 03.09'da BAŞLIYOR: günlük pencerenin tabanı (02.09) yok.
        "SAT": gecmis("SAT", [(GUNLER[3], 90.0)]),
    })
    assert a.uncovered_codes("gunluk") == ["SAT"]
    # Vekil taban 1.000 x 90 = 90.000; kapsam 100.000 / 190.000
    assert a.coverage["gunluk"] == pytest.approx(100_000 / 190_000)
    assert a.coverage["gunluk"] < 0.999          # konsol uyarısı tetiklenir


def test_kapsam_paydasi_tek_bir_tarihin_parasi():
    """Kapsanan taban ile vekil değer AYNI günün TL'siyle ifade edilmeli.

    Eskiden `baslangic` taban günü TL'si, `eksik` ise BUGÜNKÜ TL idi; aradaki
    fark tam da ölçmeye çalıştığımız getiriydi ve kapsam oranı iki farklı
    tarihin parasını topluyordu.

    KISA 20.08'de başlıyor, 50 → 200 (dört kat). Aylık pencerenin tabanı
    (03.08) serisinde yok, yani tabanı hesaplanamıyor:
      eski vekil (bugünkü değer) : 1.000 x 200 = 200.000
      yeni vekil (taban günü)    : 1.000 x  50 =  50.000
    """
    is_gunleri = pd.bdate_range("2026-08-03", "2026-09-03").date.tolist()
    pf = Portfolio()
    pf.add_lot("KAL", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("KISA", 1000, date(2026, 1, 1), 100.0)
    kisa_gunler = [g for g in is_gunleri if g >= date(2026, 8, 20)]
    a = analytics.analyze(pf, {
        "KAL": gecmis("KAL", [(g, 100.0) for g in is_gunleri]),
        "KISA": gecmis("KISA", [
            (g, 50.0 if i == 0 else 200.0) for i, g in enumerate(kisa_gunler)
        ]),
    })
    assert a.uncovered_codes("aylik") == ["KISA"]
    # 100.000 / (100.000 + 50.000) — bugünkü değerle 100.000/300.000 olurdu.
    assert a.coverage["aylik"] == pytest.approx(100_000 / 150_000)


# --------------------------------------------------------------------------
# Pencere takvimi ve kapanmış satır filtresi
# --------------------------------------------------------------------------
def test_pencere_en_guncel_seriden_kurulur():
    """Regresyon: `max(key=len)` en UZUN seriyi seçiyordu, en GÜNCEL'i değil.

    Yayın yapmayı bırakmış uzun serili bir fon pencereyi geçmişe çekiyordu:
    başlıkta 03.09 yazarken hesap 22.08 penceresini ölçüyordu.
    """
    uzun_ama_bayat = [date(2026, 8, d) for d in (17, 18, 19, 20, 21, 24, 25)]
    pf = Portfolio()
    pf.add_lot("BAYAT", 1000, date(2026, 1, 1), 10.0)
    pf.add_lot("GUNCEL", 1000, date(2026, 1, 1), 10.0)
    a = analytics.analyze(pf, {
        "BAYAT": gecmis("BAYAT", list(zip(uzun_ama_bayat, [10.0] * 7))),
        "GUNCEL": gecmis("GUNCEL", list(zip(GUNLER, [10.0, 10.0, 10.0, 11.0]))),
    })
    assert a.windows["gunluk"] == (date(2026, 9, 2), date(2026, 9, 3))


def test_cok_once_kapanan_fon_kapanis_notunda_yer_almaz():
    """Pencerelerin hiçbirine değmeyen kapanış, "getiriye dahil" diye duyurulmaz.

    Katkısı zaten sıfır; listede kalması doğru ama alakasız bir gürültü olurdu.
    """
    pf = Portfolio()
    pf.add_lot("KAL", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("ESKI", 500, date(2026, 1, 1), 10.0)
    pf.add_lot("ESKI", -500, date(2026, 7, 1), 12.0)   # aylık pencereden önce
    a = analytics.analyze(pf, {
        "KAL": gecmis("KAL", list(zip(GUNLER, [100.0, 100.0, 100.0, 110.0]))),
        "ESKI": gecmis("ESKI", list(zip(GUNLER, [12.0, 12.0, 12.0, 12.0]))),
    })
    assert a.closed_rows == []
    assert a.closed_note is None
    assert a.weighted_returns["gunluk"] == pytest.approx(10.0)


def test_kapanan_ve_holding_tl_toplami_ozeti_tutar(satilan_fonlu_portfoy):
    """Tablo satırlarının TL'si özetteki değişimi tutturmalı.

    Arayüzler tabloyu holding + "Kapanan" satırı olarak iki parçada gösteriyor;
    ikisinin toplamı `value_change` etmezse kolonu toplayan kullanıcı başlıktaki
    rakama varamaz.
    """
    a = satilan_fonlu_portfoy
    for periyot in ("gunluk", "haftalik", "aylik"):
        toplam = a.value_change(periyot)
        if toplam is None:
            continue
        holdingler = sum(
            row.value_change(periyot)
            for row in a.rows
            if row.value_change(periyot) is not None
        )
        kapanan = a.closed_value_change(periyot) or 0.0
        assert holdingler + kapanan == pytest.approx(toplam)


def test_konsol_tablosu_kapanan_satirini_basar(satilan_fonlu_portfoy, capsys, monkeypatch):
    """Konsol çıktısında kapanan satır GÖRÜNMELİ, TL'siyle birlikte.

    Düz metin yolu zorlanıyor: rich yolu terminal genişliğine göre kırpıyor ve
    test dar terminalde etikete değil kırpılmış metne bakmış olurdu.
    """
    from portfoy import console as konsol

    monkeypatch.setattr(konsol, "_RICH", False)
    konsol.render(satilan_fonlu_portfoy)
    cikti = capsys.readouterr().out

    assert "Kapanan" in cikti
    assert "SAT (03.09)" in cikti
    assert "-10.000,00 ₺" in cikti      # kapanan satırın günlük TL'si

    # SAT holding satırı OLARAK listelenmemeli: tablo gövdesi başlık ile
    # "Kapanan" satırı arasındaki kısım.
    govde = cikti.split("Aylık")[1].split("Kapanan")[0]
    assert "KAL" in govde and "SAT" not in govde


# --------------------------------------------------------------------------
# "%" ile "₺" ayrışması: işaretleme
# --------------------------------------------------------------------------
def test_akis_isareti_yalniz_islem_olan_periyotta_cikar():
    """İşaretin kuralı: fonun O PENCEREDE tarihli bir lotu var mı.

    Dönem içinde işlem yoksa yüzde (birim fiyat getirisi) ile ₺ (portföye
    yansıyan kazanç) birebir tutar; varsa ayrışabilirler ve hücre işaretlenir.
    """
    pf = Portfolio()
    pf.add_lot("DURAN", 1000, date(2026, 1, 1), 100.0)      # pencerede işlem yok
    pf.add_lot("ALAN", 1000, date(2026, 1, 1), 100.0)
    pf.add_lot("ALAN", 500, date(2026, 9, 3), 100.0)        # günlük pencerede
    a = analytics.analyze(pf, {
        "DURAN": gecmis("DURAN", list(zip(GUNLER, [100.0, 100.0, 100.0, 110.0]))),
        "ALAN": gecmis("ALAN", list(zip(GUNLER, [100.0, 100.0, 100.0, 110.0]))),
    })
    duran = next(r for r in a.rows if r.code == "DURAN")
    alan = next(r for r in a.rows if r.code == "ALAN")

    assert not duran.has_flow("gunluk")
    assert duran.flow_mark("gunluk") == ""
    assert alan.has_flow("gunluk")
    assert alan.flow_mark("gunluk") == config.FLOW_MARK

    # İşaretsiz hücrede yüzde ile ₺/taban birebir tutmalı — işaretin anlamı bu.
    assert duran.returns["gunluk"] == pytest.approx(
        duran.value_change("gunluk") / duran.period_base("gunluk") * 100.0
    )


def test_akis_isareti_kismi_yildiziyla_cakismaz():
    """`*` ve `†` ayrı şeyler söyler; aynı hücrede birlikte durabilmeliler."""
    # Seri haftalık pencerenin tabanını (27.08) KAPSAMALI; 4 günlük seride o
    # pencere hiç kurulmaz ve test sessizce boşa çıkardı.
    gunler = pd.bdate_range("2026-08-20", "2026-09-03").date.tolist()
    pf = Portfolio()
    pf.add_lot("YENI", 1000, date(2026, 9, 2), 100.0)   # haftalık pencere içinde
    a = analytics.analyze(pf, {
        "YENI": gecmis("YENI", [
            (g, 110.0 if g == date(2026, 9, 3) else 100.0) for g in gunler
        ]),
    })
    satir = a.rows[0]
    assert satir.is_partial("haftalik")     # periyodun tamamında elde değildi
    assert satir.has_flow("haftalik")       # ve pencerede alım var
    assert config.FLOW_MARK != "*"


def test_kolon_aciklamasi_konsolda_basilir(satilan_fonlu_portfoy, capsys, monkeypatch):
    """Açıklama satırı olmadan ayrışma "tutarsızlık" gibi okunuyor."""
    from portfoy import console as konsol

    monkeypatch.setattr(konsol, "_RICH", False)
    konsol.render(satilan_fonlu_portfoy)
    cikti = capsys.readouterr().out

    assert config.COLUMN_BASIS_NOTE in cikti
    assert config.TOTAL_BASIS_NOTE in cikti
    # Fikstürde SAT 03.09'da satılıyor -> akış var -> işaret açıklaması da basılmalı
    assert config.FLOW_MARK_NOTE in cikti


def test_akis_yokken_isaret_aciklamasi_basilmaz(ayrisan_portfoy, capsys, monkeypatch):
    """Gereksiz not gürültüdür: hiç işaret yoksa açıklaması da çıkmasın."""
    from portfoy import console as konsol

    monkeypatch.setattr(konsol, "_RICH", False)
    konsol.render(ayrisan_portfoy)
    cikti = capsys.readouterr().out

    assert config.COLUMN_BASIS_NOTE in cikti      # bu her zaman basılır
    assert config.FLOW_MARK_NOTE not in cikti     # ama işaret açıklaması hayır
