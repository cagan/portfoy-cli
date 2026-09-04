"""Zaman ağırlıklı getiride dış para akışının fiyatlanması."""

from __future__ import annotations

from datetime import date

import pytest

from portfoy import snapshots as sn
from portfoy.storage import Portfolio


def goruntu(gun: date, **fonlar) -> sn.Snapshot:
    """goruntu(date(...), TMV=(100, 10.0)) -> Snapshot"""
    return sn.Snapshot(gun, {k: sn.Holding(*v) for k, v in fonlar.items()})


def test_akis_yoksa_getiri_dogrudan_deger_farki():
    a = goruntu(date(2026, 1, 1), AAA=(100, 10.0))
    b = goruntu(date(2026, 1, 2), AAA=(100, 11.0))
    (dilim,) = sn.sub_period_returns([a, b])
    assert dilim.flow == 0
    assert dilim.ret == pytest.approx(0.10)


def test_alim_getiri_sayilmaz():
    a = goruntu(date(2026, 1, 1), AAA=(100, 10.0))
    b = goruntu(date(2026, 1, 2), AAA=(200, 10.0))
    (dilim,) = sn.sub_period_returns([a, b])
    assert dilim.flow == pytest.approx(1000.0)
    assert dilim.ret == pytest.approx(0.0)


def test_cikis_kayitli_satis_fiyatindan_fiyatlanir():
    """Asıl düzeltme: eskiden dönem başı fiyatı tahmin ediliyordu.

    100 adet, görüntüde 10,00'dan duruyor ama 12,00'ye satıldı. Aradaki
    2.000 TL gerçek bir kazanç; tahmin yolu onu yutuyordu.
    """
    pf = Portfolio()
    pf.add_lot("AAA", 1000, date(2026, 1, 1), 10.0)
    pf.add_lot("AAA", -1000, date(2026, 1, 2), 12.0)
    pf.add_lot("BBB", 1000, date(2026, 1, 1), 10.0)

    seri = [
        goruntu(date(2026, 1, 1), AAA=(1000, 10.0), BBB=(1000, 10.0)),
        goruntu(date(2026, 1, 2), AAA=(1000, 10.0), BBB=(1000, 10.0)),
        goruntu(date(2026, 1, 3), BBB=(1000, 10.0)),
    ]

    tahminle = sn.sub_period_returns(seri)[-1]
    kayitla = sn.sub_period_returns(seri, pf)[-1]

    assert tahminle.flow == pytest.approx(-10_000.0)
    assert tahminle.ret == pytest.approx(0.0)      # 2.000 TL kazanç yutuldu
    assert kayitla.flow == pytest.approx(-12_000.0)
    assert kayitla.ret == pytest.approx(0.10)


def test_ardisik_islem_gunlerinde_her_dilim_kayitli_fiyati_kullanir():
    """Regresyon: pencere iki ucundan kapalıyken ikinci dilim tahmine düşüyordu."""
    pf = Portfolio()
    pf.add_lot("BBB", 100, date(2026, 1, 2), 10.0)
    pf.add_lot("BBB", 50, date(2026, 1, 3), 20.0)

    seri = [
        goruntu(date(2026, 1, 1), BBB=(0.0001, 10.0)),
        goruntu(date(2026, 1, 2), BBB=(100.0001, 10.0)),
        goruntu(date(2026, 1, 3), BBB=(150.0001, 20.0)),
    ]
    d1, d2 = sn.sub_period_returns(seri, pf)
    assert d1.flow == pytest.approx(1000.0)
    assert d2.flow == pytest.approx(1000.0)      # 50 x 20, tahmin değil


