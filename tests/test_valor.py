"""Valör türetmesi: emir zamanından gerçekleşme ve nakit günü."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from portfoy import valor


@pytest.fixture
def takvim():
    """25.08 Sal – 02.09 Çar arası iş günleri (29-30.08 hafta sonu)."""
    return valor.IslemTakvimi.seriden([
        date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 28),
        date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
    ])


@pytest.fixture
def hsyf():
    return valor.kategori_kurali("Hisse Senedi Fonu")


# --- Kesim saati -----------------------------------------------------------
def test_kesim_sonrasi_emir_ertesi_is_gunune_kayar(takvim, hsyf):
    """Gerçek olay, dekontla doğrulanmış: 01.09 18:22'de verilen PHE satış emri.

    Garanti BBVA İşlem Sonuç Formu (04.09.2026): işlem günü 02.09, uygulanan
    birim fiyat 02.09 kapanışı (3,230415), hesaba geçiş 04.09. Yani FİYAT
    işlem gününün kendisinden alınır; valör (03.09) yalnızca payların çıkış
    günüdür. Kod bir dönem fiyatı 03.09'dan alıyordu.
    """
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 18, 22), satis=True)
    assert c.kesim_sonrasi
    assert c.islem_gunu == date(2026, 9, 2)
    assert c.gerceklesme == date(2026, 9, 2)      # fiyat günü = T
    assert c.valor_gunu == date(2026, 9, 3)       # payların çıkışı
    assert c.nakit == date(2026, 9, 4)


def test_kesim_oncesi_emir_ayni_gun_islem_gorur(takvim, hsyf):
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 11, 0), satis=True)
    assert not c.kesim_sonrasi
    assert c.islem_gunu == date(2026, 9, 1)
    assert c.gerceklesme == date(2026, 9, 1)


def test_tam_kesim_saatinde_verilen_emir_kayar(takvim, hsyf):
    """13:30'un kendisi kesim sonrası sayılır — sınır lehte yorumlanmaz."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 13, 30), satis=True)
    assert c.kesim_sonrasi


def test_saat_bilinmiyorsa_varsayim_yapilmaz(takvim, hsyf):
    """'Herhalde erkendi' varsayımı tam da bu aracı yanıltan senaryo."""
    with pytest.raises(valor.ValorHatasi, match="Emir saati bilinmiyor"):
        valor.cozumle(hsyf, takvim, date(2026, 9, 1), satis=True)

    c = valor.cozumle(hsyf, takvim, date(2026, 9, 1), satis=True, kesim_sonrasi=True)
    assert c.islem_gunu == date(2026, 9, 2)


# --- İş günü takvimi -------------------------------------------------------
def test_hafta_sonu_atlanir(takvim, hsyf):
    """28.08 Cuma 15:00 → T = 31.08 Pazartesi (29-30 hafta sonu)."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 8, 28, 15, 0), satis=True)
    assert c.islem_gunu == date(2026, 8, 31)
    assert c.gerceklesme == date(2026, 8, 31)
    assert c.valor_gunu == date(2026, 9, 1)


def test_tatil_gunu_verilen_emir_ilk_is_gunune_tasinir(takvim, hsyf):
    """29.08 Cumartesi verilen emir 31.08 Pazartesi'ye taşınır."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 8, 29, 10, 0), satis=True)
    assert c.islem_gunu == date(2026, 8, 31)


def test_seri_ici_tatil_ayri_tablo_gerektirmez():
    """Resmi tatil seride yok; 'n iş günü ileri' kendiliğinden doğru çıkar."""
    # 27.08 ve 28.08 tatil olsun: seride yoklar.
    tak = valor.IslemTakvimi.seriden([
        date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 31), date(2026, 9, 1),
    ])
    kural = valor.kategori_kurali("Hisse Senedi Fonu")
    c = valor.cozumle(kural, tak, datetime(2026, 8, 26, 10, 0), satis=True)
    assert c.islem_gunu == date(2026, 8, 26)
    assert c.gerceklesme == date(2026, 8, 26)      # fiyat günü = T
    assert c.valor_gunu == date(2026, 8, 31)       # 27-28 atlandı
    assert c.gerceklesme_kesin and c.valor_kesin


