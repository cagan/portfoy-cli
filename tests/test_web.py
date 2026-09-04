"""Web arayüzü: doğrulama, güvenlik ve mutasyon akışı.

TEFAS'a gerçek istek atılmaz: kod doğrulama yolu `monkeypatch` ile,
mutasyonlar `force=true` ile ağdan bağımsız koşar.
"""

from __future__ import annotations

import re

import pathlib
from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi", reason="web ekstrası kurulu değil")

from fastapi.testclient import TestClient           # noqa: E402

from portfoy import storage                          # noqa: E402
from portfoy.web import create_app                   # noqa: E402


KONAK = "127.0.0.1:8000"
KAYNAK = f"http://{KONAK}"


@pytest.fixture
def istemci(ornek_portfoy, portfoy_dosyasi, cikti_dizini):
    """Tarayıcı gibi davranan istemci.

    `Origin` başlığı varsayılan olarak gönderilir: yazan isteklerde bu başlık
    ZORUNLU (bkz. app.erisim_kalkani) ve modern tarayıcılar same-origin form
    POST'larında da gönderir.
    """
    app = create_app(data_file=portfoy_dosyasi, output_dir=cikti_dizini)
    with TestClient(app, base_url=KAYNAK, headers={"origin": KAYNAK}) as c:
        yield c


def gonder(istemci, yol: str, **veri) -> str:
    """POST at, yönlendirmeyi izle, sonuç sayfasının HTML'ini döndür."""
    cevap = istemci.post(yol, data=veri, follow_redirects=False)
    assert cevap.status_code == 303, (yol, cevap.status_code)
    return istemci.get(cevap.headers["location"]).text


# --- Sayfalar --------------------------------------------------------------
@pytest.mark.parametrize("yol", ["/", "/islemler", "/raporlar", "/eposta"])
def test_sayfalar_aciliyor(istemci, yol):
    assert istemci.get(yol).status_code == 200


def test_kapanmis_pozisyon_islemlerde_gorunur_durumda_gorunmez(istemci):
    """Kapanan fon HOLDING olarak listelenmez ama getiri özetinde adı geçer.

    İkisi farklı sorular: fon tablosu "şu an neye sahibim"i, özet ise "bu
    pencerede ne yaşadım"ı anlatıyor. Kapanan fonu özetten de saklamak,
    portföyün o gün yaşadığı düşüşü görünmez kılardı.
    """
    islemler = istemci.get("/islemler").text
    assert "PHE" in islemler and "KAPANDI" in islemler

    durum = istemci.get("/").text
    ozet, _, fon_tablosu = durum.partition("<h2>Fonlar</h2>")
    # Holding GÖVDESİ: iddia "kapanmış fon holding olarak listelenmez" olduğuna
    # göre bakılacak yer tablonun tamamı değil, açık pozisyonların tbody'si.
    # (Şablona `class="holdingler"` bunun için eklendi; kapanan satır tfoot'ta.)
    govde = fon_tablosu.split('<tbody class="holdingler">')[1].split("</tbody>")[0]
    assert "PHE" not in govde
    # Özette adı GEÇMELİ: `closed_note`/akış notu None'a dönerse bu test
    # kırmızıya dönsün — yoksa "özette adı geçer" iddiası doğrulanmamış kalır.
    assert "PHE" in ozet


# --- Güvenlik --------------------------------------------------------------
def test_yabanci_konak_reddedilir(istemci):
    """DNS rebinding: kötü bir site alan adını 127.0.0.1'e çözdürebilir."""
    assert istemci.get("/", headers={"host": "kotu.example.com"}).status_code == 403


def test_ipv6_loopback_kabul_edilir():
    """Regresyon: '[::1]:8000' adresi ':' ile bölününce '[' kalıyordu."""
    from portfoy.web.app import YEREL_KONAKLAR, _konak_adi

    for konak in ["[::1]:8000", "[::1]", "::1", "127.0.0.1:8000", "LOCALHOST:80"]:
        assert _konak_adi(konak) in YEREL_KONAKLAR, konak


