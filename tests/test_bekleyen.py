"""Bekleyen emirler: gerçekleşme gününe kadar bekleyip fiyat gelince çözülme."""

from __future__ import annotations

import json
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
    """01.09 15:00'te verilen satış emri → işlem günü ve fiyat günü 02.09."""
    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 15, 0), satis=True,
    )
    assert cozum.gerceklesme == date(2026, 9, 2)
    return bekleyen.emir_olustur("PHE", -1000, cozum, datetime(2026, 9, 1, 15, 0))


# --- Bekleme ---------------------------------------------------------------
def test_fiyat_yayimlanmadan_cozulmez(portfoy, emir):
    h = gecmis("PHE", {date(2026, 8, 31): 3.90, date(2026, 9, 1): 3.65})
    cozulen, kalan = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert cozulen == [] and len(kalan) == 1
    # Adetler portföyde kalır: gerçekleşmeye kadar fiyat riski kullanıcıdadır.
    assert portfoy.position("PHE").units == 1000


def test_fiyat_gelince_islem_kaydina_donusur(portfoy, emir):
    h = gecmis("PHE", {date(2026, 9, 2): 3.23, date(2026, 9, 3): 3.05})
    cozulen, kalan = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert len(cozulen) == 1 and kalan == []
    # 03.09 fiyati da elde ama kullanilmaz: fiyat ISLEM GUNUNUN kapanisidir.
    assert cozulen[0].fiyat == pytest.approx(3.23)
    assert cozulen[0].fiyat_kaynagi == "tefas"
    assert cozulen[0].tarih == date(2026, 9, 2)

    pos = portfoy.position("PHE")
    assert pos.is_closed
    assert pos.realized_profit == pytest.approx(1000 * (3.23 - 4.0))


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
    # 02.09 tatil çıktı, ilk işlem günü 03.09.
    h = gecmis("PHE", {date(2026, 9, 1): 3.65, date(2026, 9, 3): 3.10})
    cozulen, _ = bekleyen.coz([emir], portfoy, {"PHE": h})

    assert cozulen[0].hizalandi
    assert cozulen[0].tarih == date(2026, 9, 3)
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
    assert geri[0].gerceklesme == date(2026, 9, 2)
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
    h = gecmis("PHE", {date(2026, 9, 2): 3.23})
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
    h = gecmis("PHE", {date(2026, 9, 2): 3.23})

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
    h = gecmis("PHE", {date(2026, 9, 2): 3.23})
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

    h = gecmis("AAA", {date(2026, 9, 1): 12.0})
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


def test_dekont_senaryosu_fiyat_islem_gununden_alinir(takvim):
    """Regresyon (04.09.2026 Garanti BBVA dekontu): fiyat T'den, valörden DEĞİL.

    01.09 18:22'de (kesim sonrası) verilen PHE satış emri; işlem günü 02.09,
    uygulanan birim fiyat 02.09 kapanışı 3,230415, hesaba geçiş 04.09. Kod
    fiyatı valör günü 03.09'dan (2,818639) alıyordu: 69.991 adette 28.820,61 TL
    fark. Fon o iki günde sert düştüğü için hata doğrudan paraya dönüşmüştü.
    """
    pf = Portfolio()
    pf.add_lot("PHE", 69991, date(2026, 8, 31), 4.106306)

    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 18, 22), satis=True,
    )
    assert cozum.islem_gunu == date(2026, 9, 2)
    assert cozum.valor_gunu == date(2026, 9, 3)
    assert cozum.nakit == date(2026, 9, 4)

    emir = bekleyen.emir_olustur("PHE", -69991, cozum,
                                 datetime(2026, 9, 1, 18, 22))
    h = gecmis("PHE", {
        date(2026, 9, 1): 3.653514,
        date(2026, 9, 2): 3.230415,
        date(2026, 9, 3): 2.818639,      # valör günü — kullanılmamalı
    })
    cozulen, kalan = bekleyen.coz([emir], pf, {"PHE": h})

    assert kalan == [] and len(cozulen) == 1
    assert cozulen[0].tarih == date(2026, 9, 2)
    assert cozulen[0].fiyat == pytest.approx(3.230415)

    satis = [lot for lot in pf.position("PHE").lots if lot.units < 0][0]
    assert abs(satis.units) * satis.price == pytest.approx(226099.98, abs=0.01)


