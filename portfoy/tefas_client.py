"""TEFAS veri erisim katmani.

Iki bagimsiz kaynak denenir:
  1. `tefas-crawler` paketi (kuruluysa)
  2. TEFAS'in kendi web servisi (BindHistoryInfo) - dogrudan HTTP

Her fon icin ayri hata yakalanir: bir fon cekilemezse digerleri raporlanmaya
devam eder, uygulama cokmez.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from . import config

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class TefasError(RuntimeError):
    """Bir fonun verisi hicbir kaynaktan alinamadiginda firlatilir."""


@dataclass
class FundHistory:
    """Tek bir fonun tarihsel fiyat serisi."""

    code: str
    title: str
    prices: pd.Series  # index: datetime64, degerler: float fiyat
    source: str

    @property
    def latest_date(self) -> date:
        return self.prices.index[-1].date()

    @property
    def latest_price(self) -> float:
        return float(self.prices.iloc[-1])


# --------------------------------------------------------------------------
# Onbellek
# --------------------------------------------------------------------------
def _cache_path(code: str, start: date, end: date) -> Path:
    return config.CACHE_DIR / f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.json"


def _read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > config.CACHE_TTL_SECONDS:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        frame = pd.DataFrame(payload["rows"])
        if frame.empty:
            return None
        frame["date"] = pd.to_datetime(frame["date"])
        frame.attrs["title"] = payload.get("title", "")
        frame.attrs["source"] = payload.get("source", "cache") + " (önbellek)"
        return frame
    except (json.JSONDecodeError, KeyError, OSError, ValueError) as exc:
        logger.debug("Onbellek okunamadi (%s): %s", path.name, exc)
        return None


def _write_cache(path: Path, frame: pd.DataFrame, title: str, source: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"date": row.date.strftime("%Y-%m-%d"), "price": float(row.price)}
            for row in frame.itertuples()
        ]
        path.write_text(
            json.dumps({"title": title, "source": source, "rows": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        # Onbellek yazilamamasi akisi durdurmaz.
        logger.debug("Onbellek yazilamadi (%s): %s", path.name, exc)


def clear_cache() -> int:
    """Onbellek dosyalarini siler, silinen dosya sayisini dondurur."""
    if not config.CACHE_DIR.exists():
        return 0
    removed = 0
    for item in config.CACHE_DIR.glob("*.json"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------
# Kaynak 1: tefas-crawler paketi
# --------------------------------------------------------------------------
def _fetch_via_library(code: str, start: date, end: date) -> pd.DataFrame:
    try:
        from tefas import Crawler  # type: ignore[import-not-found]
    except ImportError as exc:  # paket kurulu degil
        raise TefasError("tefas-crawler paketi kurulu degil") from exc

    crawler = Crawler()
    raw = crawler.fetch(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        name=code,
        columns=["code", "title", "date", "price"],
    )
    if raw is None or len(raw) == 0:
        raise TefasError(f"{code}: kütüphane bos sonuc dondurdu")

    frame = pd.DataFrame(raw)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    title = str(frame["title"].iloc[-1]) if "title" in frame else code
    frame = frame[["date", "price"]].dropna()
    frame.attrs["title"] = title
    frame.attrs["source"] = "tefas-crawler"
    return frame


# --------------------------------------------------------------------------
# Kaynak 2: dogrudan TEFAS web servisi
# --------------------------------------------------------------------------
def _fetch_via_http(code: str, start: date, end: date) -> pd.DataFrame:
    """TEFAS'in guncel JSON ucundan fiyat gecmisi ceker.

    Uc, tarih araligi degil "kac ay geriye" (periyod) parametresi alir; istenen
    araligi kapsayacak ay sayisini isteyip sonucu tarihe gore kirpiyoruz.
    """
    span_days = max((end - start).days, 1)
    months = max(1, math.ceil(span_days / 30) + 1)  # sinirda veri kaybetmemek icin +1

    payload = {"fonKodu": code, "dil": "TR", "periyod": months}
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(1, config.HTTP_RETRIES + 1):
        try:
            response = requests.post(
                config.TEFAS_PRICE_URL,
                json=payload,
                headers=headers,
                timeout=config.HTTP_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            logger.debug("%s: HTTP denemesi %d basarisiz: %s", code, attempt, exc)
            if attempt < config.HTTP_RETRIES:
                time.sleep(1.5 * attempt)  # kademeli bekleme
    else:
        raise TefasError(f"{code}: TEFAS servisine ulasilamadi ({last_error})")

    rows = body.get("resultList") if isinstance(body, dict) else None
    if not rows:
        raise TefasError(
            f"{code}: TEFAS bu fon icin veri dondurmedi (fon kodu yanlis olabilir)"
        )

    frame = pd.DataFrame(rows)
    if "tarih" not in frame or "fiyat" not in frame:
        raise TefasError(f"{code}: TEFAS yaniti beklenen alanlari icermiyor")

    frame["date"] = pd.to_datetime(frame["tarih"], errors="coerce").dt.normalize()
    frame["price"] = pd.to_numeric(frame["fiyat"], errors="coerce")

    title = code
    if "fonUnvan" in frame and not frame["fonUnvan"].isna().all():
        title = str(frame["fonUnvan"].dropna().iloc[-1])

    frame = frame[["date", "price"]].dropna()
    # Uc istenenden fazla gecmis dondurebilir; araliga kirp.
    frame = frame[frame["date"] >= pd.Timestamp(start)]
    if frame.empty:
        raise TefasError(f"{code}: istenen tarih araliginda kayit yok")

    frame.attrs["title"] = title
    frame.attrs["source"] = "TEFAS API"
    return frame


# --------------------------------------------------------------------------
# Genel arayuz
# --------------------------------------------------------------------------
def fetch_history(
    code: str,
    lookback_days: int = config.LOOKBACK_DAYS,
    use_cache: bool = True,
) -> FundHistory:
    """Bir fonun son `lookback_days` gunluk fiyat serisini getirir.

    Once onbellek, sonra tefas-crawler, sonra dogrudan HTTP denenir.
    Hicbiri calismazsa TefasError firlatilir.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    cache_file = _cache_path(code, start, end)

    frame: pd.DataFrame | None = None
    if use_cache:
        frame = _read_cache(cache_file)

    if frame is None:
        errors: list[str] = []
        for fetcher in (_fetch_via_library, _fetch_via_http):
            try:
                frame = fetcher(code, start, end)
                break
            except TefasError as exc:
                errors.append(str(exc))
            except Exception as exc:  # kutuphane ici beklenmedik hatalar
                errors.append(f"{fetcher.__name__}: {exc}")
        if frame is None:
            raise TefasError(f"{code} verisi alinamadi -> " + " | ".join(errors))

        _write_cache(cache_file, frame, frame.attrs.get("title", code),
                     frame.attrs.get("source", "bilinmiyor"))

    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame[frame["price"] > 0]
    if frame.empty:
        raise TefasError(f"{code}: gecerli fiyat kaydi bulunamadi")

    series = pd.Series(
        frame["price"].astype(float).to_numpy(),
        index=pd.DatetimeIndex(frame["date"]),
        name=code,
    )
    return FundHistory(
        code=code,
        title=frame.attrs.get("title", code) or code,
        prices=series,
        source=frame.attrs.get("source", "bilinmiyor"),
    )


def fetch_many(
    codes: list[str],
    lookback_days: int = config.LOOKBACK_DAYS,
    use_cache: bool = True,
) -> tuple[dict[str, FundHistory], dict[str, str]]:
    """Birden cok fonu ceker.

    Returns:
        (basarili sonuclar, {fon_kodu: hata_mesaji})
    """
    results: dict[str, FundHistory] = {}
    failures: dict[str, str] = {}
    for code in codes:
        try:
            results[code] = fetch_history(code, lookback_days, use_cache)
            continue
        except TefasError as exc:
            message = str(exc)
        except Exception as exc:  # beklenmedik her sey yalnizca bu fonu etkilesin
            message = f"beklenmedik hata: {exc}"

        failures[code] = message
        logger.warning("%s verisi alinamadi: %s", code, message)

    return results, failures
