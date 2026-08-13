"""Uygulama geneli sabitler ve dosya yollari."""

from __future__ import annotations

from pathlib import Path

# --- Dizinler --------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "veri"
OUTPUT_DIR = BASE_DIR / "ciktilar"
CACHE_DIR = DATA_DIR / "cache"

PORTFOLIO_FILE = DATA_DIR / "portfoy.json"
MAIL_CONFIG_FILE = DATA_DIR / "mail.json"

# --- Portfoy varsayilanlari ------------------------------------------------
DEFAULT_FUNDS = ("TLY", "DFI", "TMV", "PBR")
DEFAULT_TARGET_MONTHLY_RETURN = 12.0  # yuzde

# --- Veri cekme ------------------------------------------------------------
# TEFAS hafta sonu / resmi tatillerde fiyat yayinlamaz. 30 gunluk getiriyi
# hesaplayabilmek icin fazladan tampon birakiyoruz.
LOOKBACK_DAYS = 50
HTTP_TIMEOUT = 20  # saniye
HTTP_RETRIES = 3
CACHE_TTL_SECONDS = 60 * 60 * 3  # 3 saat

# TEFAS'in guncel JSON ucu. Eski BindHistoryInfo/TarihselVeriler ucu 2026'da
# kapatildi; fon basina "periyod" (ay) parametresiyle calisan bu uc onun yerini
# aldi. Sitenin HTML'i JavaScript korumasi arkasinda, bu API ise aciktir.
TEFAS_PRICE_URL = "https://www.tefas.gov.tr/api/funds/fonFiyatBilgiGetir"

# Getiri periyotlari: etiket -> pandas.DateOffset argumanlari.
# "gunluk" ozel: takvim yerine bir onceki islem gunu kullanilir.
#
# "aylik" bilerek {"months": 1} - yani "1 ay onceki ayni takvim gunu", 30 gun
# DEGIL. TEFAS'in "Son 1 Ay Getirisi" rakami bu kurali kullaniyor; 30 gun ile
# hesaplayinca ay basinda sicrama yapan fonlarda sonuc TEFAS'tan sapiyordu
# (or. DFI 31.07.2026'da: 30 gun -> %10,14 ama TEFAS -> %14,0042).
# Ay sonlarinda pandas gunu kirpar: 31.07 - 1 ay = 30.06.
PERIODS: dict[str, dict | None] = {
    "gunluk": None,
    "haftalik": {"days": 7},
    "aylik": {"months": 1},
}

PERIOD_LABELS = {
    "gunluk": "Günlük",
    "haftalik": "Haftalık",
    "aylik": "Aylık",
}

# --- E-posta ---------------------------------------------------------------
# Kimlik bilgileri BURAYA YAZILMAZ. Ortam degiskeni, sistem anahtarligi veya
# veri/mail.json uzerinden okunur; ayrinti icin portfoy/mailer.py.
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465  # SSL. 587 verilirse STARTTLS kullanilir.
# Bos birakilir: alici verilmezse gonderen hesabin kendi adresi kullanilir.
# Sabit alici icin PORTFOY_MAIL_TO ortam degiskenini veya --to bayragini kullanin.
DEFAULT_MAIL_TO = ""
SMTP_TIMEOUT = 30  # saniye

# --- Renk paleti (dataviz referans paleti, slot 1-8) -----------------------
# Slot sirasi CVD guvenlik mekanizmasidir; karistirmayin, dongusel atamayin.
PALETTES = {
    "light": {
        "series": [
            "#2a78d6",  # 1 mavi
            "#eb6834",  # 2 turuncu
            "#1baf7a",  # 3 turkuaz
            "#eda100",  # 4 sari
            "#e87ba4",  # 5 macenta
            "#008300",  # 6 yesil
            "#4a3aa7",  # 7 mor
            "#e34948",  # 8 kirmizi
        ],
        "surface": "#fcfcfb",
        "page": "#f9f9f7",
        "text_primary": "#0b0b0b",
        "text_secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "positive": "#006300",
        "negative": "#d03b3b",
    },
    "dark": {
        "series": [
            "#3987e5",
            "#d95926",
            "#199e70",
            "#c98500",
            "#d55181",
            "#008300",
            "#9085e9",
            "#e66767",
        ],
        "surface": "#1a1a19",
        "page": "#0d0d0d",
        "text_primary": "#ffffff",
        "text_secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "positive": "#0ca30c",
        "negative": "#d03b3b",
    },
}


def series_color(index: int, theme: str = "light") -> str:
    """Slot sirasina gore seri rengi dondurur; palet asilirsa griye duser."""
    palette = PALETTES[theme]["series"]
    if index < len(palette):
        return palette[index]
    return PALETTES[theme]["muted"]


def ensure_dirs() -> None:
    for directory in (DATA_DIR, OUTPUT_DIR, CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
