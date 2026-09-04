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
# Gunluk portfoy anlik goruntuleri: takvim ayi karnesinin GERCEKLESEN kolu
# buradan beslenir (bkz. portfoy/snapshots.py).
SNAPSHOT_FILE = DATA_DIR / "gecmis.json"
# Fon bazli valor kurallari (emir gununden gerceklesme gununu turetmek icin).
# TEFAS valoru yayinlamadigi icin elle beslenir; bkz. portfoy/valor.py.
VALOR_FILE = DATA_DIR / "valor.json"
# Verilmis ama henuz gerceklesmemis emirler: gerceklesme gunu belli, o gunun
# fiyati henuz yayimlanmamis. Fiyat gelince islem kaydina donusur.
PENDING_FILE = DATA_DIR / "bekleyen.json"

# --- Portfoy varsayilanlari ------------------------------------------------
DEFAULT_FUNDS = ("TLY", "DFI", "TMV", "PBR")
DEFAULT_TARGET_MONTHLY_RETURN = 12.0  # yuzde

# --- Veri cekme ------------------------------------------------------------
# TEFAS hafta sonu / resmi tatillerde fiyat yayinlamaz. 30 gunluk getiriyi
# hesaplayabilmek icin fazladan tampon birakiyoruz.
#
# Karnede gosterilecek en fazla KAPANMIS takvim ayi. Veri daha azsa eldeki
# kadari gosterilir; daha fazlaysa en yeniler alinir. `--months` ile artirilir.
#
# Varsayilan bilerek kisa: karne heniz "temsili" kolda calisiyor, yani gecmis
# aylar BUGUNKU agirliklarla canlandiriliyor. Portfoy o aylarda bu bilesimde
# degilse rakam gercek performans degildir - uzun bir gecmis, guven verdigi
# olcude yaniltir. Anlik goruntuler birikip aylar "gerceklesen"e dondukce
# `--months` ile pencereyi genisletmek anlamli hale gelir.
TRACK_RECORD_MONTHS = 3

# Cekilecek gecmis. Karne penceresinden TURETILIR: N kapanmis ay icin N ay +
# taban ayi + icinde bulunulan ay gerekir. Sabit bir sayi olsaydi `--months`
# buyutuldugunde fetch penceresi yetmez, karne sessizce kirpilirdi.
def lookback_for(months: int = TRACK_RECORD_MONTHS) -> int:
    """Karne penceresini karsilayan gun sayisi (en az 50)."""
    return max(50, (months + 2) * 31)


LOOKBACK_DAYS = lookback_for()
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

# Periyodun HANGI pencereyi olctugunu soyleyen uzun ad. "Aylik" etiketi tek
# basina "ay basindan bu yana" diye okunabiliyordu; oysa olculen sey kayan
# 1 aylik penceredir (bir ay onceki ayni takvim gunu -> bugun).
PERIOD_WINDOW_LABELS = {
    "gunluk": "Önceki işlem günü",
    "haftalik": "Son 7 gün",
    "aylik": "Son 1 ay",
}

