"""Portfoyun yerel JSON dosyasinda saklanmasi.

Bir fon "islem kayitlari" (lot) listesiyle tutulur: her alis/satis icin tarih,
adet ve fiyat. Elde kalan adet bu lotlarin toplamidir. Tarih bilgisi, fonun
portfoyde bulunmadigi gunlerin getirisinin portfoye yazilmamasi icin sart
(bkz. analytics.compute_returns).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

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

    def to_dict(self) -> dict:
        payload: dict = {"adet": self.units}
        if self.date is not None:
            payload["tarih"] = self.date.isoformat()
        if self.price is not None:
            payload["fiyat"] = self.price
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

        return cls(units=units, date=lot_date, price=price)


@dataclass
class Position:
    """Bir fondaki tum islemler."""

    lots: list[Lot] = field(default_factory=list)

    @property
    def units(self) -> float:
        return sum(lot.units for lot in self.lots)

    @property
    def acquired_on(self) -> date | None:
        """En eski alis tarihi; tek bir lot bile tarihsizse None.

        Tarihsiz lot "ne zamandir elimde bilmiyorum" demektir. Bu durumda
        getiriyi kirpmak yerine tam periyodu kullaniriz: eksik veriyle fonu
        oldugundan yeni gostermek, oldugundan eski gostermekten daha yanlis.
        """
        if not self.lots or any(lot.date is None for lot in self.lots):
            return None
        return min(lot.date for lot in self.lots)

    @property
    def cost_basis(self) -> float | None:
        """Elde kalan adedin toplam maliyeti (TL), bilinmiyorsa None.

        Yalnizca butun lotlar alis ve fiyatlari biliniyorsa hesaplanir; satis
        varsa hangi lotun kapandigi (FIFO/LIFO) belirsiz oldugu icin
        hesaplamaya girismiyoruz.
        """
        if not self.lots:
            return None
        if any(lot.price is None or lot.units <= 0 for lot in self.lots):
            return None
        return sum(lot.units * lot.price for lot in self.lots)

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
        """Fon kodu -> elde kalan adet (lotlardan turetilir)."""
        return {code: position.units for code, position in self.positions.items()}

    def position(self, code: str) -> Position | None:
        return self.positions.get(normalize_code(code))

    @property
    def codes(self) -> list[str]:
        return sorted(self.positions)

    def is_empty(self) -> bool:
        return not self.positions

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
        """Fonu tek bir islem kaydina indirger; onceki lotlar silinir."""
        code = normalize_code(code)
        if units < 0:
            raise ValueError("Adet negatif olamaz.")
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
    ) -> float:
        """Mevcut fona yeni bir islem ekler. Negatif adet satistir."""
        code = normalize_code(code)
        position = self.positions.get(code) or Position()
        new_total = position.units + float(units)
        if new_total < 0:
            raise ValueError(
                f"{code} icin mevcut adet {position.units:g}; {units:g} dusulemez."
            )

        position.lots.append(Lot(float(units), on, price))
        if new_total == 0:  # tamami satildi -> fon portfoyden cikar
            self.positions.pop(code, None)
        else:
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
            "fonlar": {code: self.positions[code].to_dict() for code in self.codes},
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
            if position.units > 0:
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
