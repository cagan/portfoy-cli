"""Gunluk portfoy anlik goruntuleri ve bunlardan zaman agirlikli getiri.

Neden gerekli: fiyat gecmisi TEFAS'ta duruyor ama PORTFOYUN gecmisi hicbir
yerde durmuyor. Elimizde yalnizca bugunku pozisyon var. Gecmis bir takvim ayinin
gercek portfoy getirisini hesaplamak icin o ay hangi fonun ne kadar tutuldugunu
bilmek sart; bugunku agirliklari geriye yansitmak baska bir sey olur (bkz.
analytics.monthly_track_record -> "temsili" kol).

Her `report` / `status` calistirmasinda o gunun pozisyonu buraya yazilir. Birikti
kce karne "temsili"den "gerceklesen"e doner.

Getiri neden dogrudan deger farkindan hesaplanamaz: yeni alim yaptiginizda
portfoy degeri artar ama bu getiri degildir. Bu yuzden ardisik iki goruntu
arasindaki ADET degisimi "dis para akisi" sayilip getiriden dusulur - zaman
agirlikli getirinin (TWR) tanimi budur.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import config
from .storage import StorageError, parse_date

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Adet karsilastirmalarinda float artigi "alim yapilmis" gibi gorunmesin.
_EPSILON = 1e-9


@dataclass
class Holding:
    units: float
    price: float

    @property
    def value(self) -> float:
        return self.units * self.price


@dataclass
class Snapshot:
    """Bir gunun portfoy fotografi."""

    on: date
    holdings: dict[str, Holding] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        return sum(holding.value for holding in self.holdings.values())

    def units(self, code: str) -> float:
        holding = self.holdings.get(code)
        return holding.units if holding else 0.0

    def price(self, code: str) -> float | None:
        holding = self.holdings.get(code)
        return holding.price if holding else None

    def to_dict(self) -> dict:
        return {
            "fonlar": {
                code: {"adet": holding.units, "fiyat": holding.price}
                for code, holding in sorted(self.holdings.items())
            }
        }

    @classmethod
    def from_dict(cls, on: date, raw: dict) -> "Snapshot":
        holdings: dict[str, Holding] = {}
        for code, item in (raw.get("fonlar") or {}).items():
            try:
                holdings[str(code).upper()] = Holding(
                    units=float(item["adet"]), price=float(item["fiyat"])
                )
            except (KeyError, TypeError, ValueError):
                logger.warning("%s: bozuk anlik goruntu kaydi atlandi (%s)", on, code)
        return cls(on=on, holdings=holdings)


# --------------------------------------------------------------------------
# Disk
# --------------------------------------------------------------------------
def load(path: Path | None = None) -> list[Snapshot]:
    """Kayitli goruntuleri tarihe gore sirali dondurur. Dosya yoksa bos liste."""
    path = path or config.SNAPSHOT_FILE
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Karne kaybolsun ama rapor durmasin: gecmis kayit "olsa iyi olur"dur.
        logger.warning("Gecmis dosyasi okunamadi (%s): %s", path, exc)
        return []

    records = payload.get("kayitlar") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        logger.warning("Gecmis dosyasi beklenen bicimde degil: %s", path)
        return []

    snapshots: list[Snapshot] = []
    for raw_date, raw in records.items():
        try:
            snapshots.append(Snapshot.from_dict(parse_date(raw_date), raw))
        except ValueError:
            logger.warning("Gecmis dosyasinda anlasilmayan tarih atlandi: %s", raw_date)
    snapshots.sort(key=lambda snapshot: snapshot.on)
    return snapshots


def save(snapshots: list[Snapshot], path: Path | None = None) -> Path:
    """Goruntuleri atomik olarak yazar (yarim yazma riskini onler)."""
    path = path or config.SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "surum": SCHEMA_VERSION,
        "kayitlar": {
            snapshot.on.isoformat(): snapshot.to_dict()
            for snapshot in sorted(snapshots, key=lambda item: item.on)
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise StorageError(f"Geçmiş dosyası yazılamadı ({path}): {exc}") from exc
    return path


def record(analysis, path: Path | None = None) -> Snapshot | None:
    """Analizden o gunun goruntusunu uretip diske ekler.

    Ayni tarih icin ikinci kez calistirilirsa kayit GUNCELLENIR, cogaltilmaz;
    gun icinde birkac kez rapor almak gecmisi bozmaz.

    Tarih olarak bugun degil `analysis.as_of` (fiyatin ait oldugu gun) kullanilir.
    TEFAS fiyati gecikmeli yayimlayabildigi icin bugunun tarihiyle yazmak, ayni
    fiyati iki farkli gune yazip getiriyi sifirlardi.

    Kapanmis pozisyonlar da (adet 0, o gunku fiyatiyla) yazilir. Yalnizca
    `analysis.rows` yazilsaydi satilan fon goruntuye HIC girmez, karne de fon
    tablosundaki sapmanin aynisini tasirdi. Adet 0 oldugu icin `total_value`
    degismez; satis, bir onceki goruntudeki adetle arasindaki fark uzerinden
    dogal olarak dis para akisina donusur.

    CIFT SAYMA YOK: `sub_period_returns` fonun kodunu zaten
    `previous.holdings | current.holdings | (penceredeki lotlar)` birlesiminden
    aliyor ve eksik holding'i `units()` ile 0 sayiyordu. Yani akis, kod
    goruntude 0 adetle YER ALSA da ALMASA da ayni delta'dan hesaplanir;
    `_recorded_flow` da ayni lotu bir kez gorur. Degisen tek sey satis gunundeki
    FIYATIN kayda gecmesi - tahmin yoluna dusuldugunde isabeti artirir.
    """
    # `all_rows`, `rows` DEGIL: her seyin satildigi tasfiye gununde acik
    # pozisyon kalmaz ve `rows` bos olur. `rows` kontrol edilseydi o gunun
    # goruntusu hic yazilmaz, tasfiye dilimi karneden KALICI olarak duserdi -
    # ustelik tam da en buyuk hareketin yasandigi gun.
    if not analysis.all_rows:
        return None

    snapshot = Snapshot(
        on=analysis.as_of,
        holdings={
            row.code: Holding(units=row.units, price=row.price)
            for row in analysis.all_rows
        },
    )

    existing = [item for item in load(path) if item.on != snapshot.on]
    existing.append(snapshot)
    try:
        save(existing, path)
    except StorageError as exc:
        logger.warning("Anlik goruntu kaydedilemedi: %s", exc)
        return None
    return snapshot


# --------------------------------------------------------------------------
# Zaman agirlikli getiri
# --------------------------------------------------------------------------
@dataclass
class SubPeriod:
    """Ardisik iki goruntu arasindaki getiri dilimi."""

    start: date
    end: date
    ret: float  # oran (0.012 = %1,2), yuzde degil
    flow: float  # donem icindeki net dis para akisi (TL)


def _dated_lots(portfolio) -> dict[str, list[tuple[date, float, float]]]:
    """Fon kodu -> (tarih, adet, fiyat) uclusu; tarihi ve fiyati olan lotlar.

    Kapanmis pozisyonlar da taranir: cikis akisini dogru fiyatlayan tek kayit
    onlarin satis lotudur (bkz. storage: kapanmis pozisyon silinmez).
    """
    if portfolio is None:
        return {}
    table: dict[str, list[tuple[date, float, float]]] = {}
    for code, position in portfolio.positions.items():
        priced = [
            (lot.date, lot.units, lot.price)
            for lot in position.lots
            if lot.date is not None and lot.price is not None
        ]
        if priced:
            table[code] = sorted(priced)
    return table


def _recorded_flow(
    lots: list[tuple[date, float, float]] | None,
    start: date,
    end: date,
    delta: float,
) -> float | None:
    """Penceredeki kayitli lotlardan akis; adetler tutmuyorsa None.

    Iki pencere sirayla denenir:

    1. Yari-acik `(start, end]` - normal durum. Dilim baslangicindaki lot bir
       onceki dilime aittir; oraya da saymak `covered != delta` yapip kayitli
       fiyat yolunu bosuna terk ettirirdi.
    2. Kapali `[start, end]` - satis, onceki goruntunun tarihinde gerceklesmis
       ama o goruntu alindiktan SONRA kaydedilmis olabilir; PHE'nin 02.09
       satisi boyleydi. Bu lot hicbir yari-acik pencereye dusmez.

    Cift saymayi onleyen sey pencerenin kendisi degil, `covered == delta`
    kapisidir: tutarli veride ayni lot iki ardisik dilimde birden bu kapiyi
    gecemez. Kapiyi gevsetecek olan cift sayma acar.

    Adetler `delta`yi tam aciklamiyorsa (eksik kayit, elle duzeltilmis adet)
    None doner ve cagiran taraf fiyat tahminine geri duser: yarim kayitla
    hesaplanan akis, hic kayit olmamasindan daha yaniltici olurdu.
    """
    if not lots:
        return None
    for include_start in (False, True):
        window = [
            (units, price)
            for on, units, price in lots
            if (start <= on if include_start else start < on) and on <= end
        ]
        if not window:
            continue
        covered = sum(units for units, _ in window)
        if abs(covered - delta) <= max(_EPSILON, abs(delta) * 1e-9):
            return sum(units * price for units, price in window)
    return None


def sub_period_returns(snapshots: list[Snapshot], portfolio=None) -> list[SubPeriod]:
    """Ardisik goruntulerden dis para akisindan arindirilmis getiri dilimleri.

    Dilim getirisi = (bitis degeri - net akis) / baslangic degeri - 1.

    `portfolio` verilirse akis, islem kaydindaki GERCEK fiyattan hesaplanir.
    Bu, tamami satilan fonlarda onemlidir: fon bitis goruntusunde bulunmadigi
    icin fiyati tahmin etmek gerekir ve tahminle gerceklesme fiyati arasindaki
    fark, dogrudan sahte kar/zarar olarak getiriye yazilir.

    Kayit yoksa eskisi gibi adet degisimi donem sonu (yoksa donem basi) fiyatiyla
    carpilir. Goruntuler seyrekse ve arada alim/satim yapildiysa dilim getirisi
    yaklasik kalir - `flow` alani bunu gorunur kilar.
    """
    lot_table = _dated_lots(portfolio)

    periods: list[SubPeriod] = []
    for previous, current in zip(snapshots, snapshots[1:]):
        start_value = previous.total_value
        if start_value <= 0:
            continue  # taban yok: oran tanimsiz

        # Kod kumesi anlik goruntulerle SINIRLI DEGIL: dilim icinde satilip geri
        # alinan fonun net adet farki sifirdir ve yalnizca goruntulere bakan bir
        # tarama onu hic gormez - oysa aradaki fiyat farki gercek bir akistir.
        codes = set(previous.holdings) | set(current.holdings) | {
            code
            for code, lots in lot_table.items()
            if any(previous.on <= on <= current.on for on, _, _ in lots)
        }

        flow = 0.0
        for code in codes:
            delta = current.units(code) - previous.units(code)

            recorded = _recorded_flow(
                lot_table.get(code), previous.on, current.on, delta
            )
            if recorded is not None:
                flow += recorded
                continue

            if abs(delta) <= _EPSILON:
                continue
            price = current.price(code)
            if price is None:
                # Fon bitis goruntusunde hic yok: donem basi fiyatina duseriz.
                # `record` artik kapanmis fonu 0 adetle YAZDIGI icin yeni
                # kayitlarda buraya dusulmez - ama gecmis dosyasindaki ESKI
                # kayitlar (o degisiklikten once yazilanlar) fonu tamamen
                # dusuruyordu ve onlar geriye donuk duzeltilmiyor. Yedek o
                # kayitlar icin duruyor; kaldirmak eski dilimlerin akisini
                # sifirlayip sahte getiri uretirdi.
                price = previous.price(code) or 0.0
            flow += delta * price

        periods.append(
            SubPeriod(
                start=previous.on,
                end=current.on,
                ret=(current.total_value - flow) / start_value - 1.0,
                flow=flow,
            )
        )
    return periods


def covered_months(snapshots: list[Snapshot]) -> set[tuple[int, int]]:
    """Goruntulerin TAM kapsadigi (yil, ay) ciftleri.

    Bir ayin gercek getirisi icin ayin basindan onceki bir acilis degeri ve ay
    bittikten sonraki bir kapanis degeri gerekir. Yalnizca bu kosulu saglayan
    aylar "gerceklesen" sayilir; yarim kapsanan ay temsili kola birakilir.
    """
    if len(snapshots) < 2:
        return set()

    first, last = snapshots[0].on, snapshots[-1].on
    months: set[tuple[int, int]] = set()
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        month_start = date(year, month, 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        next_start = date(next_year, next_month, 1)
        # Acilis ayin basindan onceye dusmeli, kapanis ay bittikten sonraya.
        if first < month_start and last >= next_start:
            months.add((year, month))
        year, month = next_year, next_month
    return months
