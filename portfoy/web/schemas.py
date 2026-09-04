"""Web formlarinin dogrulama modelleri.

Buradaki kurallar PORTFOYDEN BAGIMSIZ olanlardir: bicim, isaret, aralik, tarih
makulluğu. "Elimde bu kadar yok" gibi portfoyun o anki durumunu gerektiren
kurallar `services.py`'de, veriyi kilit altinda okurken uygulanir - Pydantic'e
tasinsalardi dogrulama ile yazma arasinda yaris olusurdu.

Hata mesajlari Turkce ve DUZELTICI yazilir: neyin yanlis oldugunu degil, ne
yapilmasi gerektigini soyler.
"""

from __future__ import annotations

import math
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from .. import config
from ..storage import normalize_code, parse_date

# Portfoy dosyasindaki en eski makul islem. TEFAS verisi bu kadar geriye
# gitmez; daha eskisi neredeyse her zaman yil yanlis yazilmis demektir.
_EN_ESKI_ISLEM = date(2000, 1, 1)

# Adet ve fiyat icin ust sinir: float sonsuza gitmesin, bir sifir fazla
# yazildiginda portfoy sessizce anlamsizlasmasin.
_MAKS_ADET = 1e12
_MAKS_FIYAT = 1e9


class IslemTuru(str, Enum):
    ALIS = "alis"
    SATIS = "satis"


def _kod_dogrula(value: str) -> str:
    """TEFAS fon kodu; buyuk harfe cevirir."""
    try:
        return normalize_code(value)
    except ValueError:
        raise ValueError(
            "Fon kodu yalnızca harf ve rakamdan oluşur, örn: TMV, DFI, PHE."
        ) from None


def _tarih_dogrula(value) -> date | None:
    """Bos birakilabilir; verilirse gelecekte veya cok eskide olamaz."""
    if value in (None, ""):
        return None
    try:
        parsed = parse_date(value)
    except ValueError as exc:
        raise ValueError(str(exc)) from None

    if parsed > date.today():
        raise ValueError(
            f"{parsed:%d.%m.%Y} gelecekte. Gün ve ayı karıştırmış olabilirsiniz "
            "(GG.AA.YYYY)."
        )
    if parsed < _EN_ESKI_ISLEM:
        raise ValueError(f"{parsed:%d.%m.%Y} fazla eski; yılı kontrol edin.")
    return parsed


def _sayiya_cevir(value, ad: str) -> float:
    """Form metnini float'a cevirir; Pydantic'e birakilsa mesaj Ingilizce olurdu.

    Turkce ondalik virgulu de kabul edilir: sayfada rakamlar '8,6140' diye
    gosterildigi icin kullanicinin ayni bicimde yazmasi beklenen bir hatadir,
    reddedilecek bir sey degil.
    """
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{ad} bir sayı olmalı ({value!r} girildi).") from None


def _sayi_dogrula(value, ad: str, ust: float) -> float:
    """Sonlu ve pozitif olmali. NaN/inf form uzerinden gercekten gelebiliyor."""
    sayi = _sayiya_cevir(value, ad)
    if not math.isfinite(sayi):
        raise ValueError(f"{ad} geçerli bir sayı olmalı.")
    if sayi <= 0:
        raise ValueError(f"{ad} sıfırdan büyük olmalı.")
    if sayi > ust:
        raise ValueError(f"{ad} fazla büyük görünüyor ({sayi:g}); bir hane fazla olabilir.")
    return sayi


