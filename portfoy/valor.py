"""Fon valor kurallari: emir zamanindan gerceklesme ve nakit gununu turetir.

Neden gerekli: TEFAS'ta satis emri, emrin verildigi gunun fiyatindan
GERCEKLESMEZ. Emir once kesim saatine gore bir islem gunune baglanir (13:30'dan
sonra verilen emir ertesi is gunune kalir), sonra fonun valorune gore ileri bir
degerleme gununun fiyatindan islenir; nakit ondan da sonra hesaba gecer.

Bu aritmetigi kullanicinin kafasinda yapmasi, portfoyun en pahali hata
kaynagidir: bir gun kayma, o gunun tum fiyat hareketini yanlis tarafa yazar.

TASARIM KARARI - tablo kaydi DOLDURUR, hesabi SURMEZ. Turetilen tarih islem
kaydina bir kez yazilir ve orada donar; raporlar yalnizca yazili tarihi okur.
Valor tablosu sonradan duzeltilse bile gecmis kayitlar kipirdamaz. Tersi
yapilsaydi (her hesapta yeniden turetme) tabloda yapilan tek bir duzeltme,
aylar oncesinin karnesini sessizce degistirirdi.

TEFAS valor bilgisini YAYINLAMAZ: calisan `fonBilgiGetir` ucu 11 alan doner ve
valor bunlarin arasinda yoktur, fon detay sayfasi da JavaScript korumasinin
arkasindadir. Dolayisiyla tablo elle beslenir; kategori varsayilanlari yalnizca
bir baslangic noktasidir ve DOGRULANANA KADAR supheli sayilir (bkz.
`ValorKurali.supheli`). Yanlis bir valor tablosu, tablo olmamasindan kotudur:
elle yapilan hata ara sira olur ve fark edilir, yanlis tablo her islemde
kendinden emin ve sessiz bir hata uretir. `nakit_uyusmazligi` bu yuzden var.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from . import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# TEFAS'in standart emir kesim saati. Araci kurumlar daha erken bir ic kesim
# uygulayabildigi icin ayarlanabilir birakildi.
VARSAYILAN_KESIM = time(13, 30)

# Kategori varsayilanlari. Anahtarlar TEFAS'in `fonBilgiGetir` ucundaki
# `fonKategori` degerleridir; boylece yeni bir fon eklendiginde kategori
# otomatik gelir. Degerler YAYGIN uygulamadir, garanti degil - hepsi
# `dogrulandi=None` ile baslar ve arayuz bunu "şüpheli" diye isaretler.
KATEGORI_VARSAYILANLARI: dict[str, tuple[int, int, int, int]] = {
    # kategori: (alis_valor, alis_nakit, satis_valor, satis_nakit)
    "Hisse Senedi Fonu": (1, 1, 1, 2),
    "Serbest Fon": (1, 1, 1, 2),
    "Borçlanma Araçları Fonu": (1, 1, 1, 2),
    "Katılım Fonu": (1, 1, 1, 2),
    "Fon Sepeti Fonu": (1, 1, 1, 2),
    "Kıymetli Madenler Fonu": (1, 1, 1, 2),
    "Karma Fon": (1, 1, 1, 2),
    "Değişken Fon": (1, 1, 1, 2),
    # Para piyasasi fonlari ayni gun islenir ve nakit ertesi gun gecer.
    "Para Piyasası Fonu": (0, 0, 0, 1),
    "Kısa Vadeli Borçlanma Araçları Fonu": (0, 0, 0, 1),
}

# Kategorisi bilinmeyen fon icin. En yaygin sema; yine supheli isaretlenir.
GENEL_VARSAYILAN = (1, 1, 1, 2)


class ValorHatasi(RuntimeError):
    """Valor cozumlemesi yapilamadiginda firlatilir."""


# --------------------------------------------------------------------------
# Kural
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ValorKurali:
    """Bir fonun valor semasi. Sayilar IS GUNU cinsindendir.

    `satis_valor=1` -> emir islem gunu T ise, T+1 is gununun degerleme
    fiyatindan gerceklesir. `satis_nakit=2` -> nakit T+2'de hesaptadir.
    """

    alis_valor: int = 1
    alis_nakit: int = 1
    satis_valor: int = 1
    satis_nakit: int = 2
    kategori: str = ""
    kaynak: str = "kategori-varsayilani"
    dogrulandi: date | None = None

    @property
    def supheli(self) -> bool:
        """Henuz dogrulanmamis kural. Turetilen tarihler uyariyla sunulur."""
        return self.dogrulandi is None

    def valor(self, satis: bool) -> int:
        return self.satis_valor if satis else self.alis_valor

    def nakit(self, satis: bool) -> int:
        return self.satis_nakit if satis else self.alis_nakit

    def to_dict(self) -> dict:
        payload = {
            "alis_valor": self.alis_valor,
            "alis_nakit": self.alis_nakit,
            "satis_valor": self.satis_valor,
            "satis_nakit": self.satis_nakit,
            "kaynak": self.kaynak,
        }
        if self.kategori:
            payload["kategori"] = self.kategori
        if self.dogrulandi is not None:
            payload["dogrulandi"] = self.dogrulandi.isoformat()
        return payload

    @classmethod
    def from_dict(cls, raw: dict, kod: str) -> "ValorKurali":
        def gun_sayisi(ad: str, varsayilan: int) -> int:
            try:
                sayi = int(raw.get(ad, varsayilan))
            except (TypeError, ValueError):
                raise ValorHatasi(f"{kod}: '{ad}' bir tam sayı olmalı.") from None
            if not 0 <= sayi <= 10:
                raise ValorHatasi(f"{kod}: '{ad}' 0-10 iş günü arasında olmalı.")
            return sayi

        ham_tarih = raw.get("dogrulandi")
        try:
            dogrulandi = date.fromisoformat(ham_tarih) if ham_tarih else None
        except ValueError:
            raise ValorHatasi(f"{kod}: 'dogrulandi' tarihi anlaşılamadı.") from None

        return cls(
            alis_valor=gun_sayisi("alis_valor", 1),
            alis_nakit=gun_sayisi("alis_nakit", 1),
            satis_valor=gun_sayisi("satis_valor", 1),
            satis_nakit=gun_sayisi("satis_nakit", 2),
            kategori=str(raw.get("kategori", "")),
            kaynak=str(raw.get("kaynak", "elle")),
            dogrulandi=dogrulandi,
        )


def kategori_kurali(kategori: str) -> ValorKurali:
    """Kategoriye gore baslangic kurali. Her zaman `supheli` doner."""
    alis_v, alis_n, satis_v, satis_n = KATEGORI_VARSAYILANLARI.get(
        kategori.strip(), GENEL_VARSAYILAN
    )
    return ValorKurali(
        alis_valor=alis_v, alis_nakit=alis_n,
        satis_valor=satis_v, satis_nakit=satis_n,
        kategori=kategori.strip(),
        kaynak="kategori-varsayilani",
        dogrulandi=None,
    )


# --------------------------------------------------------------------------
# Islem gunu takvimi
# --------------------------------------------------------------------------
@dataclass
class IslemTakvimi:
    """TEFAS fiyat serisinden turetilen is gunu takvimi.

    Ayri bir tatil tablosu TUTULMAZ: TEFAS hafta sonu ve resmi tatillerde fiyat
    yayimlamadigi icin serinin kendisi zaten islem gunu takvimidir. Bayram,
    yarim gun, idari tatil - hepsi kendiliginden dogru cikar ve takvim her veri
    cekiminde guncellenir.

    Serinin BITISINDEN sonrasi icin hafta sonu kuralina duselir; o araliktaki
    resmi tatiller bilinemez, bu yuzden sonuc `kesin=False` ile isaretlenir.
    Beklemeye alinan emir, fiyat yayimlandiginda gercek takvime gore yeniden
    hizalanir (bkz. bekleyen.py), dolayisiyla tahmin kalici hale gelmez.
    """

    gunler: tuple[date, ...]

    @classmethod
    def seriden(cls, tarihler: Iterable[date]) -> "IslemTakvimi":
        return cls(tuple(sorted({item for item in tarihler})))

    @property
    def son(self) -> date | None:
        return self.gunler[-1] if self.gunler else None

    @property
    def ilk(self) -> date | None:
        return self.gunler[0] if self.gunler else None

    def kapsiyor_mu(self, gun: date) -> bool:
        """Gun, takvimin BASLANGICINDAN sonra mi.

        Oncesi kapsanmaz: seride olmayan bir gunun ileri hizalamasi serinin ilk
        gunune yapisir ve aylarca kayma uretir - ustelik `kesin=True` gorunur,
        yani uyari da vermez.
        """
        return bool(self.gunler) and gun >= self.gunler[0]

    def _ileri_hizala(self, gun: date) -> tuple[date, bool]:
        """Gunu takvimdeki ilk islem gunune (kendisi dahil) tasir."""
        if self.gunler and gun < self.gunler[0]:
            # Seriden once: hangi gunlerin islem gunu oldugunu bilmiyoruz.
            # Serinin ilk gunune yapistirmak sessiz bir aylik kayma olurdu.
            return self.gunler[0], False
        for aday in self.gunler:
            if aday >= gun:
                return aday, True
        # Serinin otesi: hafta sonunu atla, tatilleri bilemeyiz.
        aday = gun
        while aday.weekday() >= 5:
            aday += timedelta(days=1)
        return aday, False

    def seans_gunu_mu(self, gun: date) -> bool:
        """Gun bir islem gunu mu (seri otesi icin hafta sonu kuralina duser)."""
        if self.icerir(gun):
            return True
        # Seri henuz oraya ulasmadiysa hafta ici olmasi yeterli; resmi tatili
        # bilemeyiz ama bu, kesim kaydirmasini uygulamak icin dogru yaklasim.
        if self.son is not None and gun > self.son:
            return gun.weekday() < 5
        return False

    def ekle(self, gun: date, adim: int) -> tuple[date, bool]:
        """`gun`den `adim` is gunu ileri. Doner: (tarih, kesin_mi)."""
        if adim < 0:
            raise ValueError("Valör gün sayısı negatif olamaz.")
        gecerli, kesin = self._ileri_hizala(gun)
        for _ in range(adim):
            gecerli, adim_kesin = self._sonraki(gecerli)
            kesin = kesin and adim_kesin
        return gecerli, kesin

    def _sonraki(self, gun: date) -> tuple[date, bool]:
        for aday in self.gunler:
            if aday > gun:
                return aday, True
        aday = gun + timedelta(days=1)
        while aday.weekday() >= 5:
            aday += timedelta(days=1)
        return aday, False

    def icerir(self, gun: date) -> bool:
        return gun in self.gunler


# --------------------------------------------------------------------------
# Cozumleme
# --------------------------------------------------------------------------
@dataclass
class Cozum:
    """Emir zamanindan turetilen tarih zinciri."""

    islem_gunu: date          # emrin bagli oldugu is gunu (T)
    gerceklesme: date         # fiyatin alindigi degerleme gunu
    nakit: date               # paranin hesaba gectigi gun
    kesim_sonrasi: bool       # emir kesim saatinden sonra mi verildi
    # Kesinlik AYRI izlenir: nakit gunu neredeyse her zaman serinin otesine
    # duser (henuz yayimlanmamis bir gundur) ama paraya dokunan sey
    # GERCEKLESME gunudur. Ikisini tek bayrakta toplamak, onemli olan tarih
    # kesinken de "supheli" uyarisi bastirirdi - surekli yanan bir uyari
    # okunmaz hale gelir.
    gerceklesme_kesin: bool
    nakit_kesin: bool
    kural: ValorKurali

    @property
    def kesin(self) -> bool:
        """Fiyatin alinacagi gun yayimlanmis takvimin icinde mi."""
        return self.gerceklesme_kesin

    @property
    def supheli(self) -> bool:
        return self.kural.supheli or not self.gerceklesme_kesin

    def anlat(self, satis: bool) -> list[str]:
        """Zinciri kullaniciya gosterilecek satirlar halinde acikla."""
        tur = "Satış" if satis else "Alış"
        v, n = self.kural.valor(satis), self.kural.nakit(satis)
        satirlar = [
            f"İşlem günü (T): {self.islem_gunu:%d.%m.%Y %a}"
            + (" — emir kesim saatinden sonra verildi, ertesi iş gününe kaydı"
               if self.kesim_sonrasi else ""),
            f"{tur} gerçekleşme (T+{v}): {self.gerceklesme:%d.%m.%Y %a} "
            f"— bu günün değerleme fiyatı kullanılır",
            f"Nakit (T+{n}): {self.nakit:%d.%m.%Y %a}",
        ]
        if self.kural.supheli:
            satirlar.append(
                f"UYARI: {self.kural.kategori or 'bu fon'} için valör kuralı "
                f"henüz doğrulanmadı ({self.kural.kaynak}). Aracı kurumun "
                f"gösterdiği nakit tarihiyle karşılaştırın."
            )
        if not self.gerceklesme_kesin:
            satirlar.append(
                "NOT: Gerçekleşme günü, yayımlanmış fiyat serisinin ötesinde; "
                "aradaki resmi tatiller bilinemedi. Fiyat yayımlandığında kayıt "
                "gerçek takvime göre hizalanacak."
            )
        elif not self.nakit_kesin:
            satirlar.append(
                "NOT: Nakit günü henüz yayımlanmamış takvime düşüyor; arada "
                "resmi tatil varsa bir gün ötelenebilir. Gerçekleşme günü kesin."
            )
        return satirlar


def cozumle(
    kural: ValorKurali,
    takvim: IslemTakvimi,
    emir_zamani: datetime | date,
    satis: bool,
    kesim: time = VARSAYILAN_KESIM,
    kesim_sonrasi: bool | None = None,
) -> Cozum:
    """Emir zamanindan gerceklesme ve nakit gununu turetir.

    `emir_zamani` saat iceriyorsa kesim karsilastirmasi ondan yapilir; yalnizca
    tarihse `kesim_sonrasi` acikca verilmelidir. Saati "herhalde erkendi" diye
    varsaymak, bu araci en pahali sekilde yaniltan senaryonun ta kendisidir.
    """
    if isinstance(emir_zamani, datetime):
        if kesim_sonrasi is not None:
            raise ValorHatasi(
                "Hem emir saati hem de kesim tarafı verildi. İkisinden birini "
                "seçin: saat verilirse kesim tarafı ondan hesaplanır."
            )
        gun = emir_zamani.date()
        sonra = emir_zamani.time() >= kesim
    else:
        gun = emir_zamani
        if kesim_sonrasi is None:
            raise ValorHatasi(
                f"Emir saati bilinmiyor. Emri {kesim:%H:%M}'dan önce mi sonra mı "
                f"verdiğinizi belirtin: kesim sonrası verilen emir bir sonraki "
                f"iş gününe kayar ve gerçekleşme fiyatı bir gün ötelenir."
            )
        sonra = kesim_sonrasi

    if takvim.ilk is None:
        raise ValorHatasi(
            "İş günü takvimi boş: fon için hiç fiyat kaydı yok. Gerçekleşme "
            "günü bu seriden türetildiği için hesaplanamıyor."
        )
    if not takvim.kapsiyor_mu(gun):
        raise ValorHatasi(
            f"Emir günü {gun:%d.%m.%Y}, çekilen fiyat penceresinin "
            f"({takvim.ilk:%d.%m.%Y} sonrası) öncesinde. İş günü takvimi bu "
            f"seriden türetildiği için gerçekleşme günü hesaplanamıyor — daha "
            f"uzun geçmiş çekin (--days) veya tarihleri elle girin."
        )

    # Kesim kaydirmasi YALNIZCA seans gununde uygulanir. Cumartesi 15:00'te
    # verilen emir de Pazartesi seansina girer ve Pazartesi'nin 13:30 kesimi
    # henuz gecmemistir; duvar saatine bakip fazladan bir gun itmek, tam da bu
    # modulun onlemek icin var oldugu bir gunluk kaymayi uretirdi.
    kesim_uygula = sonra and takvim.seans_gunu_mu(gun)
    islem_gunu, t_kesin = takvim.ekle(gun, 1 if kesim_uygula else 0)
    gerceklesme, g_kesin = takvim.ekle(islem_gunu, kural.valor(satis))
    nakit, n_kesin = takvim.ekle(islem_gunu, kural.nakit(satis))

    return Cozum(
        islem_gunu=islem_gunu,
        gerceklesme=gerceklesme,
        nakit=nakit,
        kesim_sonrasi=kesim_uygula,
        gerceklesme_kesin=t_kesin and g_kesin,
        nakit_kesin=t_kesin and n_kesin,
        kural=kural,
    )


def nakit_uyusmazligi(cozum: Cozum, beklenen_nakit: date | None) -> str | None:
    """Araci kurumun gosterdigi nakit tarihiyle mutabakat.

    Nakit tarihi kullanicinin ekraninda GORDUGU bir olgudur; turetilen zincirin
    tek dogrulanabilir ucu odur. Tutmuyorsa ya valor kurali ya da emir saati
    yanlistir - ikisi de gerceklesme gununu kaydirir, yani dogrudan paraya
    dokunur. Bu kontrol olmasaydi hata ancak aylar sonra fark edilirdi.
    """
    if beklenen_nakit is None or beklenen_nakit == cozum.nakit:
        return None
    fark = (beklenen_nakit - cozum.nakit).days
    yon = "ileri" if fark > 0 else "geri"
    return (
        f"Türetilen nakit günü {cozum.nakit:%d.%m.%Y}, sizin girdiğiniz ise "
        f"{beklenen_nakit:%d.%m.%Y} ({abs(fark)} gün {yon}). Bu, gerçekleşme "
        f"gününün de kaydığı anlamına gelir. İki olası sebep: emri kesim "
        f"saatinden sonra vermiş olabilirsiniz, ya da bu fonun valör kuralı "
        f"({cozum.kural.kaynak}) yanlış."
    )


# --------------------------------------------------------------------------
# Disk
# --------------------------------------------------------------------------
def _dosya(path: Path | None) -> Path:
    return path or config.VALOR_FILE


def yukle(path: Path | None = None) -> dict[str, ValorKurali]:
    """Kayitli valor kurallari. Dosya yoksa bos sozluk."""
    path = _dosya(path)
    if not path.exists():
        return {}
    try:
        ham = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Valor tablosu "olsa iyi olur"dur: bozuksa rapor durmasin, yalnizca
        # turetme devre disi kalsin.
        logger.warning("Valör dosyası okunamadı (%s): %s", path, exc)
        return {}

    kurallar_ham = ham.get("fonlar") if isinstance(ham, dict) else None
    if not isinstance(kurallar_ham, dict):
        logger.warning("Valör dosyası beklenen biçimde değil: %s", path)
        return {}

    kurallar: dict[str, ValorKurali] = {}
    for kod, kayit in kurallar_ham.items():
        try:
            kurallar[str(kod).upper()] = ValorKurali.from_dict(kayit, str(kod))
        except ValorHatasi as exc:
            logger.warning("%s", exc)
    return kurallar


def kaydet(kurallar: dict[str, ValorKurali], path: Path | None = None) -> Path:
    path = _dosya(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "surum": SCHEMA_VERSION,
        "kesim_saati": VARSAYILAN_KESIM.strftime("%H:%M"),
        "fonlar": {kod: kurallar[kod].to_dict() for kod in sorted(kurallar)},
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def kural_ver(
    kod: str,
    kurallar: dict[str, ValorKurali],
    kategori: str = "",
) -> ValorKurali:
    """Kayitli kural varsa onu, yoksa kategori varsayilanini dondurur."""
    mevcut = kurallar.get(kod.upper())
    if mevcut is not None:
        return mevcut
    return kategori_kurali(kategori)


def dogrula(kural: ValorKurali, gun: date | None = None) -> ValorKurali:
    """Kurali dogrulanmis olarak isaretler; artik supheli sayilmaz."""
    return replace(kural, dogrulandi=gun or date.today(), kaynak="elle-dogrulandi")
