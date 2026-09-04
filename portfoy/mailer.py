"""Rapor e-postasi gonderimi (SMTP).

Kimlik bilgileri asla kaynak koda gomulmez. Sifre su sirayla aranir:
  1. PORTFOY_SMTP_PASSWORD ortam degiskeni
  2. Sistem anahtarligi (macOS Anahtar Zinciri) - `keyring` kuruluysa
  3. veri/mail.json dosyasi (yalnizca sahibin okuyabilecegi izinlerle)

Gmail icin normal hesap sifresi calismaz; iki adimli dogrulama acip
"Uygulama Sifresi" uretmeniz gerekir.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

from . import config
from .analytics import PortfolioAnalysis
from .formatting import (
    fmt_money, fmt_money_change, fmt_number, fmt_pct, fmt_points, fmt_price, fmt_units
)

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "portfoy-cli-smtp"

_XLSX_MIME = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class MailError(RuntimeError):
    """E-posta gonderilemedigi durumlarda firlatilir."""


# --------------------------------------------------------------------------
# Yapilandirma
# --------------------------------------------------------------------------
@dataclass
class MailConfig:
    host: str = config.DEFAULT_SMTP_HOST
    port: int = config.DEFAULT_SMTP_PORT
    user: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)

    @property
    def from_address(self) -> str:
        return self.sender or self.user

    def missing_fields(self) -> list[str]:
        eksik = []
        if not self.user:
            eksik.append("gönderen hesap (kullanıcı)")
        if not self.password:
            eksik.append("uygulama şifresi")
        if not self.recipients:
            eksik.append("alıcı adresi")
        return eksik

    @property
    def is_ready(self) -> bool:
        return not self.missing_fields()


def _read_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Mail ayar dosyasi okunamadi (%s): %s", path, exc)
        return {}


def _password_from_keyring(user: str) -> str:
    if not user:
        return ""
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, user) or ""
    except Exception as exc:  # anahtarlik kilitli / erisilemez olabilir
        logger.debug("Anahtarlik okunamadi: %s", exc)
        return ""


def _split_recipients(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    return [item.strip() for item in raw if str(item).strip()]


def load_config(path: Path | None = None) -> MailConfig:
    """Ortam degiskeni > ayar dosyasi onceligiyle yapilandirmayi kurar."""
    path = path or config.MAIL_CONFIG_FILE
    stored = _read_config_file(path)

    user = os.environ.get("PORTFOY_SMTP_USER") or stored.get("kullanici", "")
    sender = os.environ.get("PORTFOY_MAIL_FROM") or stored.get("gonderen", "")
    host = os.environ.get("PORTFOY_SMTP_HOST") or stored.get("sunucu") or config.DEFAULT_SMTP_HOST

    raw_port = os.environ.get("PORTFOY_SMTP_PORT") or stored.get("port")
    try:
        port = int(raw_port) if raw_port else config.DEFAULT_SMTP_PORT
    except (TypeError, ValueError):
        logger.warning("Geçersiz SMTP portu: %r — varsayılan kullanılıyor.", raw_port)
        port = config.DEFAULT_SMTP_PORT

    # Alici verilmemisse raporu gonderen hesabin kendisine yolla.
    recipients = _split_recipients(
        os.environ.get("PORTFOY_MAIL_TO")
        or stored.get("alicilar")
        or config.DEFAULT_MAIL_TO
        or user
    )

    password = (
        os.environ.get("PORTFOY_SMTP_PASSWORD")
        or _password_from_keyring(user)
        or stored.get("sifre", "")
    )

    return MailConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        sender=sender,
        recipients=recipients,
    )


def save_config(
    user: str,
    recipients: list[str],
    password: str | None = None,
    host: str = config.DEFAULT_SMTP_HOST,
    port: int = config.DEFAULT_SMTP_PORT,
    path: Path | None = None,
) -> tuple[Path, str]:
    """Ayarlari kaydeder.

    Returns:
        (ayar dosyasi yolu, sifrenin nereye yazildigi)
    """
    path = path or config.MAIL_CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = _read_config_file(path)

    payload = {
        "kullanici": user,
        "alicilar": recipients,
        "sunucu": host,
        "port": port,
    }

    if password:
        password_location = _store_password(user, password, payload)
    else:
        # Yeni sifre verilmediyse dosyadakini koru - "bos birakirsaniz degismez".
        password_location = "değiştirilmedi"
        if previous.get("sifre"):
            payload["sifre"] = previous["sifre"]

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)  # sifre dosyaya dustuyse baskasi okuyamasin
    except OSError:
        pass

    return path, password_location


def _store_password(user: str, password: str, payload: dict) -> str:
    """Sifreyi once anahtarliga, olmazsa ayar dosyasina yazar."""
    try:
        import keyring  # type: ignore[import-not-found]

        keyring.set_password(KEYRING_SERVICE, user, password)
        return "sistem anahtarlığı (Anahtar Zinciri)"
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Anahtarlığa yazılamadı: %s", exc)

    payload["sifre"] = password
    return "ayar dosyası (düz metin, 600 izniyle)"


# --------------------------------------------------------------------------
# Icerik
# --------------------------------------------------------------------------
def build_subject(analysis: PortfolioAnalysis) -> str:
    monthly = analysis.weighted_returns.get("aylik")
    parca = "aylık veri yok" if monthly is None else f"aylık {fmt_pct(monthly)}"
    durum = "hedef tuttu" if analysis.target_met else "hedefin altında"
    if analysis.target_gap is None:
        durum = "hedef değerlendirilemedi"
    return f"Portföy Raporu {analysis.as_of:%d.%m.%Y} — {parca}, {durum}"


def _marked(row, period: str) -> str:
    """Getiri metni, isaretleriyle.

    `*` fon periyodun tamaminda elde degildi.
    `†` periyot icinde alim/satim var; yuzde ile TL ayni tabandan cikmaz
        (bkz. config.COLUMN_BASIS_NOTE).
    """
    text = fmt_pct(row.returns.get(period))
    if row.is_partial(period):
        text += "*"
    return text + row.flow_mark(period)


def build_text_body(analysis: PortfolioAnalysis, track_record: list | None = None) -> str:
    lines = [
        f"TEFAS Portföy Raporu — {analysis.as_of:%d.%m.%Y}",
        "",
        f"Toplam Portföy Değeri: {fmt_money(analysis.total_value)}",
        f"Önceki Güne Göre Değişim: {fmt_money_change(analysis.value_change('gunluk'))}",
    ]
    for period, label in config.PERIOD_LABELS.items():
        lines.append(
            f"Ağırlıklı Ortalama {label} Getiri: "
            f"{fmt_pct(analysis.weighted_returns.get(period))} "
            f"({fmt_money_change(analysis.value_change(period))})"
        )
        # Akis AYRI satirda: portfoyden cikan para kar/zarar degil, ama toplam
        # degerin neden degistigini aciklayan tek sey o.
        flow = analysis.flow_note(period)
        if flow:
            lines.append(f"  Dış para akışı: {flow}")

    closed_note = analysis.closed_note
    if closed_note:
        lines.append(f"Kapanan pozisyon: {closed_note}")

    bounds = analysis.window("aylik")
    if bounds:
        lines.append(
            f"Ölçüm penceresi: {analysis.window_label('aylik')} (ay başından değil)"
        )

    closed = [item for item in (track_record or []) if item.closed]
    if closed:
        lines += ["", "Takvim ayı karnesi:"]
        for item in closed:
            mark = "" if item.is_actual else "  (temsili)"
            lines.append(f"  {item.label}: {fmt_pct(item.ret)}{mark}")

    lines += ["", f"Hedef Aylık Getiri: %{fmt_number(analysis.target_monthly_return)}"]
    gap = analysis.target_gap
    if gap is None:
        lines.append("Durum: aylık veri eksik, hedef değerlendirilemedi")
    else:
        durum = "HEDEF TUTTU" if gap >= 0 else "HEDEFİN ALTINDA"
        lines.append(f"Durum: {durum} ({fmt_points(gap)} yüzde puan)")

    profit = analysis.total_profit
    if profit is not None:
        lines.append(
            f"Toplam Kar/Zarar: {fmt_money_change(profit)} "
            f"({fmt_pct(analysis.total_profit_pct)})"
        )

    # `all_rows`: kapanmis fon da listelenir (adet 0), yoksa gunluk TL kolonunu
    # toplayan kullanici ozetteki rakami tutturamaz.
    lines += ["", "Fon detayı:"]
    for row in analysis.all_rows:
        kapanis = f"  [KAPANDI {row.closed_on:%d.%m}]" if row.closed_on else ""
        lines.append(
            f"  {row.code:<5} {fmt_units(row.units):>12} adet  "
            f"{fmt_price(row.price):>12} ₺  "
            f"ağırlık %{fmt_number(row.weight_pct, 1)}  "
            f"günlük {_marked(row, 'gunluk')} "
            f"({fmt_money_change(row.value_change('gunluk'))})  "
            f"haftalık {_marked(row, 'haftalik')}  "
            f"aylık {_marked(row, 'aylik')}{kapanis}"
        )

    partial = analysis.partial_rows()
    if partial:
        lines += ["", "* Periyodun tamamında portföyde değildi:"]
        lines += [f"  - {row.code}: {row.partial_note}" for row in partial]

    # Kolonlarin NEYI olctugu: aciklama olmadan "%" ile "₺"nin ayrismasi
    # tutarsizlik gibi okunuyor.
    lines += ["", config.COLUMN_BASIS_NOTE]
    if any(row.flow_periods for row in analysis.all_rows):
        lines.append(config.FLOW_MARK_NOTE)
    lines.append(config.TOTAL_BASIS_NOTE)

    if analysis.failures:
        lines += ["", "Verisi alınamayan fonlar:"]
        lines += [f"  - {code}: {message}" for code, message in sorted(analysis.failures.items())]

    lines += ["", "Bu e-posta portfoy-cli tarafından otomatik gönderildi."]
    return "\n".join(lines)


def _cell(content: str, palette: dict, align: str = "right", bold: bool = False,
          color: str | None = None) -> str:
    weight = "600" if bold else "400"
    ink = color or palette["text_primary"]
    return (
        f'<td style="padding:8px 12px;text-align:{align};font-weight:{weight};'
        f'color:{ink};border-bottom:1px solid {palette["grid"]};'
        f'white-space:nowrap">{content}</td>'
    )


def _track_record_html(track_record: list, analysis, palette: dict) -> str:
    """Takvim ayi karnesi: 4 sutunlu kompakt izgara.

    Gorseller e-posta istemcisinde engellenebiliyor; karne yalnizca grafikte
    kalirsa okunamaz olur. Bu yuzden ayni bilgi metin olarak da veriliyor.
    Izgara ic ice <table> ile kuruldu: `inline-block` Outlook'un Word motorunda
    calismiyor, tablo hucresi her istemcide calisiyor.
    """
    closed = [item for item in track_record if item.closed]
    if not closed:
        return ""

    target = analysis.target_monthly_return
    hit = sum(1 for item in closed if item.ret >= target)
    product = 1.0
    for item in closed:
        product *= 1.0 + item.ret / 100.0
    geometric = (product ** (1.0 / len(closed)) - 1.0) * 100.0

    columns = 4
    cells = []
    for item in closed:
        color = palette["positive"] if item.ret >= target else palette["negative"]
        mark = "" if item.is_actual else "†"
        cells.append(
            f'<td style="padding:6px 10px;width:25%">'
            f'<div style="color:{palette["text_secondary"]};font-size:11px">'
            f"{item.short_label}{mark}</div>"
            f'<div style="color:{color};font-size:14px;font-weight:600">'
            f"{fmt_pct(item.ret)}</div></td>"
        )
    rows = "".join(
        f"<tr>{''.join(cells[index:index + columns])}</tr>"
        for index in range(0, len(cells), columns)
    )

    hypothetical = all(not item.is_actual for item in closed)
    note = (
        f'<div style="margin-top:8px;color:{palette["muted"]};font-size:11px">'
        "† <strong>Temsili</strong> — o ay için portföy kaydı yoktu; fonların "
        "gerçek ay getirileri bugünkü ağırlıklarla canlandırıldı. "
        "Gerçekleşmiş performans değildir. Portföy kaydı biriktikçe bu aylar "
        "gerçekleşen getiriyle değişecek.</div>"
        if any(not item.is_actual for item in closed)
        else ""
    )
    heading = "Takvim Ayı Karnesi" + (" (temsili)" if hypothetical else "")

    return (
        f'<div style="margin-top:24px;padding:14px 16px;border-radius:6px;'
        f'background:{palette["page"]}">'
        f'<div style="font-weight:600;font-size:13px;'
        f'color:{palette["text_primary"]}">{heading}</div>'
        f'<div style="margin:2px 0 8px;color:{palette["text_secondary"]};font-size:12px">'
        f"{len(closed)} kapanmış ayda {hit} kez hedef tuttu · "
        f"aylık ortalama (bileşik) {fmt_pct(geometric)} · "
        f"hedef %{fmt_number(target)}</div>"
        f'<table style="width:100%;border-collapse:collapse" '
        f'cellspacing="0" cellpadding="0">{rows}</table>'
        f"{note}</div>"
    )


def build_html_body(
    analysis: PortfolioAnalysis,
    inline_images: dict[str, str],
    track_record: list | None = None,
) -> str:
    """E-posta gövdesi. Stiller satır içi — posta istemcileri <style> bloklarını atar."""
    palette = config.PALETTES["light"]
    positive, negative = palette["positive"], palette["negative"]

    def tone(value: float | None) -> str:
        if value is None:
            return palette["muted"]
        return positive if value >= 0 else negative

    headers = [
        "Fon", "Adet", "Fiyat", "Değer ₺", "Ağırlık",
        "Günlük", "Günlük ₺", "Haftalık", "Aylık", "Katkı",
    ]
    header_cells = "".join(
        f'<th style="padding:10px 12px;text-align:{"left" if i == 0 else "right"};'
        f'background:{palette["text_primary"]};color:#ffffff;font-size:12px;'
        f'font-weight:600;white-space:nowrap">{name}</th>'
        for i, name in enumerate(headers)
    )

    body_rows = []
    # `all_rows`: kapanmis satir da tabloda. Adet/deger 0 oldugu icin toplamlari
    # bozmaz ama "Günlük ₺" kolonunun toplami artik ozetteki rakami tutar.
    for row in analysis.all_rows:
        contribution = row.contributions.get("aylik")
        etiket = f"{row.code} · KAPANDI {row.closed_on:%d.%m}" if row.closed_on else row.code
        cells = [
            _cell(etiket, palette, "left", bold=True),
            _cell(fmt_units(row.units), palette),
            _cell(fmt_price(row.price), palette),
            _cell(fmt_number(row.value, 0), palette),
            _cell(f"%{fmt_number(row.weight_pct, 1)}", palette),
        ]
        for period in ("gunluk", "haftalik", "aylik"):
            value = row.returns.get(period)
            cells.append(_cell(_marked(row, period), palette, color=tone(value)))
            if period == "gunluk":  # yuzdenin hemen yaninda TL karsiligi
                change = row.value_change("gunluk")
                cells.append(
                    _cell(
                        fmt_money_change(change),
                        palette,
                        color=tone(change),
                    )
                )
        cells.append(_cell(fmt_points(contribution), palette, color=tone(contribution)))
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    daily_change = analysis.value_change("gunluk")
    summary_rows = [
        ("Toplam Portföy Değeri", fmt_money(analysis.total_value), palette["text_primary"]),
        (
            "Önceki Güne Göre Değişim",
            "veri yok" if daily_change is None else fmt_money_change(daily_change),
            tone(daily_change),
        ),
    ]
    for period, label in config.PERIOD_LABELS.items():
        value = analysis.weighted_returns.get(period)
        suffix = "*" if analysis.partial_rows(period) else ""
        # Etiketin yanina olculen pencere: "Aylık" tek basina ay basindan bu
        # yana diye okunabiliyordu.
        bounds = analysis.window(period)
        window_html = (
            f' <span style="color:{palette["muted"]};font-size:11px">'
            f"{bounds[0]:%d.%m} → {bounds[1]:%d.%m}</span>"
            if bounds
            else ""
        )
        summary_rows.append(
            (
                f"Ağırlıklı Ortalama {label}{window_html}",
                fmt_pct(value) + suffix,
                tone(value),
            )
        )
    # Akis satirlari getirilerin hemen ardinda: rakamin neden oldugu gibi
    # ciktigini aciklarlar. Renk NOTR - akis bir kazanc/kayip degil.
    for period, label in config.PERIOD_LABELS.items():
        flow = analysis.flow_note(period)
        if flow:
            summary_rows.append(
                (f"{label} Dış Para Akışı", flow, palette["text_secondary"])
            )
    closed_note = analysis.closed_note
    if closed_note:
        summary_rows.append(
            ("Kapanan Pozisyon", closed_note, palette["text_secondary"])
        )

    total_profit = analysis.total_profit
    if total_profit is not None:
        summary_rows.append(
            ("Toplam Kar/Zarar",
             f"{fmt_money_change(total_profit)} ({fmt_pct(analysis.total_profit_pct)})",
             tone(total_profit))
        )
    summary_rows.append(
        ("Hedef Aylık Getiri", f"%{fmt_number(analysis.target_monthly_return)}",
         palette["text_secondary"])
    )

    gap = analysis.target_gap
    if gap is None:
        banner_text = "Aylık veri eksik — hedef değerlendirilemedi"
        banner_color = palette["muted"]
    elif gap >= 0:
        banner_text = f"HEDEF TUTTU · {fmt_points(gap)} yüzde puan"
        banner_color = positive
    else:
        banner_text = f"HEDEFİN ALTINDA · {fmt_points(gap)} yüzde puan"
        banner_color = negative

    summary_html = "".join(
        f'<tr><td style="padding:6px 0;color:{palette["text_secondary"]};font-size:13px">'
        f"{label}</td>"
        f'<td style="padding:6px 0;text-align:right;font-weight:600;color:{color};'
        f'font-size:13px;white-space:nowrap">{value}</td></tr>'
        for label, value, color in summary_rows
    )

    images_html = "".join(
        f'<div style="margin-top:24px">'
        f'<img src="cid:{cid}" alt="{alt}" '
        f'style="max-width:100%;height:auto;border-radius:6px"></div>'
        for cid, alt in inline_images.items()
    )

    track_html = _track_record_html(track_record or [], analysis, palette)

    partial_html = ""
    partial = analysis.partial_rows()
    if partial:
        items = "".join(
            f"<li><strong>{row.code}</strong> — {row.partial_note}</li>"
            for row in partial
        )
        partial_html = (
            f'<div style="margin-top:16px;padding:10px 16px;border-radius:6px;'
            f'background:{palette["page"]};color:{palette["text_secondary"]};'
            f'font-size:12px">'
            f"<strong>* Periyodun tamamında portföyde değildi</strong> — bu fonların "
            f"getirisi alış tarihinden itibaren hesaplandı, TEFAS'ın tam periyot "
            f"rakamıyla aynı değildir.<ul>{items}</ul></div>"
        )

    # Kolon tabani kutusu: yildiz notuyla ayni uslupta, her zaman basilir.
    # Yildiz "fon ne kadar suredir elde" sorusunu, bu ise "kolon neyi olcuyor"
    # sorusunu cevapliyor - ikisi ayri kutularda kalsin.
    basis_items = [config.COLUMN_BASIS_NOTE]
    if any(row.flow_periods for row in analysis.all_rows):
        basis_items.append(config.FLOW_MARK_NOTE)
    basis_items.append(config.TOTAL_BASIS_NOTE)
    basis_html = (
        f'<div style="margin-top:16px;padding:10px 16px;border-radius:6px;'
        f'background:{palette["page"]};color:{palette["text_secondary"]};'
        f'font-size:12px">'
        + "".join(f"<div>{item}</div>" for item in basis_items)
        + "</div>"
    )

    failures_html = ""
    if analysis.failures:
        items = "".join(
            f"<li><strong>{code}</strong> — {message}</li>"
            for code, message in sorted(analysis.failures.items())
        )
        failures_html = (
            f'<div style="margin-top:24px;padding:12px 16px;border-radius:6px;'
            f'background:#fff6e5;color:{palette["text_primary"]};font-size:13px">'
            f"<strong>Verisi alınamayan fonlar</strong><ul>{items}</ul></div>"
        )

    return f"""\