def test_valor_gunu_diske_yazilip_okunur(tmp_path, emir):
    """Valör günü emirle birlikte kalıcı olmalı: kaydedildikten sonra tek kaynağı bu."""
    yol = tmp_path / "bekleyen.json"
    bekleyen.kaydet([emir], yol)

    geri = bekleyen.yukle(yol)[0]
    assert geri.valor_gunu == date(2026, 9, 3)
    assert geri.gerceklesme == date(2026, 9, 2)      # fiyat günü ayrı


def surum1_dosyasi(yol, *, damga: bool = True) -> None:
    """Eski mantıkla yazılmış bir emir dosyası: gerçekleşme = işlem günü + valör."""
    kayit = {
        "id": "eski01", "kod": "PHE", "adet": -69991.0,
        "emir_zamani": "2026-09-01T18:22:00",
        "islem_gunu": "2026-09-02",
        "gerceklesme": "2026-09-03",        # sürüm 1'de valör günü buraya yazılırdı
        "nakit": "2026-09-04",
        "tarih_kaynagi": "turetildi", "valor_supheli": False,
    }
    govde = {"emirler": [kayit]}
    if damga:
        govde = {"surum": 1, **govde}
    yol.write_text(json.dumps(govde, ensure_ascii=False), encoding="utf-8")


def test_eski_surum_kaydi_dogru_fiyat_gunune_tasinir(tmp_path):
    """Sürüm 1'de fiyat valör gününden alınıyordu; göç kayıpsız çünkü T de yazılı."""
    yol = tmp_path / "bekleyen.json"
    surum1_dosyasi(yol)

    geri = bekleyen.yukle(yol)[0]
    assert geri.gerceklesme == date(2026, 9, 2)      # fiyat günü = işlem günü
    assert geri.valor_gunu == date(2026, 9, 3)       # eski alan valör günüydü
    assert geri.nakit == date(2026, 9, 4)


def test_surum_damgasi_yoksa_eski_sayilir(tmp_path):
    """Elle düzenlenmiş dosyada damga silinmiş olabilir; varsayılan güvenli taraf."""
    yol = tmp_path / "bekleyen.json"
    surum1_dosyasi(yol, damga=False)
    assert bekleyen.yukle(yol)[0].gerceklesme == date(2026, 9, 2)


def test_goc_kaydetme_turundan_sonra_da_kalici(tmp_path):
    """Regresyon: sürüm damgası dosyada, kusur kayıttaydı.

    Uyarı yaklaşımında ilk `kaydet` damgayı 2 yapıyor, kaydın kendisi eski
    kalıyordu — emir bir daha uyarmadan valör gününün fiyatından işlenirdi.
    """
    yol = tmp_path / "bekleyen.json"
    surum1_dosyasi(yol)

    bekleyen.kaydet(bekleyen.yukle(yol), yol)        # kullanıcı bir emir ekler/siler
    ham = json.loads(yol.read_text(encoding="utf-8"))
    assert ham["surum"] == 2

    geri = bekleyen.yukle(yol)[0]
    assert geri.gerceklesme == date(2026, 9, 2)      # göç kalıcı, sessiz kayma yok
    assert geri.valor_gunu == date(2026, 9, 3)


def test_bos_eski_dosya_gurultu_uretmez(tmp_path, caplog):
    """Emir yoksa taşınacak bir şey de yok; sürekli yanan kayıt okunmaz hale gelir."""
    yol = tmp_path / "bekleyen.json"
    yol.write_text('{"surum": 1, "emirler": []}', encoding="utf-8")
    with caplog.at_level("INFO"):
        assert bekleyen.yukle(yol) == []
    assert "taşındı" not in caplog.text