# --- Kolon tabani aciklamalari --------------------------------------------
# Fon satirindaki "%" ile "₺" AYNI SEYI OLCMUYOR ve bunun ekranda yazmasi sart:
#
#   %  -> fonun BIRIM FIYAT getirisi (analytics.compute_returns)
#   ₺  -> portfoye yansiyan kazanc (analytics._flow_adjusted_change), yani
#         donem ICINDE alinan/satilan adet dikkate alinarak
#
# Donem icinde islem yoksa ikisi birebir tutar. Islem varsa ayrisirlar ve bu
# bir HATA DEGIL: TMV 13.08'de alim yaptigi icin aylik %25,74 (birim fiyat)
# ama +%39,32 (portfoye yansiyan) - fon ay basindan beri elde tutulmus gibi
# degil, gercekte tutulan adetle olculuyor. TL tarafi dogru olan taraftir;
# eskiden TL yuzdeden turetiliyordu ve TMV'nin aylik TL'si 150.980,60 ₺ diye
# sisik cikiyordu (gercegi 137.616,06 ₺).
#
# Metinler burada duruyor ki konsol, web, Excel ve e-posta ayni cumleyi
# gostersin; dort ayri kopya kacinilmaz olarak ayrisir.
COLUMN_BASIS_NOTE = (
    "% = fonun birim fiyat getirisi · "
    "₺ = portföye yansıyan kazanç (dönem içi alım/satım düzeltilmiş)"
)
# Kirpilmis periyot isareti "*" ile CAKISMASIN diye ayri sembol.
FLOW_MARK = "†"
FLOW_MARK_NOTE = (
    "† dönem içinde alım/satım var — yüzde ile ₺ aynı tabandan çıkmaz"
)
TOTAL_BASIS_NOTE = "Toplam getiri, dönem başındaki sermayenin getirisidir."
# Dar yerler (konsol ozet paneli) icin kisa bicim: orada "Taban" etiketi zaten
# cumlenin oznesini veriyor, tam cumle satiri sardirip okunaksiz yapiyordu.
TOTAL_BASIS_SHORT = "dönem başındaki sermaye"

AY_ADLARI = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)

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
        # Katki/degisim grafiklerinde isaret (polarite) rengi. Metindeki
        # yesil/kirmizi burada kullanilmaz: yesil-kirmizi cifti renk korlugunde
        # ayirt edilemiyor (protan ΔE 4,6). Mavi-kirmizi ayni islevi gorur ve
        # tum CVD kontrollerinden gecer (ΔE 23,8).
        "up": "#2a78d6",
        "down": "#d03b3b",
        "neutral_bar": "#52514e",  # toplam/ozet cubugu - seri rengi degil
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
        "up": "#3987e5",
        "down": "#d03b3b",
        "neutral_bar": "#c3c2b7",
    },
}


def series_color(index: int, theme: str = "light") -> str:
    """Slot sirasina gore seri rengi dondurur; palet asilirsa griye duser."""
    palette = PALETTES[theme]["series"]
    if index < len(palette):
        return palette[index]
    return PALETTES[theme]["muted"]


def yan_dosyalar(data_file: Path | None) -> dict[str, Path]:
    """Portfoy dosyasina eslik eden yan dosyalarin yollari.

    Gecmis, bekleyen emirler ve valor kurallari portfoye BAGLIDIR. Sabit yola
    yazsalardi alternatif bir portfoy dosyasiyla calismak (test portfoyu, ikinci
    hesap) asil portfoyun karnesini, emirlerini ve valor tablosunu bozardi.

    Tek yerde durmalarinin sebebi: CLI ve web ayni kurali kullansin. Iki ayri
    kopya kacinilmaz olarak ayrisir ve ayrildiginda hata "yanlis dosyaya
    yazildi" seklinde, yani en gec fark edilen bicimde ortaya cikar.
    """
    # `resolve()` sart: `--data-file veri/portfoy.json` (goreli) ham
    # karsilastirmada PORTFOLIO_FILE'a esit CIKMAZ ve gercek gecmis/bekleyen/
    # valor dosyalari sessizce gorunmez olurdu - duran bir satis emri raporda
    # kaybolur, karne sifirdan baslardi.
    if data_file is None or data_file.resolve() == PORTFOLIO_FILE.resolve():
        return {"gecmis": SNAPSHOT_FILE, "bekleyen": PENDING_FILE, "valor": VALOR_FILE}
    return {
        "gecmis": data_file.with_name(f"{data_file.stem}_gecmis.json"),
        "bekleyen": data_file.with_name(f"{data_file.stem}_bekleyen.json"),
        "valor": data_file.with_name(f"{data_file.stem}_valor.json"),
    }


def ensure_dirs() -> None:
    for directory in (DATA_DIR, OUTPUT_DIR, CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
