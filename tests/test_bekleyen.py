"""Bekleyen emirler: gerçekleşme gününe kadar bekleyip fiyat gelince çözülme."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from portfoy import bekleyen, storage, valor
from portfoy.storage import Portfolio
from portfoy.tefas_client import FundHistory


def gecmis(kod: str, fiyatlar: dict[date, float]) -> FundHistory:
    seri = pd.Series(
        list(fiyatlar.values()),
        index=pd.DatetimeIndex([pd.Timestamp(g) for g in fiyatlar]),
    ).sort_index()
    return FundHistory(code=kod, title=kod, source="test", prices=seri)


@pytest.fixture
def takvim():
    return valor.IslemTakvimi.seriden([
        date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
    ])


@pytest.fixture
def portfoy():
    pf = Portfolio()
    pf.add_lot("PHE", 1000, date(2026, 7, 1), 4.0)
    return pf


@pytest.fixture
def emir(takvim):
    """01.09 15:00'te verilen satış emri → gerçekleşme 03.09."""
    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 15, 0), satis=True,
    )
    assert cozum.gerceklesme == date(2026, 9, 3)
    return bekleyen.emir_olustur("PHE", -1000, cozum, datetime(2026, 9, 1, 15, 0))


# --- Bekleme ---------------------------------------------------------------
def test_fiyat_yayimlanmadan_cozulmez(portfoy, emir):
    h = gecmis("PHE", {date(2026, 9, 1): 3.65, date(2026, 9, 2): 3.23})
    cozulen, kalan = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert cozulen == [] and len(kalan) == 1
    # Adetler portföyde kalır: gerçekleşmeye kadar fiyat riski kullanıcıdadır.
    assert portfoy.position("PHE").units == 1000


def test_fiyat_gelince_islem_kaydina_donusur(portfoy, emir):
    h = gecmis("PHE", {date(2026, 9, 2): 3.23, date(2026, 9, 3): 3.05})
    cozulen, kalan = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert len(cozulen) == 1 and kalan == []
    assert cozulen[0].fiyat == pytest.approx(3.05)
    assert cozulen[0].fiyat_kaynagi == "tefas"
    assert cozulen[0].tarih == date(2026, 9, 3)

    pos = portfoy.position("PHE")
    assert pos.is_closed
    assert pos.realized_profit == pytest.approx(1000 * (3.05 - 4.0))


def test_elle_girilen_fiyat_tefasi_ezer(portfoy, takvim):
    """Aracı kurumun gerçekleşen fiyatı komisyon/yuvarlama yüzünden farklı olabilir."""
    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 11, 0), satis=True,
    )
    emir = bekleyen.emir_olustur("PHE", -1000, cozum, datetime(2026, 9, 1, 11, 0),
                                 fiyat=3.19)
    h = gecmis("PHE", {date(2026, 9, 1): 3.65, date(2026, 9, 2): 3.23})
    cozulen, _ = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert cozulen[0].fiyat == pytest.approx(3.19)
    assert cozulen[0].fiyat_kaynagi == "elle"


def test_tahmin_edilen_gun_tatile_denk_gelirse_hizalanir(portfoy, emir):
    """Seri ötesinde hafta sonu kuralı kullanılır; resmi tatil bilinemez."""
    # 03.09 tatil çıktı, ilk işlem günü 04.09.
    h = gecmis("PHE", {date(2026, 9, 2): 3.23, date(2026, 9, 4): 3.10})
    cozulen, _ = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert cozulen[0].hizalandi
    assert cozulen[0].tarih == date(2026, 9, 4)
    assert "hizalandı" in cozulen[0].ozet()


# --- Doğrulama -------------------------------------------------------------
def test_beklemedeki_satis_ayni_paylari_iki_kez_sattirmaz(portfoy, emir):
    with pytest.raises(bekleyen.BekleyenHatasi, match="bekleyen satış emrinde"):
        bekleyen.satis_dogrula(portfoy, [emir], "PHE", 100)


def test_elde_olmayan_satilamaz(portfoy):
    with pytest.raises(bekleyen.BekleyenHatasi, match="satılabilir"):
        bekleyen.satis_dogrula(portfoy, [], "PHE", 5000)


def test_portfoyde_olmayan_fon_satilamaz(portfoy):
    with pytest.raises(bekleyen.BekleyenHatasi, match="açık bir pozisyon değil"):
        bekleyen.satis_dogrula(portfoy, [], "ZZZ", 1)


# --- Disk ------------------------------------------------------------------
def test_emir_diske_yazilip_okunur(tmp_path, emir):
    yol = tmp_path / "bekleyen.json"
    bekleyen.kaydet([emir], yol)

    geri = bekleyen.yukle(yol)
    assert len(geri) == 1
    assert geri[0].kod == "PHE" and geri[0].adet == -1000
    assert geri[0].gerceklesme == date(2026, 9, 3)
    assert geri[0].nakit == date(2026, 9, 4)


