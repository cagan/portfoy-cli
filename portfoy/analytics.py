"""Agirlik, getiri ve agirlikli ortalama hesaplamalari."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import config
from .storage import Portfolio
from .tefas_client import FundHistory


@dataclass
class FundRow:
    """Tek bir fonun rapor satiri."""

    code: str
    title: str
    units: float
    price: float
    price_date: date
    value: float
    source: str
    weight: float = 0.0  # 0-1 arasi oran
    returns: dict[str, float | None] = field(default_factory=dict)  # yuzde
    contributions: dict[str, float | None] = field(default_factory=dict)  # yuzde puan
    acquired_on: date | None = None  # en eski alis tarihi; bilinmiyorsa None
    # Fonun periyodun tamaminda elde olmadigi periyotlar: getirileri alis
    # tarihinden itibaren hesaplanir, tam periyot degil.
    partial_periods: set[str] = field(default_factory=set)
    cost_basis: float | None = None  # toplam alis maliyeti (TL)

    @property
    def weight_pct(self) -> float:
        return self.weight * 100.0

    def is_partial(self, period: str) -> bool:
        return period in self.partial_periods

    @property
    def holding_days(self) -> int | None:
        """Fonun kac gundur portfoyde oldugu. Alis tarihi yoksa None."""
        if self.acquired_on is None:
            return None
        return (self.price_date - self.acquired_on).days

    @property
    def partial_note(self) -> str | None:
        """Kirpilmis periyotlar icin tek satirlik aciklama."""
        if not self.partial_periods:
            return None
        labels = ", ".join(
            config.PERIOD_LABELS[period].lower()
            for period in config.PERIODS
            if period in self.partial_periods
        )
        days = self.holding_days
        # days <= 0: bugun alindi ya da ileri tarih girilmis; ikisinde de gun
        # sayisi yazmak yaniltici olur.
        sure = f"{days} gündür portföyde" if days and days > 0 else "yeni alındı"
        return f"{sure} — {labels} getirisi alıştan itibaren"

    @property
    def profit(self) -> float | None:
        """Alistan bu yana toplam kazanc/kayip (TL). Maliyet bilinmiyorsa None."""
        if self.cost_basis is None:
            return None
        return self.value - self.cost_basis

    @property
    def profit_pct(self) -> float | None:
        if not self.cost_basis:
            return None
        return (self.value / self.cost_basis - 1.0) * 100.0

    def value_change(self, period: str) -> float | None:
        """Periyot basindan bu yana fonun TL kazanci/kaybi.

        Adedin periyot boyunca sabit kaldigi varsayilir; ara donemde alim/satim
        yaptiysaniz gunluk disindaki periyotlar yaklasik kalir. Fonun portfoye
        periyot icinde girdigi durumda taban zaten alis anina cekildigi icin
        (bkz. compute_returns) buradaki rakam gercek kazanci verir.
        """
        ret = self.returns.get(period)
        if ret is None:
            return None
        factor = 1.0 + ret / 100.0
        if factor <= 0:  # fon degerinin tamami erimis: taban fiyat hesaplanamaz
            return None
        return self.value - self.value / factor


@dataclass
class PortfolioAnalysis:
    """Portfoyun tamaminin analiz sonucu."""

    rows: list[FundRow]
    failures: dict[str, str]
    total_value: float
    weighted_returns: dict[str, float | None]  # yuzde
    coverage: dict[str, float]  # her periyot icin verisi olan agirlik orani (0-1)
    target_monthly_return: float
    as_of: date

    @property
    def monthly_return(self) -> float | None:
        return self.weighted_returns.get("aylik")

    @property
    def target_gap(self) -> float | None:
        """Aylik getirinin hedeften farki (yuzde puan). Veri yoksa None."""
        monthly = self.monthly_return
        if monthly is None:
            return None
        return monthly - self.target_monthly_return

    @property
    def target_met(self) -> bool:
        gap = self.target_gap
        return gap is not None and gap >= 0

    def value_change(self, period: str) -> float | None:
        """Periyot basindan bu yana portfoyun TL kazanci/kaybi.

        Fonlarin TL degisimlerinin toplamidir; boylece rapordaki fon bazli
        sutun bu toplami tutturur. Getirisi bilinmeyen fonlar disarida kalir,
        yani agirlikli ortalamayla ayni kapsami kullanir.
        """
        changes = [
            change for row in self.rows if (change := row.value_change(period)) is not None
        ]
        return sum(changes) if changes else None

    def partial_rows(self, period: str | None = None) -> list[FundRow]:
        """Periyodun tamaminda portfoyde olmayan fonlar.

        `period` verilmezse herhangi bir periyodu kirpilmis butun fonlar doner.
        """
        if period is None:
            return [row for row in self.rows if row.partial_periods]
        return [row for row in self.rows if row.is_partial(period)]

    @property
    def total_cost(self) -> float | None:
        """Portfoyun toplam maliyeti. Tek bir fonun maliyeti bile eksikse None.

        Kismi toplam yaniltici olur: eksik fonun maliyeti sifir sayilir ve
        kar/zarar oldugundan buyuk gorunur.
        """
        if not self.rows or any(row.cost_basis is None for row in self.rows):
            return None
        return sum(row.cost_basis for row in self.rows)

    @property
    def total_profit(self) -> float | None:
        cost = self.total_cost
        return None if cost is None else self.total_value - cost

    @property
    def total_profit_pct(self) -> float | None:
        cost = self.total_cost
        if not cost:
            return None
        return (self.total_value / cost - 1.0) * 100.0


# --------------------------------------------------------------------------
# Getiri yardimcilari
# --------------------------------------------------------------------------
def _previous_trading_price(prices: pd.Series) -> float | None:
    """Son fiyattan bir onceki islem gununun fiyati.

    TEFAS hafta sonu/tatil gunlerinde kayit uretmedigi icin takvim gunu yerine
    serideki bir onceki kaydi kullaniyoruz.
    """
    if len(prices) < 2:
        return None
    return float(prices.iloc[-2])


def _price_asof(
    prices: pd.Series, target: pd.Timestamp, allow_last: bool = False
) -> float | None:
    """`target` tarihinde veya ondan onceki en yakin islem gununun fiyati.

    `allow_last`: normalde son kaydin kendisi taban olamaz (getiri sifir cikar,
    periyot anlamsizdir). Alis tarihi taban alinirken ise bu gecerli bir sonuc:
    bugun alinan fonun getirisi gercekten %0'dir.
    """
    position = prices.index.searchsorted(target, side="right") - 1
    if position < 0:
        return None  # o tarihte veya oncesinde kayit yok
    if position >= len(prices) - 1 and not allow_last:
        return None
    return float(prices.iloc[position])


def compute_returns(
    history: FundHistory,
    acquired_on: date | None = None,
    average_cost: float | None = None,
) -> tuple[dict[str, float | None], set[str]]:
    """Fonun gunluk / haftalik / aylik yuzde getirilerini hesaplar.

    `acquired_on` verilirse fon portfoye girmeden onceki gunler getiriye
    katilmaz: periyot basi alis tarihinden eskiyse taban fiyat alis anina
    cekilir. Aksi halde 3 gun once alinan bir fon, portfoye 1 aydir varmis gibi
    ay basindaki fiyattan hesaplanir ve aylik getiriyi sisirir/dusurur.

    Taban olarak once gercek alis maliyeti (`average_cost`) kullanilir; yoksa
    TEFAS'in alis tarihindeki kapanis fiyatina duseriz.

    Doner: (getiriler, kirpilan periyotlarin kumesi).
    """
    prices = history.prices
    latest = float(prices.iloc[-1])
    latest_ts = prices.index[-1]
    acquired_ts = pd.Timestamp(acquired_on) if acquired_on is not None else None

    returns: dict[str, float | None] = {}
    partial: set[str] = set()

    for period, offset in config.PERIODS.items():
        if offset is None:
            # "gunluk": takvim yerine serideki bir onceki islem gunu
            base_ts = prices.index[-2] if len(prices) >= 2 else None
            base = _previous_trading_price(prices)
        else:
            base_ts = latest_ts - pd.DateOffset(**offset)
            base = _price_asof(prices, base_ts)

        if acquired_ts is not None and base_ts is not None and acquired_ts > base_ts:
            base = average_cost or _price_asof(prices, acquired_ts, allow_last=True)
            if base:
                partial.add(period)

        returns[period] = None if not base else (latest / base - 1.0) * 100.0

    return returns, partial


# --------------------------------------------------------------------------
# Portfoy analizi
# --------------------------------------------------------------------------
def analyze(
    portfolio: Portfolio,
    histories: dict[str, FundHistory],
    failures: dict[str, str] | None = None,
) -> PortfolioAnalysis:
    """Portfoy + fiyat verisinden tam analiz uretir."""
    failures = dict(failures or {})
    rows: list[FundRow] = []

    for code in portfolio.codes:
        history = histories.get(code)
        if history is None:
            failures.setdefault(code, "fiyat verisi yok")
            continue

        position = portfolio.positions[code]
        units = position.units
        price = history.latest_price
        returns, partial = compute_returns(
            history, position.acquired_on, position.average_cost
        )
        rows.append(
            FundRow(
                code=code,
                title=history.title,
                units=units,
                price=price,
                price_date=history.latest_date,
                value=units * price,
                source=history.source,
                returns=returns,
                acquired_on=position.acquired_on,
                partial_periods=partial,
                cost_basis=position.cost_basis,
            )
        )

    total_value = sum(row.value for row in rows)

    # Agirlik = fonun degeri / portfoyun toplam degeri
    for row in rows:
        row.weight = (row.value / total_value) if total_value > 0 else 0.0
        # Katki = agirlik x getiri (yuzde puan cinsinden)
        row.contributions = {
            period: (None if ret is None else row.weight * ret)
            for period, ret in row.returns.items()
        }

    # En degerliden en dusuge sirala - tablo ve grafikler ayni sirayi kullanir.
    rows.sort(key=lambda r: r.value, reverse=True)

    weighted_returns: dict[str, float | None] = {}
    coverage: dict[str, float] = {}
    for period in config.PERIODS:
        covered = [row for row in rows if row.returns.get(period) is not None]
        covered_weight = sum(row.weight for row in covered)
        coverage[period] = covered_weight
        if covered_weight <= 0:
            weighted_returns[period] = None
            continue
        # Agirlikli ortalama. Bazi fonlarin verisi eksikse kalan agirliga gore
        # normalize edilir; tam kapsamda sonuc duz toplamla ayni olur.
        weighted_returns[period] = (
            sum(row.weight * row.returns[period] for row in covered) / covered_weight
        )

    as_of = max((row.price_date for row in rows), default=date.today())

    return PortfolioAnalysis(
        rows=rows,
        failures=failures,
        total_value=total_value,
        weighted_returns=weighted_returns,
        coverage=coverage,
        target_monthly_return=portfolio.target_monthly_return,
        as_of=as_of,
    )


def to_dataframe(analysis: PortfolioAnalysis) -> pd.DataFrame:
    """Analizi Excel/CSV'ye uygun bir DataFrame'e cevirir."""
    records = []
    for row in analysis.rows:
        records.append(
            {
                "Tarih": row.price_date.strftime("%d.%m.%Y"),
                "Fon Kodu": row.code,
                "Fon Adı": row.title,
                "Alış Tarihi": (
                    row.acquired_on.strftime("%d.%m.%Y") if row.acquired_on else "—"
                ),
                "Adet": row.units,
                "Güncel Fiyat": row.price,
                "Toplam Değer": row.value,
                "Toplam Maliyet": row.cost_basis,
                "Kar/Zarar (₺)": row.profit,
                "Kar/Zarar (%)": row.profit_pct,
                "Portföy Ağırlığı (%)": row.weight_pct,
                "Günlük Getiri (%)": row.returns.get("gunluk"),
                "Günlük Değişim (₺)": row.value_change("gunluk"),
                "Haftalık Getiri (%)": row.returns.get("haftalik"),
                "Aylık Getiri (%)": row.returns.get("aylik"),
                "Aylık Katkı (yp)": row.contributions.get("aylik"),
                # Sayisal hucreye yildiz konamaz; kirpilmis periyot burada yazar.
                "Not": row.partial_note or "",
            }
        )
    return pd.DataFrame.from_records(records)
