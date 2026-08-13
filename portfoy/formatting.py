"""Turkce sayi bicimlendirme yardimcilari (1.234,56).

Terminal, grafik ve Excel etiketleri ayni bicimi kullansin diye tek yerde.
"""

from __future__ import annotations


def fmt_number(value: float, decimals: int = 2) -> str:
    """1234.5 -> '1.234,50' (binlik nokta, ondalik virgul)."""
    text = f"{value:,.{decimals}f}"
    # Once ',' -> gecici isaret, sonra '.' -> ',', en son gecici -> '.'
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_units(value: float) -> str:
    """Adet: en fazla 4 ondalik, gereksiz sifirlar atilir (1.500 / 12,25)."""
    text = fmt_number(value, 4)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text or "0"


def fmt_price(value: float) -> str:
    """Fiyat: kucuk birim fiyatlarda 4, buyuklerde 2 ondalik."""
    return fmt_number(value, 2 if abs(value) >= 100 else 4)


def fmt_money(value: float, decimals: int = 2) -> str:
    return f"{fmt_number(value, decimals)} ₺"


def fmt_money_change(value: float | None, decimals: int = 2) -> str:
    """Isaretli TL degisimi: '+17.478,69 ₺' / '-3.201,00 ₺'."""
    if value is None:
        return "—"
    return f"{'+' if value >= 0 else '-'}{fmt_money(abs(value), decimals)}"


def fmt_pct(value: float | None, signed: bool = True, decimals: int = 2) -> str:
    """Yuzde: '+%12,09' / '-%0,49'. Isaret '%' isaretinden once gelir."""
    if value is None:
        return "—"
    if value < 0:
        sign = "-"
    elif signed and value > 0:
        sign = "+"
    else:
        sign = ""
    return f"{sign}%{fmt_number(abs(value), decimals)}"


def fmt_points(value: float | None, decimals: int = 2) -> str:
    """Yuzde puan: her zaman isaretli ('+4,10' / '-0,79')."""
    if value is None:
        return "—"
    return f"{'+' if value >= 0 else '-'}{fmt_number(abs(value), decimals)}"
