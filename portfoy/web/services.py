"""Web ile CLI arasindaki ortak is katmani.

Sorumluluklari:

* **Es zamanlilik.** FastAPI senkron uc noktalari bir is parcacigi havuzunda
  kosturur, yani iki istek gercekten ayni anda calisabilir. Portfoy dosyasi
  oku-degistir-yaz dongusuyle guncellendigi icin kilitsiz birakmak, iki sekmeden
  arka arkaya girilen islemden birinin sessizce kaybolmasi demekti.
* **Onbellek.** Her sayfa yuklemesinde TEFAS'a gitmek arayuzu kullanilmaz
  yapardi. Analiz bir sure saklanir; portfoy her degistiginde ANINDA gecersiz
  kilinir - bayat rakam gostermek yavas gostermekten kotudur.
* **Portfoy durumuna bagli dogrulama.** "Elimde bu kadar yok" gibi kurallar
  burada, veriyi kilit altinda okurken uygulanir; Pydantic'te olsalardi
  dogrulama ile yazma arasinda yaris olusurdu.

Hesap kurallarinin kendisi burada DEGIL: analytics, storage, charts, mailer
neyse o kullanilir. Ikinci bir kopya kacinilmaz olarak ayrisir.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .. import (analytics, bekleyen, charts, config, excel_report, mailer,
                snapshots, storage, tefas_client, valor)
from ..formatting import fmt_money, fmt_units
from ..storage import Portfolio, StorageError

logger = logging.getLogger(__name__)

# Analiz onbelleginin omru. TEFAS gun icinde tek fiyat yayimladigi icin uzun
# tutulabilirdi; kisa tutmanin sebebi fiyatin degil, PORTFOYUN disaridan
# (CLI'dan, elle) degismis olabilmesi.
ANALIZ_TTL_SANIYE = 10 * 60


class ServisHatasi(RuntimeError):
    """Kullaniciya oldugu gibi gosterilebilecek, beklenen hata."""


@dataclass
class CiktiDosyasi:
    """Uretilmis bir rapor/grafik dosyasi ve okuma anindaki bilgisi."""

    yol: Path
    boyut: int
    degistirildi: float

    @property
    def ad(self) -> str:
        return self.yol.name

    @property
    def png_mi(self) -> bool:
        return self.yol.suffix.lower() == ".png"

    @property
    def baslik(self) -> str:
        return self.yol.stem.replace("_", " ")

    @property
    def kb(self) -> int:
        return round(self.boyut / 1024)


@dataclass
class AnalizSonucu:
    analysis: analytics.PortfolioAnalysis
    track_record: list
    uretildi: float          # time.monotonic()
    uyarilar: list[str]

    @property
    def yas_saniye(self) -> float:
        return time.monotonic() - self.uretildi


class PortfoyServisi:
    """Tek bir portfoy dosyasi uzerinde calisan servis."""

    def __init__(
        self,
        data_file: Path | None = None,
        output_dir: Path | None = None,
        lookback_days: int | None = None,
    ) -> None:
        self.data_file = data_file
        self.output_dir = output_dir or config.OUTPUT_DIR
        # Gecmis, bekleyen emirler ve valor kurallari portfoye baglidir; yol
        # kurali CLI ile ORTAK (config.yan_dosyalar). Iki ayri kopya yazilsaydi
        # ayrisirlar ve hata "yanlis dosyaya yazildi" seklinde, yani en gec
        # fark edilen bicimde ortaya cikardi.
        yollar = config.yan_dosyalar(data_file)
        self.snapshot_file = yollar["gecmis"]
        self.pending_file = yollar["bekleyen"]
        self.valor_file = yollar["valor"]
        self.lookback_days = lookback_days or config.LOOKBACK_DAYS

        # Yazma kilidi: oku-degistir-yaz dongusunun tamamini kapsar.
        self._yazma = threading.RLock()
        # Analiz kilidi ayri: uzun suren TEFAS cekimi yazmalari bloklamasin.
        # Bu ayrimin gecerli olmasi icin YAZMA YOLU BU KILIDI ALMAMALI -
        # alsaydi (onbellek bosaltirken oldugu gibi) yazan istek cekimin
        # arkasinda beklerdi ve ayrica ters kilit sirasi riski dogardi.
        self._analiz_kilidi = threading.RLock()
        self._analiz: AnalizSonucu | None = None
        # Son analizde gerceklesen bekleyen emirlerin ozeti; arayuz bunu
        # bildirim olarak gosterir.
        self.son_cozulenler: list[str] = []
        # matplotlib.pyplot is parcacigi guvenli degil; grafik uretimi tek
        # siraya alinir. Tek kullanicilik arayuzde pratik maliyeti yok.
        self._cizim = threading.Lock()

    # -- Portfoy okuma / yazma --------------------------------------------
    def portfoy(self) -> Portfolio:
        try:
            return storage.load(self.data_file)
        except StorageError as exc:
            raise ServisHatasi(str(exc)) from exc

    def _kaydet(self, portfolio: Portfolio) -> None:
        try:
            storage.save(portfolio, self.data_file)
        except StorageError as exc:
            raise ServisHatasi(str(exc)) from exc
        self.onbellegi_bosalt()

    def onbellegi_bosalt(self) -> None:
        """Onbellegi gecersiz kilar.

        Kilit ALMAZ: tek islem bir oznitelik atamasi ve CPython'da bu atomik.
        Kilit alsaydi `_kaydet` (yazma kilidi altinda calisir) suren bir TEFAS
        cekiminin arkasinda bekler, ustelik `_yazma -> _analiz_kilidi` sirasini
        kalicilastirirdi.
        """
        self._analiz = None

    # -- Portfoy durumuna bagli dogrulama ---------------------------------
    @staticmethod
    def _satis_dogrula(portfolio: Portfolio, code: str, adet: float) -> None:
        """Elde olmayani satmayi, anlasilir bir mesajla engeller.

        `storage.add_lot` de reddediyor ama mesaji gelistiriciye gore yazilmis;
        arayuzde kullanicinin ne yapabilecegini soylemek gerekiyor.
        """
        position = portfolio.positions.get(code)
        if position is None:
            raise ServisHatasi(
                f"{code} portföyde yok, satılamaz. Önce alış kaydı girin."
            )
        if position.is_closed:
            raise ServisHatasi(
                f"{code} zaten kapanmış (tamamı satılmış). İşlem geçmişi "
                f"'İşlemler' sayfasında duruyor."
            )
        if adet > position.units + storage.EPSILON:
            raise ServisHatasi(
                f"{code} için elinizde {fmt_units(position.units)} adet var; "
                f"{fmt_units(adet)} adet satılamaz."
            )

    def fon_taninmiyor_mu(self, code: str) -> bool | None:
        """TEFAS bu kodu taniyor mu? Belirlenemezse None.

        None ile False'u ayirmak onemli: internet yoksa "bu fon yok" demek,
        dogru girilmis bir kodu reddetmek olurdu. Yalnizca KESIN bilinmeyen
        kodda True doner ve arayuz "yine de ekle" onayi ister.
        """
        try:
            tefas_client.fetch_history(code, lookback_days=30)
        except tefas_client.TefasError:
            return True
        except Exception as exc:  # ag hatasi, zaman asimi: karar veremiyoruz
            logger.warning("%s dogrulanamadi: %s", code, exc)
            return None
        return False

    # -- Mutasyonlar -------------------------------------------------------
    def islem_ekle(
        self, code: str, signed_units: float, on: date | None, price: float | None
    ) -> str:
        """Alis/satis lotu ekler. Kullaniciya gosterilecek ozeti dondurur."""
        with self._yazma:
            portfolio = self.portfoy()
            if signed_units < 0:
                self._satis_dogrula(portfolio, code, -signed_units)

            onceki = portfolio.positions.get(code)
            onceki_adet = onceki.units if onceki else 0.0
            try:
                toplam = portfolio.add_lot(code, signed_units, on, price)
            except ValueError as exc:
                raise ServisHatasi(str(exc)) from exc
            self._kaydet(portfolio)

            tur = "Satış" if signed_units < 0 else "Alış"
            ozet = (
                f"{code}: {tur} {fmt_units(abs(signed_units))} adet kaydedildi. "
                f"{fmt_units(onceki_adet)} → {fmt_units(toplam)} adet."
            )
            position = portfolio.positions.get(code)
            if position is not None and position.is_closed:
                ozet += " Pozisyon KAPANDI; raporlarda artık yer almayacak."
            return ozet

    def adet_duzelt(
        self, code: str, units: float, on: date | None, price: float | None
    ) -> str:
        with self._yazma:
            portfolio = self.portfoy()
            onceki = portfolio.positions.get(code)
            if onceki is None:
                # Bu form "mevcut kaydi duzelt" icindir. Yeni fon yaratmasina
                # izin vermek, alis formundaki TEFAS kod dogrulamasini ikinci
                # bir kapidan atlatmak olurdu.
                raise ServisHatasi(
                    f"{code} portföyde yok. Yeni fon için İşlemler sayfasındaki "
                    f"alış formunu kullanın."
                )
            lot_sayisi = len(onceki.lots)
            try:
                portfolio.set_units(code, units, on, price)
            except ValueError as exc:
                raise ServisHatasi(str(exc)) from exc
            self._kaydet(portfolio)

            ozet = f"{code} adedi {fmt_units(units)} olarak ayarlandı."
            if lot_sayisi > 1:
                ozet += f" Önceki {lot_sayisi} işlem kaydı tek kayda indirildi."
            return ozet

    def fon_sil(self, code: str) -> str:
        with self._yazma:
            portfolio = self.portfoy()
            position = portfolio.positions.get(code)
            if position is None:
                raise ServisHatasi(f"{code} portföyde bulunamadı.")
            lot_sayisi = len(position.lots)
            portfolio.remove(code)
            self._kaydet(portfolio)
            return f"{code} silindi ({lot_sayisi} işlem kaydıyla birlikte)."

    def hedef_ayarla(self, percent: float) -> str:
        with self._yazma:
            portfolio = self.portfoy()
            portfolio.target_monthly_return = percent
            self._kaydet(portfolio)
            return f"Hedef aylık getiri %{percent:g} olarak ayarlandı."

    # -- Valor ve bekleyen emirler ----------------------------------------
    def valor_kurallari(self) -> dict:
        return valor.yukle(self.valor_file)

    def valor_kurali(self, kod: str, kategori: str = "") -> valor.ValorKurali:
        """Kayitli kural, yoksa kategori varsayilani (supheli isaretli)."""
        return valor.kural_ver(kod, self.valor_kurallari(), kategori)

    def valor_kaydet(
        self, kod: str, alanlar: dict, dogrula: bool = False
    ) -> str:
        """Verilen alanlari MEVCUT kuralin uzerine yazar (birlestirir).

        Sifirdan kural kurmak kategoriyi kaybettirir ve verilmeyen alanlari
        sessizce varsayilana dondururdu: Para Piyasasi fonunun (0,0,0,1)
        kuralinda tek alani duzeltmek isteyen kullanici digerlerini farkinda
        olmadan 1/1/1/2 yapardi. CLI de birlestirdigi icin iki arayuz ayni
        anlama gelmis olur.
        """
        import dataclasses

        with self._yazma:
            kurallar = self.valor_kurallari()
            mevcut = kurallar.get(kod) or valor.kategori_kurali(
                tefas_client.fetch_category(kod)
            )
            degisen = {
                ad: deger for ad, deger in alanlar.items()
                if deger is not None and getattr(mevcut, ad) != deger
            }
            kural = mevcut
            if degisen:
                # Dogrulama ESKI rakamlar icindi; rakam degistiyse duser.
                kural = dataclasses.replace(
                    kural, **degisen, kaynak="elle", dogrulandi=None
                )
            if dogrula:
                kural = valor.dogrula(kural)
            kurallar[kod] = kural
            valor.kaydet(kurallar, self.valor_file)
        durum = ("doğrulandı" if not kural.supheli else "şüpheli — henüz doğrulanmadı")
        return (
            f"{kod} valör kuralı kaydedildi: alış T+{kural.alis_valor}/"
            f"nakit T+{kural.alis_nakit}, satış T+{kural.satis_valor}/"
            f"nakit T+{kural.satis_nakit} ({durum})."
        )

    def bekleyen_emirler(self) -> list:
        try:
            return bekleyen.yukle(self.pending_file)
        except bekleyen.BekleyenHatasi as exc:
            raise ServisHatasi(str(exc)) from exc

    def emir_cozumle(
        self, kod: str, satis: bool, emir_zamani, kesim_sonrasi: bool | None
    ) -> tuple[valor.Cozum, object]:
        """Emir zamanindan tarih zincirini turetir. Doner: (cozum, fiyat gecmisi).

        Is gunu takvimi TEFAS serisinden turetilir - ayri bir tatil tablosu
        tutulmaz, bkz. valor.IslemTakvimi.
        """
        try:
            history = tefas_client.fetch_history(
                kod, lookback_days=self.lookback_days
            )
        except tefas_client.TefasError as exc:
            raise ServisHatasi(
                f"{kod} fiyat geçmişi alınamadı ({exc}). İş günü takvimi bu "
                f"seriden türetildiği için valör hesaplanamıyor."
            ) from exc

        takvim = valor.IslemTakvimi.seriden(
            [ts.date() for ts in history.prices.index]
        )
        kural = self.valor_kurali(kod, tefas_client.fetch_category(kod))
        try:
            cozum = valor.cozumle(
                kural, takvim, emir_zamani, satis=satis,
                kesim_sonrasi=kesim_sonrasi,
            )
        except valor.ValorHatasi as exc:
            raise ServisHatasi(str(exc)) from exc
        return cozum, history

    def emir_ekle(
        self, kod: str, signed_units: float | None, emir_zamani, kesim_sonrasi,
        beklenen_nakit=None, fiyat: float | None = None,
        tutar: float | None = None,
    ) -> tuple[str, list[str]]:
        """Bekleyen emir olusturur. Doner: (ozet, aciklama satirlari).

        `signed_units` ya da `tutar`dan biri verilir; TL ile verilen emirde adet
        islem gunu fiyati yayimlaninca hesaplanir (bkz. bekleyen.coz).
        """
        satis = signed_units is not None and signed_units < 0
        cozum, history = self.emir_cozumle(kod, satis, emir_zamani, kesim_sonrasi)

        # Mutabakat: nakit tarihi turetilen zincirin dogrulanabilir tek ucudur.
        # Tutmuyorsa ya nakit kurali ya emir saati yanlistir. Ikincisi ISLEM
        # GUNUNU, dolayisiyla FIYAT gununu kaydirir; birincisi fiyata dokunmaz
        # ama zincirin dogrulanmis tek ucunu curutur. Iki halde de kaydetmeden
        # once duruyoruz: dogrulanmamis bir zinciri sessizce yazmak, hatanin
        # aylar sonra fark edilmesi demekti.
        uyusmazlik = valor.nakit_uyusmazligi(cozum, beklenen_nakit)
        if uyusmazlik:
            raise ServisHatasi(uyusmazlik)

        with self._yazma:
            portfolio = self.portfoy()
            emirler = self.bekleyen_emirler()
            try:
                if satis:
                    bekleyen.satis_dogrula(portfolio, emirler, kod, -signed_units)
                emir = bekleyen.emir_olustur(
                    kod, signed_units, cozum, emir_zamani, fiyat=fiyat,
                    tutar=tutar,
                )
                emirler.append(emir)
                bekleyen.kaydet(emirler, self.pending_file)
            except bekleyen.BekleyenHatasi as exc:
                raise ServisHatasi(str(exc)) from exc

        aciklama = cozum.anlat(satis=satis)
        if beklenen_nakit is not None:
            aciklama.append("✓ Nakit tarihi tutuyor — türetilen zincir doğrulandı.")
        if cozum.gerceklesme > history.latest_date:
            if tutar is not None:
                # Alista adet HENUZ YOK; "portfoyde kalir" demek yanlis olurdu.
                aciklama.append(
                    f"{cozum.gerceklesme:%d.%m.%Y} değerleme fiyatı "
                    f"yayımlandığında işlem kaydına dönüşecek. Adet o gün "
                    f"{fmt_money(tutar)} ÷ kapanış fiyatı olarak hesaplanacak; "
                    f"o güne kadar portföye girmez."
                )
            elif satis:
                # "Portfoyde kalir" YALNIZCA satista dogru: satilan paylar
                # gerceklesmeye kadar sizde durur ve fiyat riskini tasirsiniz.
                # Bekleyen bir ALIS ise portfoyde degil (lot ancak `coz` ile
                # eklenir); ayni cumleyi orada yazmak olmayan paylari var
                # gostermek olurdu.
                aciklama.append(
                    f"{cozum.gerceklesme:%d.%m.%Y} değerleme fiyatı yayımlandığında "
                    f"işlem kaydına dönüşecek. Adetler o güne kadar portföyde kalır "
                    f"— fiyat riski sizde."
                )
            else:
                aciklama.append(
                    f"{cozum.gerceklesme:%d.%m.%Y} değerleme fiyatı yayımlandığında "
                    f"işlem kaydına dönüşecek; o güne kadar portföye girmez."
                )
        return emir.ozet(), aciklama

    def emir_sil(self, emir_id: str) -> str:
        with self._yazma:
            emirler = self.bekleyen_emirler()
            kalan = [item for item in emirler if item.id != emir_id]
            if len(kalan) == len(emirler):
                raise ServisHatasi("Bekleyen emir bulunamadı.")
            bekleyen.kaydet(kalan, self.pending_file)
        return "Bekleyen emir iptal edildi."

    # -- Analiz ------------------------------------------------------------
    def analiz(self, yenile: bool = False) -> AnalizSonucu | None:
        """Guncel analiz; acik pozisyon yoksa None.

        Onbellek TTL doldugunda veya `yenile` ile bastan uretilir.
        """
        with self._analiz_kilidi:
            if (
                not yenile
                and self._analiz is not None
                and self._analiz.yas_saniye < ANALIZ_TTL_SANIYE
            ):
                return self._analiz

            portfolio = self.portfoy()
            if portfolio.is_empty():
                self._analiz = None
                return None

            # Yakin zamanda kapanmis fonlar da cekilir: pencere icinde satilan
            # fonun getirisi portfoy rakamina giriyor (storage.codes_for_pricing).
            histories, failures = tefas_client.fetch_many(
                portfolio.codes_for_pricing(self.lookback_days),
                lookback_days=self.lookback_days,
                use_cache=not yenile,
            )
            if not histories:
                raise ServisHatasi(
                    "Hiçbir fonun verisi alınamadı. İnternet bağlantısını "
                    "kontrol edin; TEFAS geçici olarak erişilemiyor olabilir."
                )

            # Gerceklesmis emirleri fiyat gelir gelmez isle; analiz guncel
            # portfoyu gormeli.
            #
            # `_yazma` SART: cozulme bekleyen dosyasina oku-degistir-yaz yapar
            # ve `emir_ekle`/`emir_sil` de ayni dosyayi yazar. Yalnizca
            # `_analiz_kilidi` altinda kalsaydi ayni dosyayi iki farkli kilit
            # korur, yani hic korumazdi: analiz is parcacigi bayat listeyi geri
            # yazip arada eklenen emri silerdi - kullanici "kaydedildi"
            # bildirimi gorur, emir yok olurdu.
            #
            # Kilit sirasi `_analiz_kilidi -> _yazma`; ters yol yok cunku yazma
            # yolu analiz kilidini hic almiyor (bkz. onbellegi_bosalt).
            # TEFAS cekimi bilerek DISARIDA kaldi.
            with self._yazma:
                portfolio = self.portfoy()   # kilit altinda tazele
                self.son_cozulenler = [
                    item.ozet()
                    for item in bekleyen.coz_ve_kaydet(
                        portfolio, histories,
                        bekleyen_yolu=self.pending_file,
                        portfoy_yolu=self.data_file,
                    )
                ]
            analysis = analytics.analyze(portfolio, histories, failures)
            # Gunun fotografini gecmise yaz: karnenin "gerceklesen" kolu boyle
            # birikir. Ayni tarihe ikinci yazim kaydi gunceller, cogaltmaz.
            snapshots.record(analysis, self.snapshot_file)

            track = analytics.monthly_track_record(
                analysis, snapshots.load(self.snapshot_file)
            )
            uyarilar = [
                f"{code}: {mesaj}" for code, mesaj in sorted(analysis.failures.items())
            ]
            self._analiz = AnalizSonucu(analysis, track, time.monotonic(), uyarilar)
            return self._analiz

    # -- Ciktilar ----------------------------------------------------------
    def cikti_uret(
        self,
        theme: str = "light",
        periods: tuple[str, ...] = ("haftalik", "aylik"),
        excel: bool = True,
        grafik: bool = True,
    ) -> list[Path]:
        sonuc = self.analiz()
        if sonuc is None:
            raise ServisHatasi("Açık pozisyon yok; rapor üretilemiyor.")

        uretilen: list[Path] = []
        with self._cizim:
            if excel:
                path = excel_report.build_report(sonuc.analysis, self.output_dir)
                if path is not None:
                    uretilen.append(path)
            if grafik:
                uretilen += charts.render_all(
                    sonuc.analysis,
                    self.output_dir,
                    theme=theme,
                    show=False,
                    periods=periods,
                    track_record=sonuc.track_record,
                )
        return uretilen

    def cikti_listesi(self) -> list["CiktiDosyasi"]:
        """Cikti klasorundeki dosyalar, yeniden eskiye.

        Boyut ve tarih BURADA okunur, sablonda degil: dosya listeleme ile
        render arasinda silinirse `stat()` cagrisi sablonda patlar ve sayfa
        500 doner. Okunamayan dosya sessizce atlanir.
        """
        if not self.output_dir.exists():
            return []
        dosyalar: list[CiktiDosyasi] = []
        for item in self.output_dir.iterdir():
            if item.suffix.lower() not in {".png", ".xlsx"}:
                continue
            try:
                bilgi = item.stat()
            except OSError:  # listelemeden sonra silinmis
                continue
            if not item.is_file():
                continue
            dosyalar.append(
                CiktiDosyasi(yol=item, boyut=bilgi.st_size, degistirildi=bilgi.st_mtime)
            )
        return sorted(dosyalar, key=lambda item: item.degistirildi, reverse=True)

    def cikti_yolu(self, ad: str) -> Path:
        """Indirme icin guvenli dosya yolu.

        Ad kullanicidan geliyor. `..` ve mutlak yol denemeleri, cozulen yolun
        cikti klasorunun ALTINDA kalmasi sartiyla eleniyor; ad uzerinde desen
        kontrolu yapmak yerine sonucu dogrulamak, kacirilan bir kacis dizisi
        birakmiyor.
        """
        kok = self.output_dir.resolve()
        hedef = (kok / ad).resolve()
        if hedef.parent != kok or not hedef.is_file():
            raise ServisHatasi(f"Dosya bulunamadı: {ad}")
        if hedef.suffix.lower() not in {".png", ".xlsx"}:
            raise ServisHatasi("Yalnızca rapor ve grafik dosyaları indirilebilir.")
        return hedef

    # -- E-posta -----------------------------------------------------------
    def eposta_ayari(self) -> mailer.MailConfig:
        return mailer.load_config()

    def eposta_kaydet(
        self, user: str, recipients: list[str], password: str, host: str, port: int
    ) -> str:
        with self._yazma:
            try:
                path, nerede = mailer.save_config(
                    user=user,
                    recipients=recipients,
                    password=password or None,
                    host=host,
                    port=port,
                )
            except (OSError, mailer.MailError) as exc:
                raise ServisHatasi(f"E-posta ayarları kaydedilemedi: {exc}") from exc
            return f"Ayarlar kaydedildi ({path.name}). Şifre: {nerede}."

    def eposta_gonder(self, ekler: list[Path] | None = None) -> str:
        sonuc = self.analiz()
        if sonuc is None:
            raise ServisHatasi("Açık pozisyon yok; gönderilecek rapor üretilemiyor.")

        settings = self.eposta_ayari()
        if not settings.is_ready:
            eksik = ", ".join(settings.missing_fields())
            raise ServisHatasi(f"E-posta ayarları eksik: {eksik}")

        try:
            alicilar = mailer.send_report(
                sonuc.analysis, ekler or [], settings, sonuc.track_record
            )
        except mailer.MailError as exc:
            raise ServisHatasi(str(exc)) from exc
        return f"E-posta gönderildi → {', '.join(alicilar)}"