def test_seri_otesinde_kesinlik_dusuyor(takvim, hsyf):
    """Fiyat günü artık T olduğu için kesinlik ancak T serinin ötesindeyse düşer."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 2, 15, 0), satis=True)
    assert c.gerceklesme == date(2026, 9, 3)
    assert not c.gerceklesme_kesin        # 03.09 henüz yayımlanmadı
    assert "gerçek takvime göre hizalanacak" in " ".join(c.anlat(satis=True))


def test_gerceklesme_kesinken_nakit_belirsizligi_uyari_bastirmaz(takvim, hsyf):
    """Nakit günü neredeyse hep serinin ötesinde; sürekli yanan uyarı okunmaz."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 11, 0), satis=True)
    assert c.gerceklesme_kesin and not c.nakit_kesin
    assert not c.supheli or c.kural.supheli   # şüphe yalnızca kuraldan gelebilir


# --- Kategori varsayılanları ----------------------------------------------
def test_kategori_varsayilanlari_supheli_baslar():
    for kategori in ["Hisse Senedi Fonu", "Serbest Fon", "bilinmeyen kategori"]:
        assert valor.kategori_kurali(kategori).supheli


def test_para_piyasasi_ayni_gun_islenir():
    k = valor.kategori_kurali("Para Piyasası Fonu")
    assert k.satis_valor == 0 and k.satis_nakit == 1


def test_dogrulanan_kural_supheli_sayilmaz():
    k = valor.dogrula(valor.kategori_kurali("Hisse Senedi Fonu"), date(2026, 9, 3))
    assert not k.supheli and k.dogrulandi == date(2026, 9, 3)