def test_izinli_konaklar_genisletilebilir(ornek_portfoy, portfoy_dosyasi, cikti_dizini):
    """`--host` ile başlatılan sunucu kendi adresini de kabul etmeli."""
    app = create_app(
        data_file=portfoy_dosyasi, output_dir=cikti_dizini,
        izinli_konaklar={"127.0.0.1", "192.168.1.5"},
    )
    with TestClient(app, base_url="http://192.168.1.5:8000") as c:
        assert c.get("/").status_code == 200


@pytest.mark.parametrize(
    "kaynak",
    [
        "http://kotu.example.com",
        # Makinede calisan BASKA bir yerel sunucudaki sayfa: konak adi ayni
        # ama origin farkli. Yalnizca konak adina bakmak bunu kabul ederdi.
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        # split(":")[0] ile bakildiginda 'localhost' gorunen sahte alan adi.
        "http://localhost:8000.evil.com",
        "https://127.0.0.1:8000",          # sema farkli
        "null",                            # sandbox'li iframe
    ],
)
def test_yabanci_kaynaktan_yazma_reddedilir(istemci, kaynak):
    cevap = istemci.post("/hedef", data={"percent": "15"}, headers={"origin": kaynak})
    assert cevap.status_code == 403


def test_kaynaksiz_yazma_reddedilir(istemci, portfoy_dosyasi):
    """Origin göndermeyen istek CSRF'in en kolay yolu; zorunlu tutuluyor."""
    onceki = storage.load(portfoy_dosyasi).target_monthly_return
    cevap = istemci.post("/hedef", data={"percent": "14"}, headers={"origin": ""})
    assert cevap.status_code == 403
    assert storage.load(portfoy_dosyasi).target_monthly_return == onceki


def test_okuma_istegi_kaynak_istemez(istemci):
    assert istemci.get("/", headers={"origin": ""}).status_code == 200