# --- TL tutarıyla emir (alış) ----------------------------------------------
@pytest.fixture
def tutar_emri(takvim):
    """04.09 17:01'de verilen TL'li alış emri → işlem günü 07.09."""
    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 15, 0), satis=False,
    )
    return bekleyen.emir_olustur("THF", None, cozum,
                                 datetime(2026, 9, 1, 15, 0), tutar=226000.0)


def test_tutarli_emirde_adet_fiyat_gelince_dogar(tutar_emri):
    """Alışta adet HENÜZ YOKTUR; tutar ÷ işlem günü fiyatı ile hesaplanır."""
    pf = Portfolio()
    h = gecmis("THF", {date(2026, 9, 1): 2.75, date(2026, 9, 2): 2.825})
    cozulen, kalan = bekleyen.coz([tutar_emri], pf, {"THF": h})

    assert kalan == [] and len(cozulen) == 1
    assert cozulen[0].fiyat == pytest.approx(2.825)
    assert cozulen[0].adet == pytest.approx(226000.0 / 2.825)
    assert pf.position("THF").units == pytest.approx(226000.0 / 2.825)
    assert "tutardan hesaplandı" in cozulen[0].ozet()


def test_tutarli_emir_fiyat_yayimlanmadan_portfoye_girmez(tutar_emri):
    """Adedi uydurmak, tahmin ile gerçekleşme farkını sahte kâr/zarara çevirirdi."""
    pf = Portfolio()
    h = gecmis("THF", {date(2026, 8, 31): 2.70, date(2026, 9, 1): 2.75})
    cozulen, kalan = bekleyen.coz([tutar_emri], pf, {"THF": h})

    assert cozulen == [] and len(kalan) == 1
    assert pf.positions == {}


def test_tutarli_emir_diske_yazilip_okunur(tmp_path, tutar_emri):
    yol = tmp_path / "bekleyen.json"
    bekleyen.kaydet([tutar_emri], yol)

    geri = bekleyen.yukle(yol)[0]
    assert geri.adet is None and geri.adet_bilinmiyor
    assert geri.tutar == pytest.approx(226000.0)
    assert not geri.satis_mi
    assert "226.000,00 ₺" in geri.ozet() and "adet fiyat gelince" in geri.ozet()


def test_tutarla_satis_kabul_edilmez(takvim):
    """Adet bilinmeden bekleyen satış rezervesi sayılamaz — çift satış riski."""
    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 11, 0), satis=True,
    )
    with pytest.raises(bekleyen.BekleyenHatasi, match="satış emri kabul edilmiyor"):
        bekleyen.emir_olustur("THF", None, cozum,
                              datetime(2026, 9, 1, 11, 0), tutar=-1000.0)


def test_adet_ve_tutar_birlikte_verilemez(takvim):
    cozum = valor.cozumle(
        valor.kategori_kurali("Hisse Senedi Fonu"), takvim,
        datetime(2026, 9, 1, 11, 0), satis=False,
    )
    with pytest.raises(bekleyen.BekleyenHatasi, match="tam olarak birini"):
        bekleyen.emir_olustur("THF", 100, cozum,
                              datetime(2026, 9, 1, 11, 0), tutar=1000.0)
    with pytest.raises(bekleyen.BekleyenHatasi, match="tam olarak birini"):
        bekleyen.emir_olustur("THF", None, cozum, datetime(2026, 9, 1, 11, 0))


def test_tutarli_emir_bekleyen_satis_adedini_etkilemez(portfoy, emir, tutar_emri):
    """Alış emri rezerve üretmez; satış korumasının tabanını bozmamalı."""
    assert bekleyen.bekleyen_satis_adedi([emir, tutar_emri], "PHE") == 1000
    assert bekleyen.bekleyen_satis_adedi([tutar_emri], "THF") == 0
