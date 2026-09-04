"""Portfoyun yerel JSON dosyasinda saklanmasi.

Bir fon "islem kayitlari" (lot) listesiyle tutulur: her alis/satis icin tarih,
adet ve fiyat. Elde kalan adet bu lotlarin toplamidir. Tarih bilgisi, fonun
portfoyde bulunmadigi gunlerin getirisinin portfoye yazilmamasi icin sart
(bkz. analytics.compute_returns).

Tamami satilan fon KAYITTAN SILINMEZ, "kapanmis pozisyon" olarak durur (adet 0,
lotlar yerinde). Silinseydi satisin tarihi ve fiyati da giderdi; gerceklesmis
kar/zarar bir daha hesaplanamaz, gecmis anlik goruntulerdeki cikis akisi
fiyatsiz kalirdi. Rapor satirlari `codes`/`holdings` uzerinden yalnizca ACIK
pozisyonlari gorur; kapanmislara `all_codes` ile ulasilir.

TEFAS cekimi ise ucuncu bir listeyi, `codes_for_pricing`i kullanir: acik
pozisyonlar + YAKIN ZAMANDA kapanmislar. Getiri penceresi icinde satilan fonun
satis gunune kadarki fiyat hareketi portfoy getirisine giriyor; fiyati
cekilmezse o hareket sessizce kaybolur (bkz. analytics._flow_adjusted_change).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# Adetler float tutuldugu icin FIFO'da "tam kapandi" karsilastirmalari kucuk bir
# tolerans ister; yoksa 1e-10'luk artik bir lot elde kalmis gibi gorunur.
# Ayni toleransi disaridan kullananlar (web katmanindaki "bu kadar adet var mi"
# kontrolu gibi) EPSILON adiyla erisir; iki ayri esik tutmak, sinirda birbirini
# tutmayan iki cevap uretirdi.
EPSILON = 1e-9
_EPSILON = EPSILON

# ISO once denenir; kalanlar aracı kurum ekranlarindaki Turkce bicimler.
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y")


class StorageError(RuntimeError):
    """Portfoy dosyasi okunamadiginda / yazilamadiginda firlatilir."""


def parse_date(value: str | date) -> date:
    """'2026-08-03', '03-08-2026', '03.08.2026' -> date."""
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"Tarih anlaşılamadı: {value!r}. Örnek: 2026-08-03 veya 03.08.2026"
    )


@dataclass
class Lot:
    """Tek bir islem. Adet pozitifse alis, negatifse satis."""

    units: float
    date: date | None = None  # None: eski kayit, ne zaman alindigi bilinmiyor
    price: float | None = None
    # Bu lotu ureten bekleyen emrin kimligi (bkz. portfoy/bekleyen.py).
    # CIFT ISLEMEYI ONLEYEN SEY BUDUR: emir cozuldugunde portfoy ve bekleyen
    # dosyasi ayri ayri yazilir; ikisi arasinda bir hata olursa emir hem
    # islenmis hem bekliyor kalirdi ve sonraki calisma ayni satisi bir kez daha
    # uygulayip adedi sessizce yok ederdi. Kimlik lotta durdugu icin cozulme
    # yeniden calistirilabilir (idempotent) hale geliyor.
    emir_id: str | None = None

    def to_dict(self) -> dict:
        payload: dict = {"adet": self.units}
        if self.date is not None:
            payload["tarih"] = self.date.isoformat()
        if self.price is not None:
            payload["fiyat"] = self.price
        if self.emir_id is not None:
            payload["emir_id"] = self.emir_id
        return payload

    @classmethod
    def from_dict(cls, raw: dict, code: str) -> "Lot":
        try:
            units = float(raw["adet"])
        except (KeyError, TypeError, ValueError):
            raise StorageError(
                f"'{code}' işlem kaydında geçersiz adet: {raw!r}"
            ) from None

        raw_date = raw.get("tarih")
        try:
            lot_date = parse_date(raw_date) if raw_date else None
        except ValueError as exc:
            raise StorageError(f"'{code}' işlem kaydı: {exc}") from None

        raw_price = raw.get("fiyat")
        try:
            price = float(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            raise StorageError(
                f"'{code}' işlem kaydında geçersiz fiyat: {raw_price!r}"
            ) from None

        emir_id = raw.get("emir_id")
        return cls(
            units=units, date=lot_date, price=price,
            emir_id=str(emir_id) if emir_id else None,
        )


@dataclass
class Position:
    """Bir fondaki tum islemler."""

    lots: list[Lot] = field(default_factory=list)

    @property
    def units(self) -> float:
        return sum(lot.units for lot in self.lots)

    def _match_fifo(self) -> tuple[list[tuple[Lot, float]], list[tuple[Lot, Lot, float]]]:
        """Satislari alislarla FIFO esler. Doner: (elde kalan, kapanmis).

        Elde kalan: (alis lotu, o lottan kalan adet), en eskiden yeniye.
        Kapanmis  : (alis lotu, satis lotu, eslesen adet) - gerceklesmis
                    kar/zarar yalnizca bu ucluden cikarilabilir, cunku hangi
                    alisin hangi satisla kapandigini bilmek gerekir.

        Satista once en eski alis kapanir - Turkiye'de fon satislarinda uygulanan
        ve aracı kurum ekranlarinin da kullandigi sira budur.

        Tarihsiz lotlar en eski sayilir; bunlar tarih alani eklenmeden once
        girilmis eski kayitlardir ve pratikte ilk alislardir.
        """
        buys = sorted(
            (lot for lot in self.lots if lot.units > 0),
            key=lambda lot: lot.date or date.min,
        )
        sells = sorted(
            (lot for lot in self.lots if lot.units < 0),
            key=lambda lot: lot.date or date.min,
        )

        open_lots = [[lot, lot.units] for lot in buys]
        closed: list[tuple[Lot, Lot, float]] = []
        index = 0
        for sell in sells:
            to_close = -sell.units
            while to_close > _EPSILON and index < len(open_lots):
                entry = open_lots[index]
                taken = min(entry[1], to_close)
                if taken > _EPSILON:
                    closed.append((entry[0], sell, taken))
                    entry[1] -= taken
                    to_close -= taken
                if entry[1] <= _EPSILON:
                    index += 1

        remaining = [(lot, left) for lot, left in open_lots if left > _EPSILON]
        return remaining, closed

    def remaining_lots(self) -> list[tuple[Lot, float]]:
        """Satislar FIFO ile dusuldukten sonra elde kalan alis lotlari.

        Doner: (lot, o lottan elde kalan adet) ciftleri, en eskiden yeniye.
        Satis lotlarinin kendi fiyatlari burada kullanilmaz: gerceklesmis
        kar/zarari ilgilendirir, elde kalanin maliyetini degil.
        """
        return self._match_fifo()[0]

    def closed_lots(self) -> list[tuple[Lot, Lot, float]]:
        """FIFO ile kapanmis (alis, satis, adet) ucluleri."""
        return self._match_fifo()[1]

    @property
    def is_closed(self) -> bool:
        """Alis yapilmis ama elde adet kalmamis pozisyon."""
        return bool(self.lots) and self.units <= _EPSILON

    @property
    def closed_on(self) -> date | None:
        """Pozisyonu kapatan son satisin tarihi; acik pozisyonda None."""
        if not self.is_closed:
            return None
        dates = [lot.date for lot in self.lots if lot.units < 0 and lot.date]
        return max(dates) if dates else None

    @property
    def realized_proceeds(self) -> float | None:
        """Kapanan lotlarin toplam satis hasilati (TL).

        Kar/zarar ile AYNI kumeden hesaplanir (FIFO'da eslesen satislar), yoksa
        "hasilat" ve "kar" satirlari yan yana basildiginda farkli seyleri
        toplayip birbirini tutmazlardi.
        """
        closed = self.closed_lots()
        if not closed or any(sell.price is None for _, sell, _ in closed):
            return None
        return sum(units * sell.price for _, sell, units in closed)

    @property
    def realized_profit(self) -> float | None:
        """Gerceklesmis kar/zarar (TL).

        Eslesen alis veya satis lotlarindan birinin fiyati bilinmiyorsa None -
        eksik veriyle 0 dondurmek "kar da zarar da etmedim" diye okunurdu.
        """
        closed = self.closed_lots()
        if not closed:
            return None
        if any(buy.price is None or sell.price is None for buy, sell, _ in closed):
            return None
        return sum(units * (sell.price - buy.price) for buy, sell, units in closed)

    @property
    def realized_cost(self) -> float | None:
        """Kapanmis lotlarin toplam maliyeti (TL)."""
        closed = self.closed_lots()
        if not closed or any(buy.price is None for buy, _, _ in closed):
            return None
        return sum(units * buy.price for buy, _, units in closed)

    @property
    def acquired_on(self) -> date | None:
        """Elde kalan en eski alisin tarihi; biri bile tarihsizse None.

        Tarihsiz lot "ne zamandir elimde bilmiyorum" demektir. Bu durumda
        getiriyi kirpmak yerine tam periyodu kullaniriz: eksik veriyle fonu
        oldugundan yeni gostermek, oldugundan eski gostermekten daha yanlis.

        FIFO ile kapanmis lotlar hesaba katilmaz: tamami satilmis bir alis
        artik portfoyde degildir, getiriyi onun tarihinden baslatmak fonu
        oldugundan uzun suredir elde tutuluyor gosterir.
        """
        remaining = self.remaining_lots()
        if not remaining or any(lot.date is None for lot, _ in remaining):
            return None
        return min(lot.date for lot, _ in remaining)

    @property
    def cost_basis(self) -> float | None:
        """Elde kalan adedin toplam maliyeti (TL), bilinmiyorsa None.

        Satislar FIFO ile dusulur; yalnizca elde kalan lotlarin fiyati gerekir.
        Kapanmis bir lotun fiyati bilinmese de kalanin maliyeti hesaplanabilir.
        """
        remaining = self.remaining_lots()
        if not remaining or any(lot.price is None for lot, _ in remaining):
            return None
        return sum(units * lot.price for lot, units in remaining)

    @property
    def average_cost(self) -> float | None:
        """Birim basina ortalama maliyet."""
        cost = self.cost_basis
        units = self.units
        if cost is None or units <= 0:
            return None
        return cost / units

    @property
    def has_dates(self) -> bool:
        return self.acquired_on is not None

    def to_dict(self) -> dict:
        return {"islemler": [lot.to_dict() for lot in self.lots]}


@dataclass
class Portfolio:
    """Elde tutulan fonlar ve hedef getiri."""

    positions: dict[str, Position] = field(default_factory=dict)
    target_monthly_return: float = config.DEFAULT_TARGET_MONTHLY_RETURN

    # --- Okuma -------------------------------------------------------------
    @property
    def holdings(self) -> dict[str, float]:
        """Fon kodu -> elde kalan adet. Kapanmis pozisyonlar yer almaz."""
        return {code: self.positions[code].units for code in self.codes}

    def position(self, code: str) -> Position | None:
        """Kapanmis pozisyonlar da doner - gecmis islemleri sormak icin."""
        return self.positions.get(normalize_code(code))

    @property
    def codes(self) -> list[str]:
        """ACIK pozisyonlarin kodlari. TEFAS cekimi ve rapor satirlari bunu kullanir.

        Kapanmis fonu buraya koymak, artik elde olmayan bir fon icin fiyat cekip
        raporda 0 adetlik satir gostermek olurdu.
        """
        return sorted(
            code for code, position in self.positions.items() if not position.is_closed
        )

    @property
    def all_codes(self) -> list[str]:
        """Kapanmislar dahil butun kodlar - islem gecmisi ve diske yazim icin."""
        return sorted(self.positions)

    @property
    def closed_codes(self) -> list[str]:
        """Tamami satilmis fonlar."""
        return sorted(
            code for code, position in self.positions.items() if position.is_closed
        )

    def codes_for_pricing(
        self, lookback_days: int = config.LOOKBACK_DAYS, today: date | None = None
    ) -> list[str]:
        """Fiyati CEKILMESI gereken kodlar: acik + yakin zamanda kapanmis.

        `codes` ve `all_codes` semantigi degismedi (bkz. modul docstring):
        `codes` hala "elde ne var", `all_codes` hala "kayitta ne var". Bu ucuncu
        liste ayri bir soruyu cevapliyor: HANGI fonlarin fiyat serisi gerekli.

        Kapanmis fon da gerekli, cunku pencere icinde satildiysa satis gunune
        kadarki fiyat hareketi portfoy getirisine giriyor (bkz.
        analytics._flow_adjusted_change). Fiyati cekilmezse o fon analizde hic
        gorunmez ve gunluk getiri, yalnizca hayatta kalan fonlarin ortalamasina
        doner - 03.09'da PHE tamamen satildiginda ekranda +%0,77 yazarken
        portfoyun gercekte -%0,04 kaybettigi durum budur.

        Lookback penceresinden ESKI kapanislar disarida kalir: getiri
        pencerelerinin hicbirine dokunmadiklari icin fiyatlarini cekmek bosuna
        ag trafigi olurdu. Kapanis tarihi bilinmeyen (satis lotu tarihsiz)
        pozisyon da disarida kalir - ne zaman kapandigini bilmeden "yakin"
        saymak, yillar once satilmis her fonu her calistirmada cekmek olurdu.
        """
        today = today or date.today()
        esik = today - timedelta(days=lookback_days)
        yakin_kapanmis = [
            code
            for code in self.closed_codes
            if (kapanis := self.positions[code].closed_on) is not None
            and kapanis >= esik
        ]
        return sorted(set(self.codes) | set(yakin_kapanmis))

    def is_empty(self) -> bool:
        """Acik pozisyon yoksa bos sayilir; kapanmis kayitlar rapor uretmez."""
        return not self.codes

    def emir_islendi_mi(self, emir_id: str) -> bool:
        """Bu emirden uretilmis bir lot zaten var mi.

        Cozulme yeniden calistirilabilir olsun diye: portfoy yazildiktan sonra
        bekleyen dosyasinin yazimi basarisiz olursa emir yeniden islenmeye
        calisilir; burasi onu yakalar.
        """
        return any(
            lot.emir_id == emir_id
            for position in self.positions.values()
            for lot in position.lots
        )

    def undated_codes(self) -> list[str]:
        """Alis tarihi bilinmeyen fonlar - getiri kirpmasi bunlarda calismaz."""
        return [code for code in self.codes if not self.positions[code].has_dates]

    # --- Mutasyonlar -------------------------------------------------------
    def set_units(
        self,
        code: str,
        units: float,
        on: date | None = None,
        price: float | None = None,
    ) -> None:
        """Fonu tek bir islem kaydina indirger; onceki lotlar silinir.

        Kapanmis pozisyonda REDDEDER. Satilmis bir fona yeniden girerken en
        dogal hamle `add KOD <adet>` oluyor; bu, gerceklesmis kar/zarari ve
        satis kaydini geri donulmez sekilde silerdi - modulun "kapanmis pozisyon
        silinmez" sozunun tam tersi. Dogru hamle `--accumulate` ile yeni bir
        alis lotu eklemek; hata mesaji bunu soyluyor.
        """
        code = normalize_code(code)
        if units < 0:
            raise ValueError("Adet negatif olamaz.")

        existing = self.positions.get(code)
        if existing is not None and existing.is_closed:
            raise ValueError(
                f"{code} kapanmis bir pozisyon: uzerine yazmak satis kaydini ve "
                f"gerceklesmis kar/zarari siler. Yeni alis icin: "
                f"add {code} {units:g} --accumulate --date GG.AA.YYYY --price <fiyat>. "
                f"Gecmisi gercekten silmek icin once: remove {code}"
            )

        if units == 0:
            self.positions.pop(code, None)
        else:
            self.positions[code] = Position([Lot(float(units), on, price)])

    def add_lot(
        self,
        code: str,
        units: float,
        on: date | None = None,
        price: float | None = None,
        emir_id: str | None = None,
    ) -> float:
        """Mevcut fona yeni bir islem ekler. Negatif adet satistir."""
        code = normalize_code(code)
        position = self.positions.get(code) or Position()
        new_total = position.units + float(units)
        if new_total < 0:
            raise ValueError(
                f"{code} icin mevcut adet {position.units:g}; {units:g} dusulemez."
            )

        position.lots.append(Lot(float(units), on, price, emir_id))
        # Tamami satilsa bile kayit durur: satisin tarihi/fiyati gerceklesmis
        # kar/zarar ve gecmis anlik goruntulerdeki cikis akisi icin gerekli.
        # Pozisyon `is_closed` olur, `codes` onu artik dondurmez.
        self.positions[code] = position
        return new_total

    # Eski isim: cagri yerleri tek tek guncellenmesin diye korunuyor.
    add_units = add_lot

    def remove(self, code: str) -> bool:
        return self.positions.pop(normalize_code(code), None) is not None

    # --- Serilestirme ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "hedef_aylik_getiri": self.target_monthly_return,
            "fonlar": {code: self.positions[code].to_dict() for code in self.all_codes},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Portfolio":
        funds_raw = raw.get("fonlar") or {}
        if not isinstance(funds_raw, dict):
            raise StorageError("'fonlar' alani bir sozluk olmali.")

        positions: dict[str, Position] = {}
        for code, entry in funds_raw.items():
            key = normalize_code(code)
            position = _position_from_entry(entry, key)
            if not position.lots:
                continue
            if position.units < -_EPSILON:
                # Elde olmayani satmak: elle duzenlenmis dosyada olur. Kaydi
                # almiyoruz ama SESSIZCE degil - sonraki save bunu kalicilastirir.
                logger.warning(
                    "%s: net adet negatif (%g); kayit yok sayildi, "
                    "sonraki kaydetmede dosyadan silinecek.",
                    key, position.units,
                )
                continue
            # Adet 0 ise ancak GERCEK bir satisla kapanmissa kapanmis pozisyondur.
            # Satissiz sifir adet, v1 formatindaki `"KOD": 0` gibi bos bir
            # kayittir; eskiden dusuruluyordu, hayalet pozisyona donusmesin.
            if position.units <= _EPSILON and not any(
                lot.units < 0 for lot in position.lots
            ):
                continue
            positions[key] = position

        try:
            target = float(raw.get("hedef_aylik_getiri", config.DEFAULT_TARGET_MONTHLY_RETURN))
        except (TypeError, ValueError):
            target = config.DEFAULT_TARGET_MONTHLY_RETURN

        return cls(positions=positions, target_monthly_return=target)


def _position_from_entry(entry, code: str) -> Position:
    """Tek bir fon kaydini Position'a cevirir.

    Uc bicim de kabul edilir:
      v1  "TMV": 40631                                  -> tarihsiz tek lot
          "TMV": {"adet": 40631}                        -> tarihsiz tek lot
      v2  "TMV": {"islemler": [{"tarih": ..., ...}]}    -> lot listesi
    """
    if isinstance(entry, dict):
        raw_lots = entry.get("islemler")
        if raw_lots:
            if not isinstance(raw_lots, list):
                raise StorageError(f"'{code}' için 'islemler' bir liste olmalı.")
            return Position([Lot.from_dict(item, code) for item in raw_lots])
        entry = entry.get("adet")

    try:
        units = float(entry)
    except (TypeError, ValueError):
        raise StorageError(f"'{code}' icin gecersiz adet degeri: {entry!r}") from None

    return Position([Lot(units)])


def normalize_code(code: str) -> str:
    """Fon kodunu TEFAS'in bekledigi bicime getirir (buyuk harf, bosluksuz)."""
    cleaned = str(code).strip().upper()
    if not cleaned:
        raise ValueError("Fon kodu bos olamaz.")
    if not cleaned.isalnum():
        raise ValueError(f"Gecersiz fon kodu: {code!r}")
    return cleaned


def load(path: Path | None = None) -> Portfolio:
    """Portfoyu diskten okur. Dosya yoksa bos portfoy doner."""
    path = path or config.PORTFOLIO_FILE
    if not path.exists():
        return Portfolio()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageError(
            f"Portfoy dosyasi bozuk: {path} ({exc}). "
            "Dosyayi duzeltin veya silip yeniden olusturun."
        ) from exc
    except OSError as exc:
        raise StorageError(f"Portfoy dosyasi okunamadi: {path} ({exc})") from exc

    if not isinstance(raw, dict):
        raise StorageError(f"Portfoy dosyasi beklenen bicimde degil: {path}")

    return Portfolio.from_dict(raw)


def backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def save(portfolio: Portfolio, path: Path | None = None) -> Path:
    """Portfoyu atomik olarak diske yazar (yarim yazma riskini onler).

    Yazmadan once mevcut dosyanin bir kopyasi `.bak` olarak saklanir; yanlis
    girilen bir adet portfoyu geri donulmez sekilde kaybettirmesin.
    """
    path = path or config.PORTFOLIO_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            backup_path(path).write_bytes(path.read_bytes())
        except OSError as exc:
            logger.warning("Yedek alinamadi (%s): %s", backup_path(path), exc)

    payload = json.dumps(portfolio.to_dict(), ensure_ascii=False, indent=2)

    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise StorageError(f"Portfoy kaydedilemedi: {path} ({exc})") from exc

    return path