class IslemForm(BaseModel):
    """Alis veya satis kaydi.

    Adet HER ZAMAN pozitif girilir; isareti `tur` belirler. Formda eksi isaret
    istemek, "-100 satış" ile "100 satış"in ayni sey mi zit sey mi oldugunu
    kullaniciya sordurur; tek dogru okuma birakiyoruz.
    """

    code: str
    tur: IslemTuru
    units: float | str
    date: str | None = None
    price: float | str | None = None
    # TEFAS'ta bulunmayan kodu kaydetmek icin acik onay. Varsayilan False:
    # yazim hatasi olan kod sessizce portfoye girmesin.
    force: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _kod_dogrula(value)

    @field_validator("units", mode="before")
    @classmethod
    def _units(cls, value) -> float:
        return _sayi_dogrula(value, "Adet", _MAKS_ADET)

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value):
        if value in (None, ""):
            return None
        return _sayi_dogrula(value, "Fiyat", _MAKS_FIYAT)

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value):
        parsed = _tarih_dogrula(value)
        return parsed.isoformat() if parsed else None

    @model_validator(mode="after")
    def _satis_fiyat_ister(self) -> "IslemForm":
        # Satista fiyat kaydin ASIL degeridir: gerceklesmis kar/zarar ve zaman
        # agirlikli getirideki cikis akisi yalnizca ondan hesaplanabiliyor
        # (bkz. snapshots.sub_period_returns). Alista eksik fiyat maliyeti
        # bilinmez birakir; satista getiriyi bozar, o yuzden zorunlu.
        if self.tur is IslemTuru.SATIS and self.price is None:
            raise ValueError(
                "Satışta birim fiyat zorunlu: gerçekleşmiş kâr/zarar ve aylık "
                "karne bu fiyattan hesaplanıyor. Aracı kurumun 'gerçekleşen "
                "emirler' ekranındaki fiyatı girin."
            )
        if self.tur is IslemTuru.SATIS and self.date is None:
            raise ValueError(
                "Satışta tarih zorunlu: emrin GERÇEKLEŞTİĞİ günü girin "
                "(emri verdiğiniz günü veya paranın hesaba geçtiği günü değil)."
            )
        return self

    @property
    def signed_units(self) -> float:
        """Depoya yazilacak isaretli adet: satis negatiftir."""
        return -self.units if self.tur is IslemTuru.SATIS else self.units

    @property
    def parsed_date(self) -> date | None:
        return parse_date(self.date) if self.date else None


class AdetDuzeltForm(BaseModel):
    """Islem gecmisini tek bir kayda indirger (CLI'daki `add` --accumulate'siz).

    Yikici oldugu icin ayri bir formda: kapanmis pozisyonda depo katmani zaten
    reddediyor, acik pozisyonda ise onceki lotlarin tarihi ve fiyati gider.
    """

    code: str
    units: float | str
    date: str | None = None
    price: float | str | None = None
    onay: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _kod_dogrula(value)

    @field_validator("units", mode="before")
    @classmethod
    def _units(cls, value) -> float:
        return _sayi_dogrula(value, "Adet", _MAKS_ADET)

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value):
        if value in (None, ""):
            return None
        return _sayi_dogrula(value, "Fiyat", _MAKS_FIYAT)

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value):
        parsed = _tarih_dogrula(value)
        return parsed.isoformat() if parsed else None

    @model_validator(mode="after")
    def _onay_gerekli(self) -> "AdetDuzeltForm":
        if not self.onay:
            raise ValueError(
                "Bu işlem fonun önceki alış/satış kayıtlarını siler. "
                "Devam etmek için onay kutusunu işaretleyin."
            )
        return self

    @property
    def parsed_date(self) -> date | None:
        return parse_date(self.date) if self.date else None


class FonSilForm(BaseModel):
    """Fonu kayittan tamamen siler - kapanmis pozisyonun gecmisi dahil."""

    code: str
    onay: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _kod_dogrula(value)

    @model_validator(mode="after")
    def _onay_gerekli(self) -> "FonSilForm":
        if not self.onay:
            raise ValueError(
                "Silme geri alınamaz ve işlem geçmişini de götürür. "
                "Devam etmek için onay kutusunu işaretleyin."
            )
        return self


class HedefForm(BaseModel):
    """Aylik getiri hedefi (yuzde).

    Negatif hedef anlamli: "en fazla %2 kaybedeyim" gecerli bir hedeftir.
    Ust sinir enflasyon ortaminda bile fazlasiyla genis tutuldu.
    """

    percent: float | str

    @field_validator("percent", mode="before")
    @classmethod
    def _aralik(cls, value) -> float:
        value = _sayiya_cevir(value, "Hedef")
        if not math.isfinite(value):
            raise ValueError("Hedef geçerli bir sayı olmalı.")
        if not -100.0 <= value <= 1000.0:
            raise ValueError(
                f"Aylık hedef %-100 ile %1000 arasında olmalı ({value:g} girildi)."
            )
        return value