@pytest.mark.parametrize(
    "ad", ["../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "portfoy.json", "yok.png"]
)
def test_indirmede_yol_kacisi_engellenir(istemci, ad):
    assert istemci.get(f"/indir/{ad}").status_code == 404


def test_fon_kodu_html_olarak_yorumlanmaz(istemci):
    """Hata mesajına düşen girdi kaçışlanmalı."""
    html = gonder(istemci, "/islemler", code="<script>x</script>", tur="alis", units="1")
    assert "<script>x</script>" not in html


# --- Biçimsel doğrulama ----------------------------------------------------
@pytest.mark.parametrize(
    "ad, yol, veri, beklenen",
    [
        ("geçersiz kod", "/islemler",
         {"code": "T M V", "tur": "alis", "units": "10"}, "yalnızca harf ve rakam"),
        ("negatif adet", "/islemler",
         {"code": "TMV", "tur": "alis", "units": "-10"}, "sıfırdan büyük"),
        ("sıfır adet", "/islemler",
         {"code": "TMV", "tur": "alis", "units": "0"}, "sıfırdan büyük"),
        ("NaN adet", "/islemler",
         {"code": "TMV", "tur": "alis", "units": "nan"}, "geçerli bir sayı"),
        ("devasa adet", "/islemler",
         {"code": "TMV", "tur": "alis", "units": "1e15"}, "fazla büyük"),
        ("bozuk tarih", "/islemler",
         {"code": "TMV", "tur": "alis", "units": "1", "date": "32.13.2026"}, "anlaşılamadı"),
        ("satış fiyatsız", "/islemler",
         {"code": "TMV", "tur": "satis", "units": "1", "date": "2026-09-01"},
         "birim fiyat zorunlu"),
        ("satış tarihsiz", "/islemler",
         {"code": "TMV", "tur": "satis", "units": "1", "price": "10"}, "tarih zorunlu"),
        ("onaysız düzeltme", "/adet-duzelt",
         {"code": "TMV", "units": "5"}, "onay kutusunu"),
        ("onaysız silme", "/fon-sil", {"code": "TMV"}, "onay kutusunu"),
        ("hedef aralık dışı", "/hedef", {"percent": "99999"}, "arasında olmalı"),
        ("hedef sayı değil", "/hedef", {"percent": "abc"}, "bir sayı olmalı"),
        ("adet sayı değil", "/islemler",
         {"code": "TMV", "tur": "alis", "units": "abc"}, "bir sayı olmalı"),
        ("port sayı değil", "/eposta",
         {"user": "a@b.com", "port": "abc"}, "Port bir sayı olmalı"),
        ("geçersiz e-posta", "/eposta", {"user": "bozuk"}, "geçerli bir e-posta"),
        ("geçersiz alıcı", "/eposta",
         {"user": "a@b.com", "recipients": "xyz"}, "Geçersiz alıcı"),
        ("geçersiz port", "/eposta",
         {"user": "a@b.com", "port": "99999"}, "Port 1-65535"),
    ],
)
def test_bicimsel_dogrulama(istemci, ad, yol, veri, beklenen):
    assert beklenen in gonder(istemci, yol, **veri), ad


def test_gelecek_tarih_reddedilir(istemci):
    yarin = (date.today() + timedelta(days=1)).isoformat()
    html = gonder(istemci, "/islemler", code="TMV", tur="alis", units="1", date=yarin)
    assert "gelecekte" in html


# --- Portföy durumuna bağlı doğrulama --------------------------------------
def test_elde_olandan_fazlasi_satilamaz(istemci):
    html = gonder(istemci, "/islemler", code="TMV", tur="satis",
                  units="999999", date="2026-09-01", price="10")
    assert "adet satılamaz" in html


def test_portfoyde_olmayan_fon_satilamaz(istemci):
    html = gonder(istemci, "/islemler", code="ZZZ", tur="satis",
                  units="1", date="2026-09-01", price="1")
    assert "portföyde yok" in html


def test_kapanmis_pozisyon_satilamaz(istemci):
    html = gonder(istemci, "/islemler", code="PHE", tur="satis",
                  units="1", date="2026-09-01", price="1")
    assert "zaten kapanmış" in html


def test_kapanmis_pozisyonun_uzerine_yazilamaz(istemci):
    html = gonder(istemci, "/adet-duzelt", code="PHE", units="100", onay="true")
    assert "kapanmis bir pozisyon" in html


def test_taninmayan_fon_kodu_reddedilir(istemci, monkeypatch):
    monkeypatch.setattr(
        "portfoy.web.services.PortfoyServisi.fon_taninmiyor_mu", lambda self, kod: True
    )
    html = gonder(istemci, "/islemler", code="ZZZ", tur="alis", units="10")
    assert "kodunu tanımıyor" in html


def test_baglanti_sorununda_reddedilmez_uyarilir(istemci, monkeypatch):
    """İnternet yoksa doğru girilmiş kodu reddetmek yanlış olurdu."""
    monkeypatch.setattr(
        "portfoy.web.services.PortfoyServisi.fon_taninmiyor_mu", lambda self, kod: None
    )
    html = gonder(istemci, "/islemler", code="ZZZ", tur="alis", units="10")
    assert "doğrulanamadı" in html and "kayıt yine de eklendi" in html


# --- Mutasyonlar -----------------------------------------------------------
def test_alis_ve_satis_kaydedilir(istemci, portfoy_dosyasi):
    html = gonder(istemci, "/islemler", code="TMV", tur="alis", units="1000",
                  date="2026-08-20", price="9.5", force="true")
    assert "Alış 1.000 adet kaydedildi" in html

    html = gonder(istemci, "/islemler", code="TMV", tur="satis", units="1000",
                  date="2026-09-01", price="10.5")
    assert "Satış 1.000 adet kaydedildi" in html

    pf = storage.load(portfoy_dosyasi)
    assert pf.position("TMV").units == pytest.approx(1500)
    assert len(pf.position("TMV").lots) == 4


def test_tamami_satilinca_pozisyon_kapanir(istemci, portfoy_dosyasi):
    html = gonder(istemci, "/islemler", code="TMV", tur="satis",
                  units="1500", date="2026-09-01", price="11")
    assert "KAPANDI" in html

    pf = storage.load(portfoy_dosyasi)
    assert pf.closed_codes == ["PHE", "TMV"]
    assert pf.position("TMV").realized_profit == pytest.approx(
        1000 * (11 - 8.0) + 500 * (11 - 9.0)
    )


def test_hedef_ayarlanir(istemci, portfoy_dosyasi):
    assert "%15 olarak ayarlandı" in gonder(istemci, "/hedef", percent="15")
    assert storage.load(portfoy_dosyasi).target_monthly_return == pytest.approx(15.0)


def test_fon_silme_gecmisi_de_goturur(istemci, portfoy_dosyasi):
    html = gonder(istemci, "/fon-sil", code="PHE", onay="true")
    assert "PHE silindi" in html
    assert "PHE" not in storage.load(portfoy_dosyasi).positions


def test_yenileme_islemi_tekrarlamaz(istemci, portfoy_dosyasi):
    """POST-Redirect-GET: yönlendirme hedefini iki kez almak yeni lot yaratmaz."""
    cevap = istemci.post(
        "/islemler",
        data={"code": "TMV", "tur": "alis", "units": "100",
              "date": "2026-08-20", "price": "9", "force": "true"},
        follow_redirects=False,
    )
    hedef = cevap.headers["location"]
    istemci.get(hedef)
    istemci.get(hedef)          # tarayıcı yenilemesi
    assert len(storage.load(portfoy_dosyasi).position("TMV").lots) == 3


# --- JSON API --------------------------------------------------------------
def test_api_portfoy_acik_ve_kapanmisi_ayirir(istemci):
    veri = istemci.get("/api/portfoy").json()
    assert set(veri["acik"]) == {"TMV"}
    assert set(veri["kapanmis"]) == {"PHE"}
    assert veri["kapanmis"]["PHE"]["gerceklesen_kar"] == pytest.approx(200 * (3.2 - 4.0))


# --- Bildirim izolasyonu ---------------------------------------------------
def test_bildirim_dogru_sekmeye_gider(ornek_portfoy, portfoy_dosyasi, cikti_dizini):
    """Regresyon: mesajlar sunucuda ortak listede tutulurken yanlış sekmeye gidiyordu.

    Kullanıcı açısından sonucu şuydu: alışı kaydeder, onay görmez, işlemin
    geçmediğini sanıp tekrar girer — tam da PRG'nin engellemeye çalıştığı şey.
    """
    app = create_app(data_file=portfoy_dosyasi, output_dir=cikti_dizini)
    with TestClient(app, base_url=KAYNAK, headers={"origin": KAYNAK}) as sekme_a, \
         TestClient(app, base_url=KAYNAK, headers={"origin": KAYNAK}) as sekme_b:

        cevap = sekme_a.post("/hedef", data={"percent": "9"}, follow_redirects=False)
        hedef = cevap.headers["location"]

        # Araya giren başka bir sekme mesajı yutmamalı.
        assert "olarak ayarlandı" not in sekme_b.get("/islemler").text
        assert "olarak ayarlandı" in sekme_a.get(hedef).text


def test_bildirim_tek_kullanimlik(istemci):
    """Jeton bir kez okunur: yenilemede eski onay tekrar görünmez."""
    cevap = istemci.post("/hedef", data={"percent": "11"}, follow_redirects=False)
    hedef = cevap.headers["location"]
    assert "olarak ayarlandı" in istemci.get(hedef).text
    assert "olarak ayarlandı" not in istemci.get(hedef).text


# --- Diğer kapanan boşluklar ----------------------------------------------
def test_adet_duzelt_olmayan_fonu_yaratmaz(istemci, portfoy_dosyasi):
    """TEFAS kod doğrulaması ikinci bir kapıdan atlanabiliyordu."""
    html = gonder(istemci, "/adet-duzelt", code="ZZZ", units="10", onay="true")
    assert "portföyde yok" in html
    assert "ZZZ" not in storage.load(portfoy_dosyasi).positions


def test_eksik_zorunlu_alan_ham_422_dondurmez(istemci):
    """FastAPI'nin 422 JSON'u arayüzde ham hata ekranı olarak görünüyordu."""
    cevap = istemci.post(
        "/islemler",
        data={"code": "TMV", "tur": "alis"},          # units yok
        headers={"accept": "text/html", "referer": f"{KAYNAK}/islemler"},
        follow_redirects=False,
    )
    assert cevap.status_code == 303
    assert "zorunlu" in istemci.get(cevap.headers["location"]).text


def test_periyot_secilmezse_sessizce_varsayilan_kullanilmaz(istemci):
    html = gonder(istemci, "/raporlar", theme="light", excel="", charts="true")
    assert "en az bir periyot" in html


def test_veri_hatasi_bos_portfoyle_karistirilmaz(istemci, monkeypatch):
    """Ağ hatasında 'açık pozisyon yok' demek parasıyla ilgili yanlış bir iddia."""
    from portfoy.web.services import ServisHatasi

    def patla(self, yenile=False):
        raise ServisHatasi("Hiçbir fonun verisi alınamadı.")

    monkeypatch.setattr("portfoy.web.services.PortfoyServisi.analiz", patla)
    html = istemci.get("/").text
    assert "Fiyat verisi alınamadı" in html
    assert "Açık pozisyon yok" not in html
    assert "TMV" in html                     # kayıtlı pozisyon yine de anılıyor


def test_yazma_istegi_tefas_cekimini_beklemez(ornek_portfoy, portfoy_dosyasi, cikti_dizini):
    """Regresyon: `_kaydet` analiz kilidini alınca yazma, çekimin arkasında beklerdi."""
    import threading
    import time

    from portfoy.web.services import PortfoyServisi

    servis = PortfoyServisi(data_file=portfoy_dosyasi, output_dir=cikti_dizini)
    servis._analiz_kilidi.acquire()          # sürmekte olan bir çekimi taklit et
    try:
        bitti = threading.Event()
        threading.Thread(
            target=lambda: (servis.hedef_ayarla(7.0), bitti.set()), daemon=True
        ).start()
        assert bitti.wait(timeout=2.0), "yazma isteği analiz kilidinin arkasında bekledi"
    finally:
        servis._analiz_kilidi.release()

    assert storage.load(portfoy_dosyasi).target_monthly_return == pytest.approx(7.0)


def test_cikti_listesi_silinen_dosyada_patlamaz(istemci, cikti_dizini):
    """Şablonda `stat()` çağırmak, listeleme ile render arasında silinen dosyada 500'lerdi."""
    from portfoy.web.services import PortfoyServisi

    (cikti_dizini / "a.png").write_bytes(b"x")
    servis = PortfoyServisi(output_dir=cikti_dizini)
    assert [item.ad for item in servis.cikti_listesi()] == ["a.png"]

    (cikti_dizini / "a.png").unlink()
    assert servis.cikti_listesi() == []
    assert istemci.get("/raporlar").status_code == 200


# --- Emir (valör türetmeli) -------------------------------------------------
@pytest.fixture
def emir_istemci(istemci, monkeypatch):
    """TEFAS'a çıkmadan çalışan istemci: takvim ve kategori sabitlenir."""
    import pandas as pd

    from portfoy.tefas_client import FundHistory

    gunler = [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]
    seri = pd.Series(
        [4.02, 3.65, 3.23], index=pd.DatetimeIndex([pd.Timestamp(g) for g in gunler])
    )
    monkeypatch.setattr(
        "portfoy.web.services.tefas_client.fetch_history",
        lambda kod, **kw: FundHistory(code=kod, title=kod, source="test", prices=seri),
    )
    monkeypatch.setattr(
        "portfoy.web.services.tefas_client.fetch_category",
        lambda kod: "Hisse Senedi Fonu",
    )
    return istemci


def test_emir_kesim_sonrasi_dogru_gunu_turetir(emir_istemci):
    """Gerçek olay: 01.09 15:00 → T=02.09 → gerçekleşme 03.09 → nakit 04.09."""
    html = gonder(
        emir_istemci, "/emir", code="TMV", tur="satis", units="100",
        emir_tarihi="2026-09-01", saat="15:00",
    )
    assert "İşlem günü (T): 02.09.2026" in html
    assert "gerçekleşme (T+1): 03.09.2026" in html
    assert "Nakit (T+2): 04.09.2026" in html


def test_emir_kesim_oncesi_ayni_gun(emir_istemci):
    html = gonder(
        emir_istemci, "/emir", code="TMV", tur="satis", units="100",
        emir_tarihi="2026-09-01", saat="11:00",
    )
    assert "İşlem günü (T): 01.09.2026" in html
    assert "gerçekleşme (T+1): 02.09.2026" in html


def test_emir_saat_bilinmiyorsa_taraf_secilebilir(emir_istemci):
    html = gonder(
        emir_istemci, "/emir", code="TMV", tur="satis", units="100",
        emir_tarihi="2026-09-01", kesim_taraf="sonra",
    )
    assert "İşlem günü (T): 02.09.2026" in html


def test_emir_zaman_verilmezse_reddedilir(emir_istemci):
    html = gonder(
        emir_istemci, "/emir", code="TMV", tur="satis", units="100",
        emir_tarihi="2026-09-01",
    )
    assert "saat kaçta verdiğinizi belirtin" in html


def test_nakit_uyusmazliginda_emir_kaydedilmez(emir_istemci, portfoy_dosyasi):
    """Türetilen zincirle aracı kurum ekranı tutmuyorsa kaydetmeden dur."""
    html = gonder(
        emir_istemci, "/emir", code="TMV", tur="satis", units="100",
        emir_tarihi="2026-09-01", saat="11:00", nakit_tarihi="2026-09-04",
    )
    assert "Türetilen nakit günü" in html and "1 gün ileri" in html
    assert "Bekleyen emirler" not in html


def test_nakit_tutuyorsa_emir_kaydedilir(emir_istemci):
    html = gonder(
        emir_istemci, "/emir", code="TMV", tur="satis", units="100",
        emir_tarihi="2026-09-01", saat="15:00", nakit_tarihi="2026-09-04",
    )
    assert "türetilen zincir doğrulandı" in html
    assert "Bekleyen emirler (1)" in html


def test_bekleyen_satis_ayni_paylari_kilitler(emir_istemci):
    gonder(emir_istemci, "/emir", code="TMV", tur="satis", units="1500",
           emir_tarihi="2026-09-01", saat="15:00")
    html = gonder(emir_istemci, "/emir", code="TMV", tur="satis", units="100",
                  emir_tarihi="2026-09-01", saat="15:00")
    assert "bekleyen satış emrinde" in html


def test_bekleyen_emir_iptal_edilebilir(emir_istemci):
    gonder(emir_istemci, "/emir", code="TMV", tur="satis", units="100",
           emir_tarihi="2026-09-01", saat="15:00")
    emirler = emir_istemci.get("/api/portfoy").json()["bekleyen"]
    assert len(emirler) == 1

    html = gonder(emir_istemci, "/emir/sil", emir_id=emirler[0]["id"])
    assert "iptal edildi" in html
    assert emir_istemci.get("/api/portfoy").json()["bekleyen"] == []


def test_bekleyen_emirde_adetler_portfoyde_kalir(emir_istemci, portfoy_dosyasi):
    """Gerçekleşmeye kadar fiyat riski kullanıcıda; pozisyon düşürülmez."""
    gonder(emir_istemci, "/emir", code="TMV", tur="satis", units="1500",
           emir_tarihi="2026-09-01", saat="15:00")
    assert storage.load(portfoy_dosyasi).position("TMV").units == pytest.approx(1500)


# --- Valör kuralları -------------------------------------------------------
def test_valor_kurali_kaydedilir(istemci):
    html = gonder(istemci, "/valor", code="PHE", alis_valor="1", alis_nakit="1",
                  satis_valor="2", satis_nakit="3", dogrula="true")
    assert "satış T+2/nakit T+3" in html and "doğrulandı" in html


def test_valor_gun_sayisi_sinirli(istemci):
    html = gonder(istemci, "/valor", code="PHE", satis_valor="99")
    assert "0-10 iş günü" in html


def test_valor_gun_sayisi_sayi_olmali(istemci):
    html = gonder(istemci, "/valor", code="PHE", satis_valor="abc")
    assert "tam sayı olmalı" in html


def test_yan_dosyalar_portfoye_bagli(tmp_path):
    """Regresyon: valör/bekleyen sabit yola yazınca testler gerçek veriyi bozdu."""
    from portfoy import config
    from portfoy.web.services import PortfoyServisi

    varsayilan = config.yan_dosyalar(None)
    assert varsayilan["valor"] == config.VALOR_FILE
    assert varsayilan["bekleyen"] == config.PENDING_FILE

    alt = tmp_path / "alt.json"
    servis = PortfoyServisi(data_file=alt)
    for yol in (servis.valor_file, servis.pending_file, servis.snapshot_file):
        assert yol.parent == tmp_path, yol
        assert yol != config.VALOR_FILE and yol != config.PENDING_FILE


def test_valor_duzenlemesi_mevcut_kurali_ezmez(istemci):
    """Regresyon: rota sıfırdan kural kurup verilmeyen alanları sıfırlıyordu."""
    gonder(istemci, "/valor", code="PPF", alis_valor="0", alis_nakit="0",
           satis_valor="0", satis_nakit="1", dogrula="true")
    html = gonder(istemci, "/valor", code="PPF", alis_valor="0", alis_nakit="0",
                  satis_valor="0", satis_nakit="2")
    # Yalnız satis_nakit değişti; diğerleri 0 olarak kalmalı.
    assert "alış T+0/nakit T+0, satış T+0/nakit T+2" in html


def test_valor_rakami_degisince_dogrulama_duser(istemci):
    """Doğrulama ESKİ rakamlar içindi; yeni rakam teyit edilmedi."""
    gonder(istemci, "/valor", code="PPF", alis_valor="1", alis_nakit="1",
           satis_valor="1", satis_nakit="2", dogrula="true")
    html = gonder(istemci, "/valor", code="PPF", alis_valor="1", alis_nakit="1",
                  satis_valor="3", satis_nakit="2")
    assert "şüpheli" in html


def test_valor_ayni_deger_dogrulamayi_dusurmez(istemci):
    gonder(istemci, "/valor", code="PPF", alis_valor="1", alis_nakit="1",
           satis_valor="1", satis_nakit="2", dogrula="true")
    html = gonder(istemci, "/valor", code="PPF", alis_valor="1", alis_nakit="1",
                  satis_valor="1", satis_nakit="2")
    assert "doğrulandı" in html


def test_goreli_data_file_gercek_yan_dosyalari_kullanir(tmp_path, monkeypatch):
    """Regresyon: göreli yol PORTFOLIO_FILE'a eşit çıkmayıp gerçek geçmişi
    görünmez kılıyordu — duran bir satış emri raporda kaybolurdu."""
    from portfoy import config
    from portfoy.web.services import PortfoyServisi

    monkeypatch.chdir(config.BASE_DIR)
    goreli = pathlib.Path("veri/portfoy.json")
    servis = PortfoyServisi(data_file=goreli)

    assert servis.valor_file == config.VALOR_FILE
    assert servis.pending_file == config.PENDING_FILE
    assert servis.snapshot_file == config.SNAPSHOT_FILE


def test_kapanan_satir_tablo_toplamini_tutturur(istemci, ornek_portfoy):
    """Kapanan satır tabloda, gövdenin DIŞINDA ve TL'siyle birlikte görünür.

    Kapanmış pozisyon fon tablosundan tamamen dışlandığında, kolonu toplayan
    kullanıcı Toplam satırındaki rakama varamıyordu. Fikstürde PHE 02.09'da
    kapanıyor.
    """
    durum = istemci.get("/").text
    _, _, fon_tablosu = durum.partition("<h2>Fonlar</h2>")
    govde = fon_tablosu.split('<tbody class="holdingler">')[1].split("</tbody>")[0]
    tfoot = fon_tablosu.split("<tfoot>")[1].split("</tfoot>")[0]

    assert "PHE" not in govde                 # holding değil
    assert "kapanan-satir" in tfoot           # ama tabloda, gövdenin dışında
    assert "PHE (02.09)" in tfoot

    assert istemci.get("/api/durum").json()["kapanmis_pozisyonlar"] == [
        {"kod": "PHE", "kapanis": "2026-09-02"}
    ]


def _para(metin: str) -> float:
    """'+19.168,24 ₺' -> 19168.24 (Türkçe biçimden sayıya)."""
    ham = metin.replace(".", "").replace(",", ".").replace("₺", "").strip()
    return float(ham.replace("−", "-"))


def _gunluk_tl(satir_html: str) -> float | None:
    """Bir tablo satırının 'Günlük' hücresindeki TL değişimi.

    Kolon sırası: Fon, Adet, Fiyat, Değer, Ağırlık, GÜNLÜK, ... -> indeks 5.
    """
    hucreler = re.split(r"<t[dh]\b", satir_html)[1:]
    if len(hucreler) < 6:
        return None
    para = re.findall(r"[-+−][\d.]+,\d{2}\s*₺", hucreler[5])
    return _para(para[0]) if para else None


def test_kapanan_ve_holding_tl_toplami_toplam_satirini_tutar(istemci, ornek_portfoy):
    """Kullanıcının GÖRDÜĞÜ rakamlar toplanıyor mu — sayfadan okunarak.

    Kapanmış pozisyon tablodan tamamen dışlandığında, "Günlük" kolonunu
    toplayan kullanıcı Toplam satırındaki rakama varamıyordu. Bu test yalnızca
    sayfanın kendi çıktısına bakar; hesabı ikinci kez yapmaz.
    """
    _, _, fon_tablosu = istemci.get("/").text.partition("<h2>Fonlar</h2>")
    govde = fon_tablosu.split('<tbody class="holdingler">')[1].split("</tbody>")[0]
    tfoot = fon_tablosu.split("<tfoot>")[1].split("</tfoot>")[0]

    holdingler = [
        tl for satir in re.findall(r"<tr>.*?</tr>", govde, re.S)
        if (tl := _gunluk_tl(satir)) is not None
    ]
    assert holdingler, "holding satırı bulunamadı — şablon değişmiş olabilir"

    kapanan_satir = re.search(r'<tr class="kapanan-satir">.*?</tr>', tfoot, re.S)
    assert kapanan_satir, "kapanan pozisyon satırı tabloda yok"
    kapanan = _gunluk_tl(kapanan_satir.group(0))

    toplam_satir = re.findall(r"<tr>.*?</tr>", tfoot, re.S)[-1]
    toplam = _gunluk_tl(toplam_satir)

    assert sum(holdingler) + kapanan == pytest.approx(toplam, abs=0.02)


def test_akis_isareti_ve_kolon_aciklamasi_sayfada(istemci, ornek_portfoy):
    """Ayrışan hücre işaretli, açıklama satırları basılı.

    Fikstürde TMV 13.08'de ikinci alımını yapıyor (aylık pencerede), PHE ise
    02.09'da satılıyor. TMV'nin aylık hücresi işaretli olmalı.
    """
    from portfoy import config

    durum = istemci.get("/").text
    _, _, fon_tablosu = durum.partition("<h2>Fonlar</h2>")
    govde = fon_tablosu.split('<tbody class="holdingler">')[1].split("</tbody>")[0]

    tmv = next(tr for tr in re.findall(r"<tr>.*?</tr>", govde, re.S) if "TMV" in tr)
    hucreler = re.split(r"<t[dh]\b", tmv)[1:]
    assert "akis-isareti" in hucreler[7]      # aylık kolonu (indeks 7)
    assert config.FLOW_MARK in hucreler[7]

    assert config.COLUMN_BASIS_NOTE in durum
    assert config.FLOW_MARK_NOTE in durum
    assert config.TOTAL_BASIS_NOTE in durum
