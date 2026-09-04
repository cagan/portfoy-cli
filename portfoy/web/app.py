"""FastAPI uygulamasi: rotalar, guvenlik kabugu, sablon baglama.

Tasarim kararlari:

* **Sunucu tarafinda uretilen HTML.** Derleme adimi, paket yoneticisi ve
  istemci durumu yok; sayfa ne gosteriyorsa sunucu onu biliyor. Tek kullanicilik
  bir portfoy aracinda SPA'nin getirdigi karmasik bir bedel, getirisi yok.
* **POST-Redirect-GET.** Her mutasyon bir yonlendirmeyle biter; tarayici
  yenilendiginde islem TEKRARLANMAZ. Para kaydeden bir formda ikinci kez
  gonderilen alis, sessizce iki katina cikan pozisyon demektir.
* **Uc noktalar `def` (async degil).** Isin tamami bloklayan G/C: TEFAS
  istekleri, dosya yazimi, matplotlib. FastAPI senkron uc noktalari is
  parcacigi havuzunda kosturur; `async def` yazip icinde bloklamak olay
  dongusunu tikardi.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import date as _date, datetime as _datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .. import __version__, config
from ..formatting import (fmt_money, fmt_money_change, fmt_number, fmt_pct,
                         fmt_price, fmt_units)
from . import schemas
from .services import PortfoyServisi, ServisHatasi

logger = logging.getLogger(__name__)

_BURASI = Path(__file__).resolve().parent

# Varsayilan olarak yalnizca bu makine. `create_app(izinli_konaklar=...)` ile
# genisletilebilir; `portfoy web --host` bunu doldurur, boylece baglanma adresi
# ile kabul edilen konak adi birbirinden AYRISMAZ (ayrisirsa sunucu calisir ama
# her istegi 403'ler).
YEREL_KONAKLAR = frozenset({"127.0.0.1", "localhost", "::1"})

# Yonlendirme sirasinda tasinan bildirimlerin omru. Kullanici yonlendirmeyi
# izlemezse (sekmeyi kapatirsa) kayit burada kalir; sureli temizlik olmazsa
# sozluk suresiz buyurdu.
_BILDIRIM_TTL_SANIYE = 300
_BILDIRIM_UST_SINIR = 200


def _konak_adi(konak: str) -> str:
    """'[::1]:8000' -> '::1', '127.0.0.1:8000' -> '127.0.0.1'.

    Koseli parantez IPv6 icin sart: adres zaten iki nokta iceriyor, once
    `split(":")` yapmak onu ortasindan bolerdi.
    """
    konak = konak.strip().lower()
    if konak.startswith("["):
        kapanis = konak.find("]")
        return konak[1:kapanis] if kapanis > 0 else ""
    if konak.count(":") > 1:
        # Koseli parantezsiz cikplak IPv6 ('::1'). RFC bunu Host basliginda
        # parantezli ister ama istemciler her zaman uymuyor; ':' sayisina
        # bakmak, portlu 'ana:8000' ile IPv6'yi guvenle ayirir.
        return konak
    return konak.split(":")[0]


def _dogrulama_mesaji(exc: ValidationError | RequestValidationError) -> str:
    """Dogrulama hatasini tek satirlik Turkce mesaja indirger.

    Ham hata alan adlarini ve tip kodlarini icerir; kullaniciya gosterilecek
    sey yalnizca ne yapmasi gerektigi.
    """
    parcalar = []
    for hata in exc.errors():
        mesaj = str(hata.get("msg", ""))
        mesaj = mesaj.replace("Value error, ", "").replace("Assertion failed, ", "")
        alan = ".".join(
            str(item) for item in hata.get("loc", ()) if item not in {"body", "query"}
        )
        # Pydantic'in kendi mesajlari Ingilizce; alan bazinda Turkcelerini
        # koyuyoruz. schemas.py cogu alani zaten `str` alip kendi mesajiyla
        # cevirdigi icin buraya yalnizca eksik alan / bilinmeyen secim duser.
        if hata.get("type") == "missing":
            mesaj = f"'{alan}' alanı zorunlu."
        elif hata.get("type") in {"enum", "literal_error"}:
            mesaj = f"'{alan}' için geçersiz seçim."
        elif alan and not mesaj.endswith("."):
            mesaj = f"{alan}: {mesaj}"
        parcalar.append(mesaj)

    goruldu, benzersiz = set(), []
    for parca in parcalar:
        if parca not in goruldu:
            goruldu.add(parca)
            benzersiz.append(parca)
    return " · ".join(benzersiz) or "Form geçersiz."


def create_app(
    data_file: Path | None = None,
    output_dir: Path | None = None,
    izinli_konaklar: frozenset[str] | set[str] | None = None,
) -> FastAPI:
    servis = PortfoyServisi(data_file=data_file, output_dir=output_dir)
    konaklar = frozenset(izinli_konaklar) if izinli_konaklar else YEREL_KONAKLAR

    app = FastAPI(
        title="Portföy",
        version=__version__,
        # Swagger UI'yi CDN'den ceker; cevrimdisiyken bos gorunur. Sema
        # (/openapi.json) her zaman yereldir.
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.servis = servis

    templates = Jinja2Templates(directory=str(_BURASI / "templates"))
    templates.env.globals.update(
        surum=__version__,
        # Terminal, Excel ve grafiklerle AYNI bicimlendiriciler. Sablonda
        # elle bicimlendirmek ('%+.2f' + virgul degistirme) ayni rakamin iki
        # yerde farkli gorunmesine yol acardi.
        fmt_money=fmt_money,
        fmt_money_change=fmt_money_change,
        fmt_price=fmt_price,
        fmt_units=fmt_units,
        fmt_number=fmt_number,
        fmt_pct=fmt_pct,
        PERIOD_LABELS=config.PERIOD_LABELS,
        # Kolon tabani aciklamalari da config'ten: konsol, Excel ve e-posta ile
        # AYNI cumle gorunsun. Sablonda elle yazilsa dort metin ayrisirdi.
        COLUMN_BASIS_NOTE=config.COLUMN_BASIS_NOTE,
        FLOW_MARK=config.FLOW_MARK,
        FLOW_MARK_NOTE=config.FLOW_MARK_NOTE,
        TOTAL_BASIS_NOTE=config.TOTAL_BASIS_NOTE,
    )
    app.mount("/static", StaticFiles(directory=str(_BURASI / "static")), name="static")

    # ----------------------------------------------------------------------
    # Guvenlik kabugu
    # ----------------------------------------------------------------------
    @app.middleware("http")
    async def erisim_kalkani(request: Request, call_next):
        """Konak ve kaynak dogrulamasi.

        Arayuzde kimlik dogrulama YOKTUR; bu iki kontrol mutasyonlarin tek
        savunmasidir, o yuzden ikisi de katidir.
        """
        konak = request.headers.get("host", "")
        if konak and _konak_adi(konak) not in konaklar:
            # DNS yeniden baglama (rebinding): kotu bir site kendi alan adini
            # 127.0.0.1'e cozdurup tarayicinizdan buraya istek attirabilir.
            # Baglantinin yerel olmasi bunu engellemez, konak adi engeller.
            return HTMLResponse(
                "<h1>403</h1><p>Bu konak adı üzerinden erişime izin verilmiyor.</p>",
                status_code=403,
            )

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            kaynak = request.headers.get("origin")
            # Origin ZORUNLU. Modern tarayicilar same-origin form POST'larinda
            # da Origin gonderir, dolayisiyla bu kati kural arayuzu kirmaz;
            # buna karsilik "basligi hic gondermeyen" istek CSRF'in en kolay
            # yoludur. Bilerek POST atan betikler basligi eklemelidir.
            if kaynak is None:
                return HTMLResponse(
                    "<h1>403</h1><p>İsteğin kaynağı belirtilmemiş.</p>",
                    status_code=403,
                )
            parcalar = urlsplit(kaynak)
            # TAM origin karsilastirmasi (sema + konak + port). Yalnizca konak
            # adina bakmak 'http://localhost:3000' (makinedeki baska bir yerel
            # sunucu) ve 'http://localhost:8000.evil.com' gibi kaynaklari kabul
            # ederdi; semayi atlamak 'https://127.0.0.1:8000'i kabul ederdi -
            # o da ayri bir origin'dir.
            if (
                parcalar.scheme != request.url.scheme
                or parcalar.netloc.lower() != konak.strip().lower()
            ):
                return HTMLResponse(
                    "<h1>403</h1><p>İsteğin kaynağı doğrulanamadı.</p>",
                    status_code=403,
                )

        return await call_next(request)

    # ----------------------------------------------------------------------
    # Bildirimler (flash)
    # ----------------------------------------------------------------------
    # Mesajlar ISTEGE baglidir, sunucuya degil. Ortak bir listede tutulsalardi
    # iki acik sekmede mesaj yanlis sekmeye gider, dogru sekmede kaybolurdu -
    # ve kullanici onay gormedigi islemi ikinci kez girerdi; tam da PRG'nin
    # engellemeye calistigi sey. Yonlendirme sirasinda tek kullanimlik bir
    # jetonla tasinirlar.
    _bekleyen: dict[str, tuple[float, list[tuple[str, str]]]] = {}
    _bekleyen_kilidi = threading.Lock()

    def bildir(request: Request, tur: str, mesaj: str) -> None:
        kuyruk = getattr(request.state, "bildirimler", None)
        if kuyruk is None:
            kuyruk = []
            request.state.bildirimler = kuyruk
        kuyruk.append((tur, mesaj))

    def _bekleyeni_ayikla(simdi: float) -> None:
        """Suresi gecmis ve tasan kayitlari at. Kilit tutulurken cagrilir."""
        for jeton in [
            j for j, (t, _) in _bekleyen.items() if simdi - t > _BILDIRIM_TTL_SANIYE
        ]:
            _bekleyen.pop(jeton, None)
        while len(_bekleyen) > _BILDIRIM_UST_SINIR:
            _bekleyen.pop(next(iter(_bekleyen)), None)

    def geri(request: Request, yol: str = "/") -> RedirectResponse:
        """PRG'nin R'si: 303 ile GET'e dondurur, yenilemede tekrar POST olmaz."""
        kuyruk = getattr(request.state, "bildirimler", None)
        if kuyruk:
            jeton = secrets.token_urlsafe(12)
            with _bekleyen_kilidi:
                _bekleyen[jeton] = (time.monotonic(), kuyruk)
                _bekleyeni_ayikla(time.monotonic())
            yol = f"{yol}{'&' if '?' in yol else '?'}m={jeton}"
        return RedirectResponse(yol, status_code=303)

    def bildirimleri_al(request: Request) -> list[tuple[str, str]]:
        """Yonlendirmeyle tasinanlar + bu istek sirasinda uretilenler."""
        jeton = request.query_params.get("m")
        tasinan: list[tuple[str, str]] = []
        if jeton:
            with _bekleyen_kilidi:
                kayit = _bekleyen.pop(jeton, None)
            if kayit is not None:
                tasinan = kayit[1]
        return tasinan + list(getattr(request.state, "bildirimler", []))

    def sayfa(request: Request, ad: str, **baglam) -> HTMLResponse:
        portfolio = servis.portfoy()
        return templates.TemplateResponse(
            request=request,
            name=ad,
            context={
                "bildirimler": bildirimleri_al(request),
                "portfoy": portfolio,
                "acik_kodlar": portfolio.codes,
                "kapanmis_kodlar": portfolio.closed_codes,
                # Tarih girdilerinin `max` siniri: tarayici gelecekteki gunu
                # zaten secturmesin. Sunucu tarafi dogrulama yine de kalir -
                # istemci kontrolu atlatilabilir.
                "bugun": _date.today().isoformat(),
                **baglam,
            },
        )

    def formu_isle(request: Request, model, veri: dict, yol: str):
        """Ortak form akisi: dogrula, hatayi bildir, yonlendir.

        Basariliysa (model_ornegi, None), degilse (None, yonlendirme) doner.
        """
        try:
            return model(**veri), None
        except ValidationError as exc:
            bildir(request, "hata", _dogrulama_mesaji(exc))
            return None, geri(request, yol)

    # FastAPI'nin kendi 422 JSON cevabi arayuzde ham hata ekrani olarak
    # gorunurdu: eksik zorunlu alan da bildirim akisina girsin.
    @app.exception_handler(RequestValidationError)
    async def form_hatasi(request: Request, exc: RequestValidationError):
        if request.method == "POST" and "text/html" in request.headers.get("accept", ""):
            jeton = secrets.token_urlsafe(12)
            with _bekleyen_kilidi:
                _bekleyen[jeton] = (
                    time.monotonic(), [("hata", _dogrulama_mesaji(exc))]
                )
                _bekleyeni_ayikla(time.monotonic())
            nereye = request.headers.get("referer") or "/"
            yol = urlsplit(nereye).path or "/"
            return RedirectResponse(f"{yol}?m={jeton}", status_code=303)
        return HTMLResponse(_dogrulama_mesaji(exc), status_code=422)

    # ----------------------------------------------------------------------
    # Durum
    # ----------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def durum(request: Request, yenile: bool = False):
        sonuc = hata = None
        try:
            sonuc = servis.analiz(yenile=yenile)
        except ServisHatasi as exc:
            hata = str(exc)
        for ozet in servis.son_cozulenler:
            bildir(request, "basari", f"Bekleyen emir gerçekleşti → {ozet}")
        servis.son_cozulenler = []
        try:
            bekleyenler = servis.bekleyen_emirler()
        except ServisHatasi:
            bekleyenler = []
        return sayfa(request, "durum.html", sonuc=sonuc, veri_hatasi=hata,
                     bekleyenler=bekleyenler)

    @app.post("/yenile")
    def yenile(request: Request):
        try:
            servis.onbellegi_bosalt()
            servis.analiz(yenile=True)
            bildir(request, "bilgi", "Fiyatlar TEFAS'tan yeniden çekildi.")
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/")

    # ----------------------------------------------------------------------
    # Islemler
    # ----------------------------------------------------------------------
    @app.get("/islemler", response_class=HTMLResponse)
    def islemler(request: Request):
        try:
            emirler = servis.bekleyen_emirler()
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
            emirler = []
        return sayfa(
            request, "islemler.html",
            bekleyenler=emirler,
            valor_kurallari=servis.valor_kurallari(),
        )

    @app.post("/emir")
    def emir_ekle(
        request: Request,
        code: str = Form(...),
        tur: str = Form(...),
        units: str = Form(""),
        tutar: str = Form(""),
        emir_tarihi: str = Form(...),
        saat: str = Form(""),
        kesim_taraf: str = Form(""),
        nakit_tarihi: str = Form(""),
        price: str = Form(""),
    ):
        form, yonlendir = formu_isle(
            request, schemas.EmirForm,
            {"code": code, "tur": tur, "units": units, "tutar": tutar,
             "emir_tarihi": emir_tarihi, "saat": saat,
             "kesim_taraf": kesim_taraf, "nakit_tarihi": nakit_tarihi,
             "price": price},
            "/islemler",
        )
        if form is None:
            return yonlendir

        # Saat verildiyse tam zaman; verilmediyse kesim tarafi kullanilir.
        if form.saat:
            sa, dk = form.saat.split(":")
            emir_zamani = _datetime.combine(
                form.parsed_emir_tarihi,
                _datetime.min.time().replace(hour=int(sa), minute=int(dk)),
            )
            kesim_sonrasi = None
        else:
            emir_zamani = form.parsed_emir_tarihi
            kesim_sonrasi = form.kesim_sonrasi

        try:
            ozet, aciklama = servis.emir_ekle(
                form.code, form.signed_units, emir_zamani, kesim_sonrasi,
                beklenen_nakit=form.parsed_nakit, fiyat=form.price,
                tutar=form.tutar,
            )
            bildir(request, "basari", ozet)
            for satir in aciklama:
                tur_bildirim = "uyari" if satir.startswith(("UYARI", "NOT")) else "bilgi"
                bildir(request, tur_bildirim, satir)
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    @app.post("/emir/sil")
    def emir_sil(request: Request, emir_id: str = Form(...)):
        form, yonlendir = formu_isle(
            request, schemas.EmirSilForm, {"emir_id": emir_id}, "/islemler"
        )
        if form is None:
            return yonlendir
        try:
            bildir(request, "basari", servis.emir_sil(form.emir_id))
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    @app.post("/valor")
    def valor_kaydet(
        request: Request,
        code: str = Form(...),
        alis_valor: str = Form("1"),
        alis_nakit: str = Form("1"),
        satis_valor: str = Form("1"),
        satis_nakit: str = Form("2"),
        dogrula: bool = Form(False),
    ):
        form, yonlendir = formu_isle(
            request, schemas.ValorForm,
            {"code": code, "alis_valor": alis_valor, "alis_nakit": alis_nakit,
             "satis_valor": satis_valor, "satis_nakit": satis_nakit,
             "dogrula": dogrula},
            "/islemler",
        )
        if form is None:
            return yonlendir
        try:
            bildir(request, "basari", servis.valor_kaydet(
                form.code,
                {"alis_valor": form.alis_valor, "alis_nakit": form.alis_nakit,
                 "satis_valor": form.satis_valor, "satis_nakit": form.satis_nakit},
                dogrula=form.dogrula,
            ))
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    @app.post("/islemler")
    def islem_ekle(
        request: Request,
        code: str = Form(...),
        tur: str = Form(...),
        units: str = Form(...),
        date: str = Form(""),
        price: str = Form(""),
        force: bool = Form(False),
    ):
        form, yonlendir = formu_isle(
            request,
            schemas.IslemForm,
            {"code": code, "tur": tur, "units": units, "date": date,
             "price": price, "force": force},
            "/islemler",
        )
        if form is None:
            return yonlendir

        try:
            # Yalnizca YENI bir fonun ALISINDA. Satista "portfoyde yok" zaten
            # dogru ve daha ucuz hata; TEFAS'a sormak hem gereksiz bir ag
            # istegi hem de kullaniciyi yanlis yone (kod hatasi) gonderirdi.
            yeni_fon = (
                form.signed_units > 0
                and form.code not in servis.portfoy().positions
            )
            if yeni_fon and not form.force:
                taninmiyor = servis.fon_taninmiyor_mu(form.code)
                if taninmiyor is True:
                    bildir(
                        request, "hata",
                        f"TEFAS '{form.code}' kodunu tanımıyor. Kodu kontrol edin; "
                        f"doğru olduğundan eminseniz 'TEFAS doğrulamasını atla' "
                        f"kutusunu işaretleyip tekrar gönderin.",
                    )
                    return geri(request, "/islemler")
                if taninmiyor is None:
                    bildir(
                        request, "uyari",
                        f"{form.code} TEFAS'ta doğrulanamadı (bağlantı sorunu); "
                        f"kayıt yine de eklendi.",
                    )

            bildir(
                request, "basari",
                servis.islem_ekle(
                    form.code, form.signed_units, form.parsed_date, form.price
                ),
            )
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    @app.post("/adet-duzelt")
    def adet_duzelt(
        request: Request,
        code: str = Form(...),
        units: str = Form(...),
        date: str = Form(""),
        price: str = Form(""),
        onay: bool = Form(False),
    ):
        form, yonlendir = formu_isle(
            request,
            schemas.AdetDuzeltForm,
            {"code": code, "units": units, "date": date, "price": price, "onay": onay},
            "/islemler",
        )
        if form is None:
            return yonlendir
        try:
            bildir(
                request, "basari",
                servis.adet_duzelt(
                    form.code, form.units, form.parsed_date, form.price
                ),
            )
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    @app.post("/fon-sil")
    def fon_sil(request: Request, code: str = Form(...), onay: bool = Form(False)):
        form, yonlendir = formu_isle(
            request, schemas.FonSilForm, {"code": code, "onay": onay}, "/islemler"
        )
        if form is None:
            return yonlendir
        try:
            bildir(request, "basari", servis.fon_sil(form.code))
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    @app.post("/hedef")
    def hedef(request: Request, percent: str = Form(...)):
        form, yonlendir = formu_isle(
            request, schemas.HedefForm, {"percent": percent}, "/islemler"
        )
        if form is None:
            return yonlendir
        try:
            bildir(request, "basari", servis.hedef_ayarla(form.percent))
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/islemler")

    # ----------------------------------------------------------------------
    # Raporlar
    # ----------------------------------------------------------------------
    @app.get("/raporlar", response_class=HTMLResponse)
    def raporlar(request: Request):
        sonuc = hata = None
        try:
            sonuc = servis.analiz()
        except ServisHatasi as exc:
            hata = str(exc)
        return sayfa(
            request,
            "raporlar.html",
            sonuc=sonuc,
            veri_hatasi=hata,
            ciktilar=servis.cikti_listesi(),
            temalar=sorted(config.PALETTES),
            periyotlar=list(config.PERIODS),
        )

    @app.post("/raporlar")
    def rapor_uret(
        request: Request,
        theme: str = Form("light"),
        # Varsayilan YOK: hicbir kutu isaretlenmezse form bu alani hic
        # gondermez ve bir varsayilan, kullanicinin secimini sessizce
        # gecersiz kilardi. Bos liste `RaporForm`'da acik hataya donusur.
        periods: list[str] = Form([]),
        excel: bool = Form(False),
        charts_: bool = Form(False, alias="charts"),
    ):
        form, yonlendir = formu_isle(
            request,
            schemas.RaporForm,
            {"theme": theme, "periods": list(periods), "excel": excel,
             "charts": charts_},
            "/raporlar",
        )
        if form is None:
            return yonlendir
        try:
            uretilen = servis.cikti_uret(
                theme=form.theme,
                periods=tuple(form.periods),
                excel=form.excel,
                grafik=form.charts,
            )
            if uretilen:
                bildir(request, "basari", f"{len(uretilen)} dosya üretildi.")
            else:
                bildir(request, "uyari",
                       "Hiçbir dosya üretilemedi; günlüğü kontrol edin.")
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/raporlar")

    @app.get("/indir/{ad}")
    def indir(ad: str):
        try:
            yol = servis.cikti_yolu(ad)
        except ServisHatasi as exc:
            return HTMLResponse(f"<h1>404</h1><p>{exc}</p>", status_code=404)
        return FileResponse(yol, filename=yol.name)

    # ----------------------------------------------------------------------
    # E-posta
    # ----------------------------------------------------------------------
    @app.get("/eposta", response_class=HTMLResponse)
    def eposta(request: Request):
        return sayfa(
            request, "eposta.html",
            ayar=servis.eposta_ayari(), ciktilar=servis.cikti_listesi(),
        )

    @app.post("/eposta")
    def eposta_kaydet(
        request: Request,
        user: str = Form(...),
        recipients: str = Form(""),
        password: str = Form(""),
        host: str = Form(config.DEFAULT_SMTP_HOST),
        port: str = Form(str(config.DEFAULT_SMTP_PORT)),
    ):
        form, yonlendir = formu_isle(
            request,
            schemas.EpostaForm,
            {"user": user, "recipients": recipients, "password": password,
             "host": host, "port": port},
            "/eposta",
        )
        if form is None:
            return yonlendir
        try:
            bildir(
                request, "basari",
                servis.eposta_kaydet(
                    form.user, form.recipient_list, form.password,
                    form.host, form.port,
                ),
            )
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/eposta")

    @app.post("/eposta/gonder")
    def eposta_gonder(request: Request, ekle: bool = Form(False)):
        try:
            ekler = [item.yol for item in servis.cikti_listesi()] if ekle else []
            bildir(request, "basari", servis.eposta_gonder(ekler))
        except ServisHatasi as exc:
            bildir(request, "hata", str(exc))
        return geri(request, "/eposta")

    # ----------------------------------------------------------------------
    # JSON API
    # ----------------------------------------------------------------------
    # Sablonlarla ayni servisi kullanir; arayuzun gosterdigi her sey
    # programatik olarak da okunabilsin diye duruyor (/api/docs).
    @app.get("/api/portfoy")
    def api_portfoy():
        portfolio = servis.portfoy()
        return {
            "hedef_aylik_getiri": portfolio.target_monthly_return,
            "acik": {
                code: {
                    "adet": portfolio.positions[code].units,
                    "ortalama_maliyet": portfolio.positions[code].average_cost,
                    "alis_tarihi": portfolio.positions[code].acquired_on,
                }
                for code in portfolio.codes
            },
            "bekleyen": [
                {
                    "id": item.id, "kod": item.kod, "adet": item.adet,
                    "gerceklesme": item.gerceklesme,
                    "valor_gunu": item.valor_gunu, "nakit": item.nakit,
                    "valor_supheli": item.valor_supheli,
                }
                for item in servis.bekleyen_emirler()
            ],
            "kapanmis": {
                code: {
                    "kapanis": portfolio.positions[code].closed_on,
                    "hasilat": portfolio.positions[code].realized_proceeds,
                    "gerceklesen_kar": portfolio.positions[code].realized_profit,
                }
                for code in portfolio.closed_codes
            },
        }

    @app.get("/api/durum")
    def api_durum():
        sonuc = servis.analiz()
        if sonuc is None:
            return {"acik_pozisyon": False}
        analysis = sonuc.analysis
        return {
            "acik_pozisyon": True,
            "tarih": analysis.as_of,
            "toplam_deger": analysis.total_value,
            "hedef": analysis.target_monthly_return,
            "agirlikli_getiri": analysis.weighted_returns,
            "deger_degisim": {
                period: analysis.value_change(period) for period in config.PERIODS
            },
            # Dis para akisi getiriden AYRI raporlanir: toplam degerin neden
            # dustugunu/ciktigini aciklar ama kar/zarar degildir. Nakit bakiyesi
            # modellenmedigi icin (bkz. analytics) tek gorunur izi budur.
            "net_akis": {
                period: analysis.net_flow(period) for period in config.PERIODS
            },
            "akis_notu": {
                period: analysis.flow_note(period) for period in config.PERIODS
            },
            # Pencere icinde tamamen satilan, getiriye dahil edilen fonlar.
            "kapanmis_pozisyonlar": [
                {"kod": row.code, "kapanis": row.closed_on}
                for row in analysis.closed_rows
            ],
            "fonlar": [
                {
                    "kod": row.code,
                    "adi": row.title,
                    "adet": row.units,
                    "fiyat": row.price,
                    "deger": row.value,
                    "agirlik": row.weight_pct,
                    "getiri": row.returns,
                    "kar": row.profit,
                }
                for row in analysis.rows
            ],
            "uyarilar": sonuc.uyarilar,
        }

    return app


def run(
    host: str = "127.0.0.1",
    port: int = 8000,
    data_file: Path | None = None,
    output_dir: Path | None = None,
) -> None:
    """Sunucuyu baslatir. Varsayilan olarak yalnizca bu makineden erisilir.

    Baglanma adresi izinli konak listesine EKLENIR: aksi halde `--host` ile
    baslatilan sunucu calisir ama konak kalkani her istegi 403'lerdi.
    """
    import uvicorn

    uvicorn.run(
        create_app(
            data_file=data_file,
            output_dir=output_dir,
            izinli_konaklar=YEREL_KONAKLAR | {_konak_adi(host)},
        ),
        host=host,
        port=port,
        log_level="warning",
    )