class RaporForm(BaseModel):
    """Grafik/Excel uretimi secenekleri."""

    theme: str = "light"
    periods: list[str] = Field(default_factory=list)
    excel: bool = True
    charts: bool = True

    @field_validator("theme")
    @classmethod
    def _theme(cls, value: str) -> str:
        if value not in config.PALETTES:
            raise ValueError(f"Tema 'light' veya 'dark' olmalı, '{value}' değil.")
        return value

    @field_validator("periods")
    @classmethod
    def _periods(cls, value: list[str]) -> list[str]:
        gecersiz = [item for item in value if item not in config.PERIODS]
        if gecersiz:
            gecerli = ", ".join(config.PERIODS)
            raise ValueError(f"Bilinmeyen periyot: {', '.join(gecersiz)}. Geçerli: {gecerli}")
        return value

    @model_validator(mode="after")
    def _en_az_bir_cikti(self) -> "RaporForm":
        # Sessiz varsayilan yok: kullanici butun periyotlari kaldirdiysa niyeti
        # "hepsini uret" degildir; bir varsayilan koymak secimi gecersiz kilardi.
        if self.charts and not self.periods:
            gecerli = ", ".join(config.PERIOD_LABELS[p] for p in config.PERIODS)
            raise ValueError(f"Grafikler için en az bir periyot seçin ({gecerli}).")
        if not self.excel and not self.charts:
            raise ValueError("En az bir çıktı türü seçin (Excel veya grafikler).")
        return self


class EpostaForm(BaseModel):
    """SMTP ayarlari. Sifre bos birakilirsa mevcut sifre korunur."""

    user: str
    recipients: str = ""
    # repr=False: bugun hicbir yere sizmiyor, ama ileride eklenecek tek bir
    # `logger.debug("%s", form)` satiri sifreyi gunluge dusururdu.
    password: str = Field(default="", repr=False)
    host: str = config.DEFAULT_SMTP_HOST
    port: int | str = config.DEFAULT_SMTP_PORT

    @field_validator("user")
    @classmethod
    def _user(cls, value: str) -> str:
        temiz = value.strip()
        if "@" not in temiz or temiz.startswith("@") or temiz.endswith("@"):
            raise ValueError("Gönderen adresi geçerli bir e-posta olmalı.")
        return temiz

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, value) -> int:
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"Port bir sayı olmalı ({value!r} girildi).") from None
        if not 1 <= value <= 65535:
            raise ValueError(f"Port 1-65535 arasında olmalı ({value} girildi).")
        return value

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        temiz = value.strip()
        if not temiz:
            raise ValueError("SMTP sunucusu boş olamaz.")
        return temiz

    @property
    def recipient_list(self) -> list[str]:
        """Virgul/noktali virgul/bosluk ile ayrilmis alicilar.

        Bos birakilirsa gonderen hesabin kendisine gonderilir (mailer'in
        varsayilani); bu yuzden burada 'en az bir alici' sarti yok.
        """
        ayrilmis = self.recipients.replace(";", ",").replace(" ", ",").split(",")
        return [item.strip() for item in ayrilmis if item.strip()]

    @model_validator(mode="after")
    def _alicilar_gecerli(self) -> "EpostaForm":
        hatali = [item for item in self.recipient_list if "@" not in item]
        if hatali:
            raise ValueError(f"Geçersiz alıcı adresi: {', '.join(hatali)}")
        return self