<html><body style="margin:0;padding:24px;background:{palette['page']};
 font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:{palette['text_primary']}">
 <div style="max-width:760px;margin:0 auto;background:{palette['surface']};
  border-radius:10px;padding:28px;border:1px solid rgba(11,11,11,0.10)">

  <h1 style="margin:0;font-size:20px">TEFAS Portföy Raporu</h1>
  <p style="margin:4px 0 20px;color:{palette['text_secondary']};font-size:13px">
   Veri tarihi: {analysis.as_of:%d.%m.%Y}</p>

  <div style="padding:12px 16px;border-radius:6px;background:{banner_color};
   color:#ffffff;font-weight:600;font-size:14px">{banner_text}</div>

  <table style="width:100%;border-collapse:collapse;margin-top:20px;font-size:13px"
   cellspacing="0" cellpadding="0">
   <thead><tr>{header_cells}</tr></thead>
   <tbody>{''.join(body_rows)}</tbody>
  </table>

  <table style="width:100%;border-collapse:collapse;margin-top:20px;
   border-top:2px solid {palette['axis']}" cellspacing="0" cellpadding="0">
   {summary_html}
  </table>

  {track_html}
  {partial_html}
  {basis_html}
  {images_html}
  {failures_html}

  <p style="margin-top:24px;color:{palette['muted']};font-size:11px">
   portfoy-cli tarafından otomatik gönderildi. Yatırım tavsiyesi değildir.</p>
 </div>