# --- Mutabakat -------------------------------------------------------------
def test_nakit_uyusmazligi_yakalanir(takvim, hsyf):
    """Bu kontrol, gerçek hatayı ilk gün yakalardı."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 11, 0), satis=True)
    assert c.nakit == date(2026, 9, 3)
    uyari = valor.nakit_uyusmazligi(c, date(2026, 9, 4))
    assert uyari and "1 gün ileri" in uyari and "kesim saatinin" in uyari


def test_nakit_tutuyorsa_uyari_yok(takvim, hsyf):
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 15, 0), satis=True)
    assert valor.nakit_uyusmazligi(c, date(2026, 9, 4)) is None
    assert valor.nakit_uyusmazligi(c, None) is None


# --- Disk ------------------------------------------------------------------
def test_kural_diske_yazilip_okunur(tmp_path):
    yol = tmp_path / "valor.json"
    kural = valor.dogrula(valor.kategori_kurali("Hisse Senedi Fonu"), date(2026, 9, 3))
    valor.kaydet({"PHE": kural}, yol)

    geri = valor.yukle(yol)
    assert geri["PHE"].satis_valor == 1 and geri["PHE"].satis_nakit == 2
    assert not geri["PHE"].supheli


def test_bozuk_valor_dosyasi_raporu_durdurmaz(tmp_path, caplog):
    yol = tmp_path / "valor.json"
    yol.write_text("{bozuk", encoding="utf-8")
    assert valor.yukle(yol) == {}
    assert "okunamadı" in caplog.text


# --- İnceleme bulguları: regresyon -----------------------------------------
def test_seri_oncesi_emir_sessizce_ileri_kaymaz(takvim, hsyf):
    """Regresyon: seri öncesi tarih ilk güne yapışıp `kesin=True` dönüyordu.

    04.05 emri 31.08'e yapışıyor, iki aylık kayma tam güvenle sunuluyordu.
    """
    with pytest.raises(valor.ValorHatasi, match="fiyat penceresinin"):
        valor.cozumle(hsyf, takvim, datetime(2026, 5, 4, 10, 0), satis=True)


def test_takvim_seri_oncesini_kapsamiyor_sayar(takvim):
    assert not takvim.kapsiyor_mu(date(2026, 5, 4))
    assert takvim.kapsiyor_mu(date(2026, 8, 31))
    assert takvim.kapsiyor_mu(date(2026, 12, 1))     # seri ötesi kapsanır
    assert takvim.ilk == date(2026, 8, 25)


@pytest.mark.parametrize("saat", [10, 15])
def test_kesim_seans_gunu_olmayana_uygulanmaz(takvim, hsyf, saat):
    """Regresyon: Cumartesi öğleden sonra verilen emir fazladan bir gün itiliyordu.

    İkisi de Pazartesi seansına girer; Pazartesi'nin 13:30 kesimi geçmemiştir.
    """
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 5, saat, 0), satis=True)
    assert c.islem_gunu == date(2026, 9, 7)
    assert c.gerceklesme == date(2026, 9, 7)
    assert not c.kesim_sonrasi          # kaydırma uygulanmadı


def test_kesim_seans_gununde_uygulanir(takvim, hsyf):
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 15, 0), satis=True)
    assert c.kesim_sonrasi and c.islem_gunu == date(2026, 9, 2)


def test_saat_ve_kesim_tarafi_birlikte_verilemez(takvim, hsyf):
    """Sessizce birini yutmak yerine açıkça reddedilir."""
    with pytest.raises(valor.ValorHatasi, match="İkisinden birini"):
        valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 10, 0),
                      satis=True, kesim_sonrasi=True)


def test_bos_takvim_cozumleme_yapmaz(hsyf):
    bos = valor.IslemTakvimi.seriden([])
    with pytest.raises(valor.ValorHatasi):
        valor.cozumle(hsyf, bos, datetime(2026, 9, 1, 10, 0), satis=True)


def test_tek_gunluk_takvim(hsyf):
    tek = valor.IslemTakvimi.seriden([date(2026, 9, 1)])
    c = valor.cozumle(hsyf, tek, datetime(2026, 9, 1, 10, 0), satis=True)
    assert c.islem_gunu == date(2026, 9, 1)
    assert c.gerceklesme == date(2026, 9, 1)
    assert c.valor_gunu == date(2026, 9, 2)      # seri ötesi, hafta içi
    assert c.gerceklesme_kesin and not c.valor_kesin


# --- anlat() metni ---------------------------------------------------------
def test_alista_valor_satiri_yonu_dogru_yazar(takvim, hsyf):
    """Alışta paylar emanete GİRER; satış metnini iki yönde de kullanmak yanlış."""
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 11, 0), satis=False)
    metin = " ".join(c.anlat(satis=False))
    assert "emanete girdiği gün" in metin
    assert "çıktığı" not in metin


def test_satista_valor_satiri_cikis_der(takvim, hsyf):
    c = valor.cozumle(hsyf, takvim, datetime(2026, 9, 1, 11, 0), satis=True)
    assert "emanetten çıktığı gün" in " ".join(c.anlat(satis=True))


def test_valor_sifirsa_ayri_satir_yazilmaz(takvim):
    """T+0 fonda valör günü = işlem günü; aynı tarihi iki kez yazmak karıştırır."""
    ppf = valor.kategori_kurali("Para Piyasası Fonu")
    assert ppf.satis_valor == 0
    c = valor.cozumle(ppf, takvim, datetime(2026, 9, 1, 11, 0), satis=True)
    satirlar = c.anlat(satis=True)
    assert c.valor_gunu == c.gerceklesme
    assert not any(s.startswith("Valör") for s in satirlar)
    assert any(s.startswith("Nakit (T+1)") for s in satirlar)
