"""FIFO eşleştirme, kapanmış pozisyonlar ve gerçekleşmiş kâr/zarar."""

from __future__ import annotations

from datetime import date

import pytest

from portfoy import storage
from portfoy.storage import Portfolio


# --- FIFO ------------------------------------------------------------------
def test_kismi_satis_en_eski_alisi_kapatir():
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", 100, date(2026, 2, 1), 20.0)
    pf.add_lot("XXX", -150, date(2026, 3, 1), 30.0)

    pos = pf.position("XXX")
    assert pos.units == 50
    assert not pos.is_closed
    # Elde kalan 50 adet YENİ lottan; maliyeti 20 TL, 10 değil.
    assert pos.cost_basis == pytest.approx(50 * 20.0)
    assert pos.realized_profit == pytest.approx(100 * (30 - 10) + 50 * (30 - 20))


def test_bir_satis_iki_alis_lotunu_kesebilir():
    pf = Portfolio()
    pf.add_lot("XXX", 40, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", 60, date(2026, 1, 5), 12.0)
    pf.add_lot("XXX", -70, date(2026, 2, 1), 15.0)

    kapanan = pf.position("XXX").closed_lots()
    assert [(adet, alis.price) for alis, _, adet in kapanan] == [(40, 10.0), (30, 12.0)]


def test_tarihsiz_lot_en_eski_sayilir():
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 5, 1), 20.0)
    pf.add_lot("XXX", 50, None, 10.0)          # tarihsiz: ilk alış varsayılır
    pf.add_lot("XXX", -50, date(2026, 6, 1), 30.0)

    pos = pf.position("XXX")
    assert pos.realized_profit == pytest.approx(50 * (30 - 10))
    assert pos.cost_basis == pytest.approx(100 * 20.0)


def test_float_artigi_hayalet_lot_birakmaz():
    pf = Portfolio()
    pf.add_lot("XXX", 0.1, date(2026, 1, 1), 1.0)
    pf.add_lot("XXX", 0.2, date(2026, 1, 2), 1.0)
    pf.add_lot("XXX", -0.3, date(2026, 1, 3), 1.0)
    assert pf.position("XXX").is_closed
    assert pf.position("XXX").remaining_lots() == []


def test_kapanis_sonrasi_yeniden_alis_yalniz_yeni_lotu_gorur():
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", -100, date(2026, 3, 1), 12.0)
    pf.add_lot("XXX", 30, date(2026, 5, 1), 9.0)

    pos = pf.position("XXX")
    assert pos.acquired_on == date(2026, 5, 1)
    assert pos.cost_basis == pytest.approx(270.0)
    assert pos.realized_profit == pytest.approx(200.0)   # eski tur korunuyor


# --- Kapanmış pozisyonlar --------------------------------------------------
def test_tamami_satilan_fon_kayittan_silinmez():
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", -100, date(2026, 3, 1), 12.0)

    assert pf.codes == []                 # rapora ve TEFAS çekimine girmez
    assert pf.all_codes == ["XXX"]        # ama kaydı durur
    assert pf.closed_codes == ["XXX"]
    assert pf.is_empty()
    assert pf.position("XXX").closed_on == date(2026, 3, 1)


def test_kapanmis_pozisyon_diske_yazilip_geri_okunur(portfoy_dosyasi):
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", -100, date(2026, 3, 1), 12.0)
    storage.save(pf, portfoy_dosyasi)

    geri = storage.load(portfoy_dosyasi)
    assert geri.closed_codes == ["XXX"]
    assert len(geri.position("XXX").lots) == 2
    assert geri.position("XXX").realized_profit == pytest.approx(200.0)


def test_set_units_kapanmis_pozisyonu_ezemez():
    """Regresyon: eskiden gerçekleşmiş kâr/zararı sessizce siliyordu."""
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", -100, date(2026, 3, 1), 12.0)

    with pytest.raises(ValueError, match="--accumulate"):
        pf.set_units("XXX", 30, date(2026, 5, 1), 9.0)

    assert pf.position("XXX").realized_profit == pytest.approx(200.0)


