"""Verilmis ama henuz gerceklesmemis emirler.

Neden ayri bir kavram: satis emri verildiginde gerceklesme GUNU bellidir ama o
gunun fiyati henuz yayimlanmamistir - TEFAS bir gunun degerlemesini ertesi gun
ilan eder. Bu araligi iki yanlis yoldan kapatabilirdik:

* Islemi bilinen son fiyatla kaydetmek: tahmin ile gerceklesme arasindaki fark
  dogrudan sahte kar/zarar olur (bkz. snapshots.sub_period_returns).
* Hic kaydetmemek: emrin varligi kullanicinin akilinda kalir; unutulur.

Ucuncu yol bu modul: emir BEKLEYEN olarak durur, adetler portfoyde kalmaya
devam eder (fon gercekten hala uzerinizdedir), fiyat yayimlandigi anda islem
kaydina donusur.

Adetlerin portfoyde kalmasi bilincli: gerceklesme gunune kadar fiyat riskini
tasiyorsuniz. Emri verir vermez pozisyonu dusmek, o gunlerin hareketini
portfoyden silmek olurdu - oysa o hareket sizindir.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import config, storage
from .formatting import fmt_money, fmt_price, fmt_units
from .storage import EPSILON, Portfolio, StorageError, normalize_code, parse_date
from .valor import Cozum, IslemTakvimi

logger = logging.getLogger(__name__)

# Surum 2: `gerceklesme` artik ISLEM GUNUDUR. Surum 1'de o alan
# `islem_gunu + valor` ile yazilmisti ve `coz` onu oldugu gibi fiyat gunu diye
# okurdu - yani eski bir emir valor gununun fiyatindan islenirdi (bkz. valor.py
# docstring'i: bu kayma 69.991 adetlik bir satista 28.820,61 TL'ye mal oldu).
# Dosya icerigine bakarak ayirt edilemez, o yuzden surumle isaretleniyor ve
# `_surum2ye_tasi` ile YUKLENIRKEN cevriliyor; yalnizca uyarmak yetmezdi.
SCHEMA_VERSION = 2


class BekleyenHatasi(RuntimeError):
    """Kullaniciya oldugu gibi gosterilebilecek, beklenen hata."""


@dataclass
class BekleyenEmir:
    """Gerceklesmeyi bekleyen tek bir emir."""

    id: str
    kod: str
    # Emir ya ADETLE ya TUTARLA verilir; ikisi de isaretlidir (satis negatif).
    # Alista aracı kurum TL ister ve adedi ancak islem gunu kapanis fiyati
    # yayimlaninca hesaplar - biz de oyle yapiyoruz: `adet = tutar / fiyat`,
    # ayni fiyattan, ek varsayim girmeden. Tam da bu yuzden `adet` zorunlu
    # degil: TL ile verilmis bir emirde adet HENUZ YOKTUR ve uydurmak, tahmin
    # ile gerceklesme arasindaki farki sahte kar/zarara cevirirdi.
    adet: float | None           # isaretli: satis negatif
    emir_zamani: str             # ISO; saat varsa iceriyor
    islem_gunu: date             # T
    gerceklesme: date            # fiyatin alinacagi degerleme gunu (= islem gunu)
    nakit: date                  # nakit gunu (bilgi + mutabakat icin)
    tarih_kaynagi: str           # "turetildi" | "elle"
    # Paylarin emanete girdigi/ciktigi gun (T+valor). Fiyata DOKUNMAZ; burada
    # tutuluyor cunku emir kaydedildikten sonra kullanicinin gorebilecegi tek
    # yer burasi ve "paylar neden hala hesabimda" sorusunun cevabi bu tarih.
    # Surum 1 dosyalarinda yok: None gelir.
    valor_gunu: date | None = None
    valor_supheli: bool = False
    # Araci kurumun gosterdigi gerceklesme fiyati. Verilirse TEFAS fiyati
    # yerine BU kullanilir: komisyon/yuvarlama farkinda dogru olan odur.
    fiyat: float | None = None
    # TL cinsinden emir tutari (isaretli). SATISTA KABUL EDILMEZ: adet
    # bilinmeden `bekleyen_satis_adedi` rezerveyi sayamaz ve ayni paylar iki
    # kez satilabilirdi. Alista boyle bir risk yok - eldeki adede dokunmuyor.
    tutar: float | None = None

    def __post_init__(self) -> None:
        if (self.adet is None) == (self.tutar is None):
            raise BekleyenHatasi(
                "Emir ya adetle ya TL tutarıyla verilir; ikisinden tam olarak "
                "birini girin."
            )
        if self.tutar is not None and self.tutar < 0:
            raise BekleyenHatasi(
                "TL tutarıyla satış emri kabul edilmiyor: adet bilinmeden "
                "beklemedeki satış rezervesi sayılamaz ve aynı paylar iki kez "
                "satılabilir. Satış emrini adetle girin."
            )

    @property
    def satis_mi(self) -> bool:
        return self.adet is not None and self.adet < 0

    @property
    def adet_bilinmiyor(self) -> bool:
        """TL ile verilmis emir: adet, fiyat yayimlaninca hesaplanacak."""
        return self.adet is None

    def to_dict(self) -> dict:
        payload = {
            "id": self.id,
            "kod": self.kod,
            "adet": self.adet,
            "tutar": self.tutar,
            "emir_zamani": self.emir_zamani,
            "islem_gunu": self.islem_gunu.isoformat(),
            "gerceklesme": self.gerceklesme.isoformat(),
            "nakit": self.nakit.isoformat(),
            "tarih_kaynagi": self.tarih_kaynagi,
            "valor_supheli": self.valor_supheli,
        }
        if self.valor_gunu is not None:
            payload["valor_gunu"] = self.valor_gunu.isoformat()
        if self.fiyat is not None:
            payload["fiyat"] = self.fiyat
        return payload

    @classmethod
    def from_dict(cls, raw: dict) -> "BekleyenEmir":
        try:
            return cls(
                id=str(raw["id"]),
                kod=normalize_code(raw["kod"]),
                adet=(float(raw["adet"]) if raw.get("adet") is not None
                      else None),
                tutar=(float(raw["tutar"]) if raw.get("tutar") is not None
                       else None),
                emir_zamani=str(raw["emir_zamani"]),
                islem_gunu=parse_date(raw["islem_gunu"]),
                gerceklesme=parse_date(raw["gerceklesme"]),
                nakit=parse_date(raw["nakit"]),
                tarih_kaynagi=str(raw.get("tarih_kaynagi", "elle")),
                valor_gunu=(parse_date(raw["valor_gunu"])
                            if raw.get("valor_gunu") else None),
                valor_supheli=bool(raw.get("valor_supheli", False)),
                fiyat=float(raw["fiyat"]) if raw.get("fiyat") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BekleyenHatasi(f"Bekleyen emir kaydı okunamadı: {raw!r} ({exc})")

    def ozet(self) -> str:
        tur = "Satış" if self.satis_mi else "Alış"
        if self.adet is None:
            miktar = f"{fmt_money(self.tutar)} (adet fiyat gelince)"
        else:
            miktar = f"{fmt_units(abs(self.adet))} adet"
        valor = ""
        if self.valor_gunu is not None and self.valor_gunu != self.gerceklesme:
            valor = f" · valör {self.valor_gunu:%d.%m.%Y}"
        return (
            f"{self.kod}: {tur} {miktar} · "
            f"gerçekleşme (fiyat günü) {self.gerceklesme:%d.%m.%Y}{valor} · "
            f"nakit {self.nakit:%d.%m.%Y}"
        )


def emir_olustur(
    kod: str, adet: float | None, cozum: Cozum, emir_zamani: datetime | date,
    fiyat: float | None = None, tarih_kaynagi: str = "turetildi",
    tutar: float | None = None,
) -> BekleyenEmir:
    """Cozumlenmis tarih zincirinden bekleyen emir uretir.

    `adet` ya da `tutar`dan tam olarak biri verilir; dogrulamayi
    `BekleyenEmir.__post_init__` yapar.
    """
    return BekleyenEmir(
        id=secrets.token_hex(6),
        kod=normalize_code(kod),
        adet=None if adet is None else float(adet),
        tutar=None if tutar is None else float(tutar),
        emir_zamani=emir_zamani.isoformat(),
        islem_gunu=cozum.islem_gunu,
        gerceklesme=cozum.gerceklesme,
        valor_gunu=cozum.valor_gunu,
        nakit=cozum.nakit,
        tarih_kaynagi=tarih_kaynagi,
        valor_supheli=cozum.supheli,
        fiyat=fiyat,
    )


# --------------------------------------------------------------------------
# Dogrulama
# --------------------------------------------------------------------------
def bekleyen_satis_adedi(emirler: list[BekleyenEmir], kod: str) -> float:
    """Bir fon icin beklemedeki toplam satis adedi (pozitif)."""
    kod = normalize_code(kod)
    # `satis_mi` adedi bilinmeyen emri zaten satis saymaz; TL ile satis
    # kabul edilmedigi icin (bkz. __post_init__) burada bir bosluk kalmiyor.
    return sum(-e.adet for e in emirler if e.kod == kod and e.satis_mi)


def satis_dogrula(
    portfolio: Portfolio, emirler: list[BekleyenEmir], kod: str, adet: float
) -> None:
    """Elde olandan fazlasinin satilmasini engeller.

    Beklemedeki satislar da DUSULUR: emir verilmis ama henuz gerceklesmemis
    adetler hala portfoyde gorunur, onlari saymadan bakmak ayni paylari iki kez
    satmaya izin verirdi.
    """
    kod = normalize_code(kod)
    position = portfolio.positions.get(kod)
    if position is None or position.is_closed:
        raise BekleyenHatasi(f"{kod} portföyde açık bir pozisyon değil, satılamaz.")

    rezerve = bekleyen_satis_adedi(emirler, kod)
    musait = position.units - rezerve
    if adet > musait + EPSILON:
        detay = f"{fmt_units(position.units)} adet"
        if rezerve > EPSILON:
            detay += f" ({fmt_units(rezerve)} adedi bekleyen satış emrinde)"
        raise BekleyenHatasi(
            f"{kod} için satılabilir {fmt_units(musait)} adet var — {detay}. "
            f"{fmt_units(adet)} adet satılamaz."
        )


# --------------------------------------------------------------------------
# Cozulme: fiyat yayimlaninca islem kaydina donus
# --------------------------------------------------------------------------
@dataclass
class Cozulen:
    """Gerceklesmis bir emir ve nasil gerceklestigi."""

    emir: BekleyenEmir
    adet: float            # gerceklesen adet; TL'li emirde tutar/fiyat ile dogar
    tarih: date            # kullanilan degerleme gunu (hizalanmis olabilir)
    fiyat: float
    fiyat_kaynagi: str     # "tefas" | "elle"
    hizalandi: bool        # tahmin edilen gun tatile denk gelip kaydi mi

    def ozet(self) -> str:
        tur = "Satış" if self.emir.satis_mi else "Alış"
        satir = (
            f"{self.emir.kod}: {tur} {fmt_units(abs(self.adet))} adet gerçekleşti "
            f"— {self.tarih:%d.%m.%Y} · {fmt_price(self.fiyat)} ₺"
        )
        if self.emir.adet_bilinmiyor:
            # Adedin nereden geldigini yazmak sart: kullanici ekranda TL girdi,
            # portfoyde adet goruyor; ikisi arasindaki koprunun gorunmez olmasi
            # "bu sayi nereden cikti" sorusunu doguruyordu.
            satir += f" [{fmt_money(self.emir.tutar)} tutardan hesaplandı]"
        if self.fiyat_kaynagi == "tefas":
            satir += " (TEFAS değerleme fiyatı)"
        if self.hizalandi:
            satir += (
                f" [tahmin edilen gün {self.emir.gerceklesme:%d.%m.%Y} işlem günü "
                f"değildi, ilk işlem gününe hizalandı]"
            )
        return satir


def _fiyat_bul(prices, hedef: date) -> tuple[date, float, bool] | None:
    """Hedef gun veya sonrasindaki ilk yayimlanmis fiyat.

    Hizalama gerekiyorsa (tahmin edilen gun resmi tatile denk geldiyse) ilk
    islem gunune kayilir; bu, `IslemTakvimi`nin seri disinda hafta sonu
    kuralina dusmesinin duzeltmesidir.
    """
    import pandas as pd

    if len(prices) == 0:
        return None
    hedef_ts = pd.Timestamp(hedef)
    if hedef_ts < prices.index[0]:
        # Hedef, elimizdeki pencerenin ONUNDE. `searchsorted` burada 0 doner ve
        # serinin ILK fiyatini verirdi - aylarca sonrasinin fiyatiyla islem
        # yapmak demek. "Ileri hizalama" diye sunmak sapmayi kozmetik gosterir;
        # beklemede birakip pencereyi genisletmeyi beklemek dogrusu.
        logger.warning(
            "Gerçekleşme günü %s, çekilen fiyat penceresinin (%s) öncesinde; "
            "emir bekletiliyor. Daha uzun geçmiş için --days kullanın.",
            hedef, prices.index[0].date(),
        )
        return None
    konum = prices.index.searchsorted(hedef_ts, side="left")
    if konum >= len(prices):
        return None  # o gunun fiyati henuz yayimlanmadi
    gercek = prices.index[konum].date()
    return gercek, float(prices.iloc[konum]), gercek != hedef


def coz(
    emirler: list[BekleyenEmir],
    portfolio: Portfolio,
    histories: dict,
) -> tuple[list[Cozulen], list[BekleyenEmir]]:
    """Fiyati yayimlanmis emirleri portfoye isler.

    Doner: (cozulenler, hala bekleyenler). Portfoy YERINDE degistirilir;
    cagiran taraf kaydetmekle yukumludur.
    """
    cozulenler: list[Cozulen] = []
    kalan: list[BekleyenEmir] = []

    # Ayni fon ve ayni gun icin ALISLAR once islenir: satis once gelseydi ve
    # adet yetmeseydi `add_lot` reddeder, emir sonsuza dek beklemede kalirdi -
    # oysa alis once islense ikisi de gecerdi.
    for emir in sorted(emirler, key=lambda e: (e.gerceklesme, e.satis_mi, e.kod)):
        # Portfoyde bu emirden uretilmis lot zaten varsa emir islenmis
        # demektir; portfoy yazildiktan SONRA bekleyen dosyasinin yazimi
        # basarisiz olmus olabilir. Ikinci kez uygulamak adedi yok ederdi.
        if portfolio.emir_islendi_mi(emir.id):
            logger.info("%s emri zaten işlenmiş, bekleyenden düşülüyor", emir.kod)
            continue

        history = histories.get(emir.kod)
        if history is None:
            kalan.append(emir)
            continue

        if emir.fiyat is not None:
            # Araci kurumun gerceklesen fiyati verilmisse TEFAS'i beklemeye
            # gerek yok; dogru olan odur.
            tarih, fiyat, kaynak, hizalandi = (
                emir.gerceklesme, emir.fiyat, "elle", False
            )
            if emir.gerceklesme > history.latest_date:
                # Ileri tarihli: gun henuz gelmedi, beklemeye devam.
                kalan.append(emir)
                continue
        else:
            bulunan = _fiyat_bul(history.prices, emir.gerceklesme)
            if bulunan is None:
                kalan.append(emir)
                continue
            tarih, fiyat, hizalandi = bulunan
            kaynak = "tefas"

        # TL ile verilmis emirde adet BURADA dogar: bolen, lotun kaydedilecegi
        # fiyatin ta kendisi. Emir verilirken bilinen son fiyatla bolmek, o
        # gunler arasindaki hareketi sahte kar/zarara cevirirdi.
        adet = emir.adet if emir.adet is not None else emir.tutar / fiyat
        try:
            portfolio.add_lot(emir.kod, adet, tarih, fiyat, emir_id=emir.id)
        except ValueError as exc:
            # Portfoy emirden sonra elle degistirilmis olabilir: emri
            # dusurmuyoruz, kullaniciya gorunur kalsin diye bekletmeye devam.
            logger.warning("%s bekleyen emri işlenemedi: %s", emir.kod, exc)
            kalan.append(emir)
            continue

        cozulenler.append(
            Cozulen(emir=emir, adet=adet, tarih=tarih, fiyat=fiyat,
                    fiyat_kaynagi=kaynak, hizalandi=hizalandi)
        )

    return cozulenler, kalan


def coz_ve_kaydet(
    portfolio: Portfolio,
    histories: dict,
    bekleyen_yolu: Path | None = None,
    portfoy_yolu: Path | None = None,
) -> list[Cozulen]:
    """Bekleyenleri cozup portfoyu ve emir dosyasini gunceller.

    CLI ve web AYNI yoldan gecsin diye burada: her fiyat cekiminden sonra
    cagrilir, gerceklesmis emir kendiliginden islem kaydina doner. Iki ayri
    kopya yazilsaydi biri digerinden ayrisirdi.
    """
    try:
        emirler = yukle(bekleyen_yolu)
    except BekleyenHatasi as exc:
        logger.warning("%s", exc)
        return []
    if not emirler:
        return []

    cozulenler, kalan = coz(emirler, portfolio, histories)
    if len(kalan) == len(emirler) and not cozulenler:
        return []

    # Sira onemli: once PORTFOY. Tersi olsaydi bekleyen dosyasi temizlenip
    # portfoy yazimi patladiginda emir izsiz kaybolurdu. Bu sirada ise en kotu
    # ihtimalle emir hem islenmis hem bekliyor kalir ve bir sonraki calisma
    # `emir_islendi_mi` sayesinde onu sessizce duser (bkz. coz).
    storage.save(portfolio, portfoy_yolu)
    try:
        kaydet(kalan, bekleyen_yolu)
    except StorageError as exc:
        logger.error(
            "Portföy yazıldı ama bekleyen emir dosyası güncellenemedi (%s). "
            "Kayıt tutarlı: emirler lot kimliğiyle işaretlendiği için bir "
            "sonraki çalıştırmada tekrar işlenmeyecek.", exc,
        )
    return cozulenler


# --------------------------------------------------------------------------
# Disk
# --------------------------------------------------------------------------
def yukle(path: Path | None = None) -> list[BekleyenEmir]:
    path = path or config.PENDING_FILE
    if not path.exists():
        return []
    try:
        ham = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BekleyenHatasi(f"Bekleyen emir dosyası okunamadı: {path} ({exc})")

    kayitlar = ham.get("emirler") if isinstance(ham, dict) else None
    if not isinstance(kayitlar, list):
        raise BekleyenHatasi(f"Bekleyen emir dosyası beklenen biçimde değil: {path}")

    surum = ham.get("surum")
    if not isinstance(surum, int):
        surum = 1                      # damgasiz dosya = surum 1 (elle duzenlenmis)
    if surum > SCHEMA_VERSION:
        logger.warning(
            "Bekleyen emir dosyası daha yeni bir sürümden (%s > %s); "
            "tanınmayan alanlar yok sayılacak.", surum, SCHEMA_VERSION,
        )
    elif surum < SCHEMA_VERSION:
        kayitlar = [_surum2ye_tasi(item) for item in kayitlar]
        if kayitlar:
            logger.info(
                "Bekleyen emir dosyası sürüm %s'den %s'e taşındı: fiyat günü "
                "işlem gününe alındı, eski gerçekleşme günü valör günü olarak "
                "kaydedildi.", surum, SCHEMA_VERSION,
            )
    return [BekleyenEmir.from_dict(item) for item in kayitlar]


def _surum2ye_tasi(raw: dict) -> dict:
    """Surum 1 kaydini surum 2 anlamina cevirir. KAYIPSIZ.

    Surum 1'de `gerceklesme` = `islem_gunu + valor` yazilirdi ve fiyat oradan
    alinirdi; surum 2'de fiyat gunu islem gunudur (bkz. valor.py docstring'i).
    Iki alan da diskte durdugu icin donusum tam: eski `gerceklesme` aslinda
    valor gunuydu, dogru fiyat gunu ise zaten yazili olan `islem_gunu`.

    Bunu SADECE uyarip birakmak yetmezdi: uyari dosyadaki surum damgasina
    bakiyordu, ilk `kaydet` cagrisi damgayi 2 yapip kaydin kendisini eski
    birakiyordu ve emir bir daha uyarmadan yanlis gunun fiyatindan islenirdi.
    """
    if not isinstance(raw, dict) or "islem_gunu" not in raw:
        return raw                     # bozuk kayit: from_dict anlamli hata versin
    tasinmis = dict(raw)
    tasinmis["valor_gunu"] = raw.get("gerceklesme")
    tasinmis["gerceklesme"] = raw["islem_gunu"]
    return tasinmis


def kaydet(emirler: list[BekleyenEmir], path: Path | None = None) -> Path:
    """Atomik yazim; yarim yazilmis emir dosyasi portfoyu kilitlerdi."""
    path = path or config.PENDING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "surum": SCHEMA_VERSION,
        "emirler": [item.to_dict() for item in
                    sorted(emirler, key=lambda e: (e.gerceklesme, e.kod))],
    }
    metin = json.dumps(payload, ensure_ascii=False, indent=2)

    gecici_ad: str | None = None
    try:
        # mkstemp de OSError atabilir (dizin yazilamiyor, disk dolu); disarida
        # birakmak ham PermissionError'in cagiran katmana sizmasi demekti.
        gecici_fd, gecici_ad = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(gecici_fd, "w", encoding="utf-8") as dosya:
            dosya.write(metin + "\n")
            dosya.flush()
            os.fsync(dosya.fileno())
        os.replace(gecici_ad, path)
    except OSError as exc:
        if gecici_ad is not None:
            Path(gecici_ad).unlink(missing_ok=True)
        raise StorageError(f"Bekleyen emirler kaydedilemedi: {path} ({exc})")
    return path