class EmirForm(BaseModel):
    """Emir zamanindan gerceklesme gununu turetecek form.

    `IslemForm`dan farki: orada GERCEKLESME tarihi girilir, burada EMIR tarihi.
    Ikisi de kalir - biri "gerceklesmis islemi kaydet", digeri "emir verdim,
    gerceklesmesini bekle" icindir.
    """

    code: str
    tur: IslemTuru
    units: float | str
    emir_tarihi: str
    # Saat ZORUNLU bilgi ama iki bicimde alinabilir: acik saat, ya da kesimin
    # hangi tarafinda oldugu. "Herhalde erkendi" varsayimi yok - kesim sonrasi
    # verilen emir bir sonraki is gunune kayar ve gerceklesme fiyati bir gun
    # otelenir; bu varsayimi sessizce yapmak dogrudan paraya dokunur.
    saat: str | None = None
    kesim_taraf: str | None = None       # "once" | "sonra"
    # Araci kurumun gosterdigi nakit tarihi. Turetilen zincirin dogrulanabilir
    # tek ucu budur; verilirse mutabakat yapilir.
    nakit_tarihi: str | None = None
    price: float | str | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _kod_dogrula(value)

    @field_validator("units", mode="before")
    @classmethod
    def _units(cls, value) -> float:
        return _sayi_dogrula(value, "Adet", _MAKS_ADET)

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value):
        if value in (None, ""):
            return None
        return _sayi_dogrula(value, "Fiyat", _MAKS_FIYAT)

    @field_validator("emir_tarihi", mode="before")
    @classmethod
    def _emir_tarihi(cls, value):
        parsed = _tarih_dogrula(value)
        if parsed is None:
            raise ValueError("Emri verdiğiniz günü girin.")
        return parsed.isoformat()

    @field_validator("nakit_tarihi", mode="before")
    @classmethod
    def _nakit_tarihi(cls, value):
        if value in (None, ""):
            return None
        # Nakit tarihi GELECEKTE olabilir - normal durum zaten budur, bu yuzden
        # `_tarih_dogrula`nin gelecek yasagi burada uygulanmaz.
        try:
            return parse_date(value).isoformat()
        except ValueError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("saat", mode="before")
    @classmethod
    def _saat(cls, value):
        if value in (None, ""):
            return None
        metin = str(value).strip()
        try:
            saat, dakika = metin.split(":")
            if not (0 <= int(saat) <= 23 and 0 <= int(dakika) <= 59):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError(f"Saat 'SS:DD' biçiminde olmalı ({metin!r} girildi).") from None
        return metin

    @field_validator("kesim_taraf", mode="before")
    @classmethod
    def _kesim(cls, value):
        if value in (None, ""):
            return None
        if value not in {"once", "sonra"}:
            raise ValueError("Kesim tarafı 'once' veya 'sonra' olmalı.")
        return value

    @model_validator(mode="after")
    def _zaman_gerekli(self) -> "EmirForm":
        if self.saat is None and self.kesim_taraf is None:
            raise ValueError(
                "Emri saat kaçta verdiğinizi belirtin. TEFAS'ta emir kesimi "
                "13:30; sonrasında verilen emir bir sonraki iş gününe kayar ve "
                "gerçekleşme fiyatı bir gün ötelenir."
            )
        return self

    @property
    def signed_units(self) -> float:
        return -self.units if self.tur is IslemTuru.SATIS else self.units

    @property
    def parsed_emir_tarihi(self) -> date:
        return parse_date(self.emir_tarihi)

    @property
    def parsed_nakit(self) -> date | None:
        return parse_date(self.nakit_tarihi) if self.nakit_tarihi else None

    @property
    def kesim_sonrasi(self) -> bool | None:
        return None if self.kesim_taraf is None else self.kesim_taraf == "sonra"


class ValorForm(BaseModel):
    """Bir fonun valor kurali (is gunu cinsinden)."""

    code: str
    alis_valor: int | str = 1
    alis_nakit: int | str = 1
    satis_valor: int | str = 1
    satis_nakit: int | str = 2
    dogrula: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _kod_dogrula(value)

    @field_validator("alis_valor", "alis_nakit", "satis_valor", "satis_nakit",
                     mode="before")
    @classmethod
    def _gun(cls, value) -> int:
        try:
            sayi = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"Valör gün sayısı tam sayı olmalı ({value!r} girildi).") from None
        if not 0 <= sayi <= 10:
            raise ValueError(f"Valör 0-10 iş günü arasında olmalı ({sayi} girildi).")
        return sayi


class EmirSilForm(BaseModel):
    """Bekleyen emri iptal eder."""

    emir_id: str

    @field_validator("emir_id")
    @classmethod
    def _id(cls, value: str) -> str:
        temiz = str(value).strip()
        if not temiz or not temiz.isalnum():
            raise ValueError("Geçersiz emir kimliği.")
        return temiz