def test_gorüntu_alindiktan_sonra_kaydedilen_satis_yakalanir():
    """Satış, önceki görüntünün tarihinde gerçekleşip sonra kaydedilebilir.

    Gerçek hayatta olan bu: rapor alındıktan sonra satış girildi. Yarı açık
    pencere bu lotu kaçırır; kapalı pencere yedeği yakalar.
    """
    pf = Portfolio()
    pf.add_lot("AAA", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("AAA", -100, date(2026, 1, 2), 13.0)

    seri = [
        goruntu(date(2026, 1, 2), AAA=(100, 12.0), BBB=(100, 10.0)),
        goruntu(date(2026, 1, 3), BBB=(100, 10.0)),
    ]
    (dilim,) = sn.sub_period_returns(seri, pf)
    assert dilim.flow == pytest.approx(-1300.0)


def test_dilim_icinde_tamamlanan_gidis_donus_gorulur():
    """Net adet farkı 0 ama arada gerçek bir akış var (sat, geri al)."""
    pf = Portfolio()
    pf.add_lot("CCC", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("CCC", -100, date(2026, 1, 4), 15.0)
    pf.add_lot("CCC", 100, date(2026, 1, 5), 11.0)

    seri = [
        goruntu(date(2026, 1, 3), CCC=(100, 14.0)),
        goruntu(date(2026, 1, 6), CCC=(100, 11.5)),
    ]
    (dilim,) = sn.sub_period_returns(seri, pf)
    assert dilim.flow == pytest.approx(-400.0)
    assert dilim.ret == pytest.approx((100 * 11.5 + 400) / (100 * 14.0) - 1)


def test_kayit_eksikse_tahmine_dusulur():
    """Yarım kayıtla hesaplanan akış, hiç kayıt olmamasından yanıltıcıdır."""
    pf = Portfolio()
    pf.add_lot("AAA", 40, date(2026, 1, 2), 10.0)   # gerçekte 100 alınmış

    seri = [
        goruntu(date(2026, 1, 1), AAA=(100, 10.0)),
        goruntu(date(2026, 1, 2), AAA=(200, 10.0)),
    ]
    (dilim,) = sn.sub_period_returns(seri, pf)
    assert dilim.flow == pytest.approx(1000.0)      # 100 x 10, 40 x 10 değil


def test_kapsanan_aylar_yarim_ayi_saymaz():
    seri = [goruntu(date(2026, 1, 15), A=(1, 1.0)), goruntu(date(2026, 2, 10), A=(1, 1.0))]
    assert sn.covered_months(seri) == set()

    seri = [goruntu(date(2026, 1, 31), A=(1, 1.0)), goruntu(date(2026, 3, 1), A=(1, 1.0))]
    assert sn.covered_months(seri) == {(2026, 2)}


def test_kapanan_fon_goruntuye_sifir_adetle_yazilir_ve_cift_sayilmaz():
    """`record` artık kapanmış satırları da yazıyor — akış bundan bozulmamalı.

    Adet 0 olduğu için toplam değer değişmez; satış, önceki görüntüdeki adetle
    arasındaki farktan zaten akışa dönüşüyordu. Kodun görüntüde 0 adetle YER
    ALMASI ile HİÇ OLMAMASI aynı delta'yı üretmeli, yoksa aynı satış iki kez
    sayılırdı.
    """
    pf = Portfolio()
    pf.add_lot("AAA", 1000, date(2026, 1, 1), 10.0)
    pf.add_lot("AAA", -1000, date(2026, 1, 3), 12.0)
    pf.add_lot("BBB", 1000, date(2026, 1, 1), 10.0)

    onceki = goruntu(date(2026, 1, 2), AAA=(1000, 10.0), BBB=(1000, 10.0))
    # Yeni davranış: kapanan fon 0 adetle yazılıyor.
    sifirli = goruntu(date(2026, 1, 3), AAA=(0.0, 12.0), BBB=(1000, 10.0))
    # Eski davranış: fon görüntüden tamamen düşüyordu.
    yoksayan = goruntu(date(2026, 1, 3), BBB=(1000, 10.0))

    (a,) = sn.sub_period_returns([onceki, sifirli], pf)
    (b,) = sn.sub_period_returns([onceki, yoksayan], pf)
    assert a.flow == pytest.approx(-12_000.0)
    assert a.flow == pytest.approx(b.flow)
    assert a.ret == pytest.approx(b.ret)
    assert sifirli.total_value == pytest.approx(yoksayan.total_value)


def test_tasfiye_gunu_goruntusu_yazilir(tmp_path):
    """Her şeyin satıldığı gün de kaydedilmeli — en büyük hareketin olduğu gün.

    Guard `analysis.rows`'a bakıyordu; tasfiye gününde o liste boş olur ve
    görüntü hiç yazılmazdı, dilim karneden kalıcı olarak düşerdi.
    """
    class SahteSatir:
        def __init__(self, code, units, price):
            self.code, self.units, self.price = code, units, price

    class SahteAnaliz:
        as_of = date(2026, 9, 3)
        rows: list = []                                   # açık pozisyon yok
        closed_rows = [SahteSatir("AAA", 0.0, 12.0)]
        all_rows = closed_rows

    yol = tmp_path / "gecmis.json"
    kayit = sn.record(SahteAnaliz(), yol)
    assert kayit is not None
    assert kayit.on == date(2026, 9, 3)
    assert kayit.units("AAA") == 0.0
    assert kayit.price("AAA") == pytest.approx(12.0)
    assert sn.load(yol)[0].on == date(2026, 9, 3)