</body></html>"""


# --------------------------------------------------------------------------
# Gonderim
# --------------------------------------------------------------------------
def build_message(
    analysis: PortfolioAnalysis,
    attachments: list[Path] | None,
    settings: MailConfig,
    track_record: list | None = None,
) -> EmailMessage:
    """Gonderilecek e-postayi kurar (duz metin + HTML + gomulu grafik + ekler)."""
    attachments = [path for path in (attachments or []) if path and path.exists()]

    # PNG'leri govdeye gomulecek sekilde ayir, digerlerini ek yap.
    inline_files = [p for p in attachments if p.suffix.lower() == ".png"]
    other_files = [p for p in attachments if p.suffix.lower() != ".png"]

    inline_map: dict[str, str] = {}
    inline_data: list[tuple[str, bytes]] = []
    for path in inline_files:
        try:
            inline_data.append((path.stem, path.read_bytes()))
            inline_map[path.stem] = path.stem.replace("_", " ")
        except OSError as exc:
            logger.warning("Grafik okunamadı, gövdeye eklenmiyor (%s): %s", path, exc)

    message = EmailMessage()
    message["Subject"] = build_subject(analysis)
    message["From"] = formataddr(("Portföy Takip", settings.from_address))
    message["To"] = ", ".join(settings.recipients)
    message["Date"] = formatdate(localtime=True)

    message.set_content(build_text_body(analysis, track_record))
    message.add_alternative(
        build_html_body(analysis, inline_map, track_record), subtype="html"
    )

    # HTML bolumu multipart/related'a cevrilir ki cid: baglantilari calissin.
    html_part = message.get_payload()[-1]
    for cid, data in inline_data:
        html_part.add_related(data, maintype="image", subtype="png", cid=f"<{cid}>")

    for path in other_files:
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("Ek okunamadı, atlanıyor (%s): %s", path, exc)
            continue
        subtype = _XLSX_MIME if path.suffix.lower() == ".xlsx" else "octet-stream"
        message.add_attachment(
            data, maintype="application", subtype=subtype, filename=path.name
        )

    return message


def send_report(
    analysis: PortfolioAnalysis,
    attachments: list[Path] | None = None,
    mail_config: MailConfig | None = None,
    track_record: list | None = None,
) -> list[str]:
    """Raporu e-posta ile gonderir. Basarisizlikta MailError firlatir.

    Returns:
        Gonderilen alici adresleri.
    """
    settings = mail_config or load_config()
    missing = settings.missing_fields()
    if missing:
        raise MailError(
            "E-posta ayarları eksik: " + ", ".join(missing) + ". "
            "Kurmak için: python main.py mail-ayar --kullanici ADRES"
        )

    _deliver(build_message(analysis, attachments, settings, track_record), settings)
    return list(settings.recipients)


def _deliver(message: EmailMessage, settings: MailConfig) -> None:
    context = ssl.create_default_context()
    try:
        if settings.port == 465:
            with smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=config.SMTP_TIMEOUT, context=context
            ) as server:
                server.login(settings.user, settings.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=config.SMTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings.user, settings.password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            f"Kimlik doğrulama reddedildi ({exc.smtp_code}). Gmail için normal hesap "
            "şifresi çalışmaz; iki adımlı doğrulamayı açıp bir Uygulama Şifresi "
            "üretmeniz gerekir: https://myaccount.google.com/apppasswords"
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(f"Alıcı adresi reddedildi: {exc.recipients}") from exc
    except smtplib.SMTPException as exc:
        raise MailError(f"SMTP hatası: {exc}") from exc
    except (OSError, ssl.SSLError) as exc:
        raise MailError(
            f"{settings.host}:{settings.port} adresine bağlanılamadı ({exc})"
        ) from exc