def test_hasilat_maliyet_ve_kar_ayni_kumeden():
    pf = Portfolio()
    pf.add_lot("XXX", 100, date(2026, 1, 1), 10.0)
    pf.add_lot("XXX", -100, date(2026, 3, 1), 12.0)

    pos = pf.position("XXX")
    assert pos.realized_proceeds - pos.realized_cost == pytest.approx(pos.realized_profit)


def test_eksik_fiyatta_kar_none_doner():
    """Eksik veride 0 dönmek 'ne kâr ne zarar' diye okunurdu."""
    pf = Portfolio()
    pf.add_lot("XXX", 100, None, None)       # fiyatı bilinmeyen eski alış
    pf.add_lot("XXX", -100, date(2026, 3, 1), 12.0)

    pos = pf.position("XXX")
    assert pos.realized_profit is None
    assert pos.realized_proceeds == pytest.approx(1200.0)


# --- Dosya biçimi ----------------------------------------------------------
@pytest.mark.parametrize(
    "kayit",
    [40631, {"adet": 40631}, {"islemler": [{"adet": 40631}]}],
    ids=["v1-cıplak", "v1-sozluk", "v2-lotlar"],
)
def test_eski_bicimler_okunabiliyor(kayit):
    pf = Portfolio.from_dict({"fonlar": {"TMV": kayit}})
    assert pf.codes == ["TMV"]
    assert pf.position("TMV").units == 40631


def test_satissiz_sifir_adet_hayalet_kapanmis_pozisyon_uretmez():
    pf = Portfolio.from_dict({"fonlar": {"HAYALET": 0, "GERCEK": 100}})
    assert "HAYALET" not in pf.positions
    assert pf.codes == ["GERCEK"]
    assert pf.closed_codes == []


def test_negatif_net_adet_uyariyla_dusurulur(caplog):
    pf = Portfolio.from_dict(
        {"fonlar": {"BOZUK": {"islemler": [{"adet": 10}, {"adet": -50}]}}}
    )
    assert "BOZUK" not in pf.positions
    assert "net adet negatif" in caplog.text


def test_elde_olmayan_satilamaz():
    pf = Portfolio()
    pf.add_lot("XXX", 10, date(2026, 1, 1), 1.0)
    with pytest.raises(ValueError, match="dusulemez"):
        pf.add_lot("XXX", -50, date(2026, 2, 1), 1.0)


# --- Fiyat çekilecek kodlar -------------------------------------------------
def test_fiyat_cekimi_yakin_kapanmisi_da_kapsar():
    """Pencere içinde satılan fonun fiyat serisi getiri için gerekli.

    `codes` (elde ne var) ve `all_codes` (kayıtta ne var) anlamları değişmedi;
    `codes_for_pricing` üçüncü soruyu, "hangi serilere ihtiyacım var"ı cevaplar.
    """
    pf = Portfolio()
    pf.add_lot("ACIK", 100, date(2026, 9, 1), 10.0)
    pf.add_lot("YENIKAP", 100, date(2026, 8, 1), 10.0)
    pf.add_lot("YENIKAP", -100, date(2026, 9, 3), 12.0)
    pf.add_lot("ESKIKAP", 100, date(2020, 1, 1), 10.0)
    pf.add_lot("ESKIKAP", -100, date(2020, 6, 1), 12.0)

    bugun = date(2026, 9, 4)
    assert pf.codes == ["ACIK"]
    assert pf.closed_codes == ["ESKIKAP", "YENIKAP"]
    assert pf.codes_for_pricing(60, today=bugun) == ["ACIK", "YENIKAP"]


def test_tarihsiz_satisla_kapanan_fon_cekilmez():
    """Ne zaman kapandığı bilinmiyorsa 'yakın' sayılamaz.

    Aksi hâlde yıllar önce satılmış her fon her çalıştırmada çekilirdi.
    """
    pf = Portfolio()
    pf.add_lot("ACIK", 100, date(2026, 9, 1), 10.0)
    pf.add_lot("TARIHSIZ", 100)
    pf.add_lot("TARIHSIZ", -100)
    assert pf.codes_for_pricing(60, today=date(2026, 9, 4)) == ["ACIK"]