def test_coz_ve_kaydet_iki_dosyayi_da_gunceller(tmp_path, portfoy, emir):
    pf_yol, bk_yol = tmp_path / "p.json", tmp_path / "b.json"
    storage.save(portfoy, pf_yol)
    bekleyen.kaydet([emir], bk_yol)

    h = gecmis("PHE", {date(2026, 9, 2): 3.23, date(2026, 9, 3): 3.05})
    cozulen = bekleyen.coz_ve_kaydet(portfoy, {"PHE": h}, bk_yol, pf_yol)

    assert len(cozulen) == 1
    assert bekleyen.yukle(bk_yol) == []
    assert storage.load(pf_yol).position("PHE").is_closed


def test_cozulme_portfoyu_bozmaz_emir_kaybolmaz(tmp_path, emir):
    """Portföy elle değiştirilip adet kalmadıysa emir düşürülmez, görünür kalır."""
    bos = Portfolio()
    h = gecmis("PHE", {date(2026, 9, 3): 3.05})
    cozulen, kalan = bekleyen.coz([emir], bos, {"PHE": h})
    assert cozulen == [] and len(kalan) == 1


# --- İnceleme bulguları: regresyon -----------------------------------------
def test_emir_iki_kez_islenmez(tmp_path, portfoy, emir, monkeypatch):
    """KRİTİK regresyon: iki dosyalı yazım atomik değil.

    Portföy yazıldıktan sonra bekleyen dosyasının yazımı patlarsa emir hem
    işlenmiş hem bekliyor kalırdı; sonraki çalışma aynı satışı bir kez daha
    uygulayıp adedi sessizce yok ederdi.
    """
    pf_yol, bk_yol = tmp_path / "p.json", tmp_path / "b.json"
    storage.save(portfoy, pf_yol)
    bekleyen.kaydet([emir], bk_yol)
    h = gecmis("PHE", {date(2026, 9, 3): 3.05})

    # 1. çalışma: bekleyen yazımı başarısız.
    patladi = {"n": 0}

    def patla(*a, **kw):
        patladi["n"] += 1
        raise storage.StorageError("disk dolu")

    monkeypatch.setattr(bekleyen, "kaydet", patla)
    bekleyen.coz_ve_kaydet(portfoy, {"PHE": h}, bk_yol, pf_yol)
    assert patladi["n"] == 1
    assert storage.load(pf_yol).position("PHE").is_closed
    assert len(bekleyen.yukle(bk_yol)) == 1      # emir hâlâ bekliyor

    # 2. çalışma: emir zaten işlenmiş, TEKRAR UYGULANMAMALI.
    monkeypatch.undo()
    tekrar = storage.load(pf_yol)
    cozulen = bekleyen.coz_ve_kaydet(tekrar, {"PHE": h}, bk_yol, pf_yol)

    assert cozulen == []
    assert bekleyen.yukle(bk_yol) == []          # bekleyenden düşüldü
    son = storage.load(pf_yol).position("PHE")
    assert len(son.lots) == 2                    # ikinci satış lotu YOK
    assert son.units == pytest.approx(0.0)


def test_cozulen_lot_emir_kimligi_tasir(portfoy, emir):
    h = gecmis("PHE", {date(2026, 9, 3): 3.05})
    bekleyen.coz([emir], portfoy, {"PHE": h})
    satis = [lot for lot in portfoy.position("PHE").lots if lot.units < 0][0]
    assert satis.emir_id == emir.id


def test_seri_oncesindeki_gerceklesme_ilk_fiyata_dusmez(portfoy, emir, caplog):
    """Regresyon: hedef serinin önündeyse serinin İLK fiyatı kullanılıyordu."""
    h = gecmis("PHE", {date(2026, 10, 10): 20.0, date(2026, 10, 12): 21.0})
    cozulen, kalan = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert cozulen == [] and len(kalan) == 1
    assert "öncesinde" in caplog.text
    assert portfoy.position("PHE").units == 1000


def test_ayni_gun_alis_satistan_once_islenir(tmp_path, takvim):
    """Satış önce gelip adet yetmezse emir sonsuza dek beklemede kalırdı."""
    pf = Portfolio()
    pf.add_lot("AAA", 100, date(2026, 8, 31), 10.0)

    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 11, 0), satis=True,
    )
    satis = bekleyen.emir_olustur("AAA", -150, cozum, datetime(2026, 9, 1, 11, 0))
    alis = bekleyen.emir_olustur("AAA", 100, cozum, datetime(2026, 9, 1, 11, 0))

    h = gecmis("AAA", {date(2026, 9, 2): 12.0})
    cozulen, kalan = bekleyen.coz([satis, alis], pf, {"AAA": h})

    assert len(cozulen) == 2 and kalan == []
    assert pf.position("AAA").units == pytest.approx(50)


def test_kaydet_izin_hatasini_sarar(tmp_path, emir):
    """Regresyon: mkstemp try dışındaydı, ham PermissionError sızıyordu."""
    yok = tmp_path / "olmayan-dizin" / "alt" / "b.json"
    yok.parent.parent.mkdir()
    yok.parent.mkdir(mode=0o500)             # yazılamaz
    try:
        with pytest.raises(storage.StorageError, match="kaydedilemedi"):
            bekleyen.kaydet([emir], yok)
    finally:
        yok.parent.chmod(0o700)
