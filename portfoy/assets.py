"""Varlik turu cozumleme ve fiyat kaynagi yonlendirmesi.

TEFAS fonlarinin yanina BIST hisseleri, gram altin/gumus ve doviz eklenir.
Her kaynak `tefas_client.FundHistory` uretir; boylece analytics, console,
charts, excel_report ve mailer katmanlarinda hicbir degisiklik gerekmez.

Sembol kurallari (hepsi alfanumerik — storage.normalize_code korunur):

    ALTIN, GUMUS   gram altin / gram gumus, TL
    USD, EUR       doviz kuru, TL
    3 harf         TEFAS fon kodu          (PHE, TLY, DFI)
    4-6 harf       BIST hisse kodu         (TUPRS -> TUPRS.IS)

Rezerve semboller once kontrol edilir; ALTIN 5 harfli oldugu icin hisse
kuralindan once eslesmelidir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .tefas_client import FundHistory, TefasError

logger = logging.getLogger(__name__)

# Bir ons = 31,1034768 gram (troy ons)
TROY_OUNCE_G = 31.1034768

FUND = "fon"
EQUITY = "hisse"
METAL = "maden"
CURRENCY = "doviz"

# Rezerve semboller: (tur, baslik, yfinance sembolu)
_RESERVED = {
    "ALTIN": (METAL, "Gram Altın", "GC=F"),
    "GUMUS": (METAL, "Gram Gümüş", "SI=F"),
    "USD": (CURRENCY, "ABD Doları", "USDTRY=X"),
    "EUR": (CURRENCY, "Euro", "EURTRY=X"),
}

# Kullanici kolayligi icin kabul edilen diger yazimlar
_ALIASES = {
    "GA": "ALTIN", "GRAMALTIN": "ALTIN", "XAU": "ALTIN", "GOLD": "ALTIN",
    "GG": "GUMUS", "GRAMGUMUS": "GUMUS", "XAG": "GUMUS", "SILVER": "GUMUS",
    "DOLAR": "USD", "USDTRY": "USD",
    "EURO": "EUR", "EURTRY": "EUR",
}

USDTRY = "USDTRY=X"


@dataclass(frozen=True)
class AssetInfo:
    """Bir sembolun cozumlenmis hali."""

    code: str          # portfoyde saklanan kanonik kod
    kind: str          # FUND | EQUITY | METAL | CURRENCY
    title: str         # ekranda gosterilecek ad
    vendor: str | None # dis kaynak sembolu (yfinance), fonlarda None

    @property
    def is_fund(self) -> bool:
        return self.kind == FUND


def canonical(code: str) -> str:
    """Takma adlari kanonik sembole cevirir."""
    key = str(code).strip().upper().replace(" ", "")
    return _ALIASES.get(key, key)


def resolve(code: str) -> AssetInfo:
    """Sembolu varlik turune cozer."""
    key = canonical(code)

    if key in _RESERVED:
        kind, title, vendor = _RESERVED[key]
        return AssetInfo(key, kind, title, vendor)

    if key.isalpha():
        if len(key) == 3:
            return AssetInfo(key, FUND, key, None)
        if 4 <= len(key) <= 6:
            return AssetInfo(key, EQUITY, key, f"{key}.IS")

    # Cozulemeyen sembolu fon varsayariz: TEFAS zaten anlasilir hata dondurur.
    return AssetInfo(key, FUND, key, None)


def describe(code: str) -> str:
    """`portfoy list` gibi yerlerde tur etiketi."""
    return resolve(code).kind


# --------------------------------------------------------------------------
# yfinance kaynagi
# --------------------------------------------------------------------------

def _yf_close(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """yfinance'ten kapanis fiyatlari. Gecici hatalara karsi yeniden dener."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise TefasError(
            "yfinance kurulu değil — hisse/altın/döviz için gerekli. "
            "Kurulum: pip install yfinance"
        ) from exc

    import time

    symbols = list(dict.fromkeys(symbols))
    son_hata: Exception | None = None

    for deneme in range(3):
        try:
            frame = yf.download(
                symbols,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                progress=False,
                auto_adjust=False,
                threads=False,
            )
            if frame is None or frame.empty:
                son_hata = TefasError("boş sonuç")
            else:
                close = frame["Close"]
                if close.ndim == 1:            # tek sembol -> Series
                    close = close.to_frame(symbols[0])
                cleaned = close.dropna(how="all")
                if not cleaned.empty:
                    return cleaned
                son_hata = TefasError("tümü boş")
        except Exception as exc:               # ag/veri hatalari
            son_hata = exc
        time.sleep(1.5 * (deneme + 1))

    raise TefasError(f"yfinance verisi alınamadı: {son_hata}")


def _series_from(close: pd.DataFrame, column: str) -> pd.Series:
    if column not in close.columns:
        raise TefasError(f"{column}: fiyat serisi bulunamadı")
    series = close[column].dropna()
    if series.empty:
        raise TefasError(f"{column}: geçerli fiyat kaydı yok")
    return series.astype(float)


# --------------------------------------------------------------------------
# Tur bazli cekiciler
# --------------------------------------------------------------------------

def _fetch_equity(info: AssetInfo, start: date, end: date) -> FundHistory:
    close = _yf_close([info.vendor], start, end)
    series = _series_from(close, info.vendor)
    return FundHistory(code=info.code, title=f"{info.code} (BIST)",
                       prices=series, source="Yahoo Finance")


def _fetch_currency(info: AssetInfo, start: date, end: date) -> FundHistory:
    close = _yf_close([info.vendor], start, end)
    series = _series_from(close, info.vendor)
    return FundHistory(code=info.code, title=f"{info.title} / TL",
                       prices=series, source="Yahoo Finance")


def _fetch_metal(info: AssetInfo, start: date, end: date) -> FundHistory:
    """Gram maden fiyati = (ons USD / 31,1034768) x USDTRY.

    Ons ve kur takvimleri birebir ortusmedigi icin ileri doldurup kesisim
    gunlerini aliriz.
    """
    close = _yf_close([info.vendor, USDTRY], start, end)
    if info.vendor not in close.columns or USDTRY not in close.columns:
        raise TefasError(f"{info.code}: ons fiyatı veya USD/TRY kuru alınamadı")

    birlesik = close[[info.vendor, USDTRY]].ffill().dropna()
    if birlesik.empty:
        raise TefasError(f"{info.code}: ortak tarih bulunamadı")

    gram_try = birlesik[info.vendor] / TROY_OUNCE_G * birlesik[USDTRY]
    gram_try = gram_try[gram_try > 0]
    if gram_try.empty:
        raise TefasError(f"{info.code}: geçerli fiyat hesaplanamadı")

    return FundHistory(code=info.code, title=info.title,
                       prices=gram_try.astype(float),
                       source="Yahoo Finance (ons × USD/TRY)")


_FETCHERS = {
    EQUITY: _fetch_equity,
    CURRENCY: _fetch_currency,
    METAL: _fetch_metal,
}


def fetch_history(code: str, lookback_days: int) -> FundHistory:
    """Fon disi bir varligin fiyat gecmisi."""
    info = resolve(code)
    fetcher = _FETCHERS.get(info.kind)
    if fetcher is None:
        raise TefasError(f"{code}: fon dışı kaynak tanımlı değil")

    end = date.today()
    start = end - timedelta(days=lookback_days)
    return fetcher(info, start, end)
