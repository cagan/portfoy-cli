"""Agirlik, getiri ve agirlikli ortalama hesaplamalari."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from . import config
from .formatting import fmt_money
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
    # Pozisyonu kapatan son satisin tarihi; acik pozisyonlarda None. Kapanmis
    # satirlar `PortfolioAnalysis.closed_rows` icinde durur.
    closed_on: date | None = None
    # Akis duzeltmeli periyot istatistikleri; `analyze` doldurur.
    # `period_bases`   : yuzde getirinin PAYDASI (taban gunundeki deger)
    # `period_changes` : yuzde getirinin PAYI (pencere icindeki TL degisim)
    # Ikisi ayni hesaptan ciktigi icin arayuzde yan yana yazildiklarinda
    # birbirini tutarlar; bkz. `value_change` ve `period_base`.
    period_bases: dict[str, float | None] = field(default_factory=dict)
    period_changes: dict[str, float | None] = field(default_factory=dict)
    # Fonun pencerede TARIHLI bir lotu (alim veya satim) bulunan periyotlar.
    # Bu periyotlarda satirdaki "%" ile "₺" ayni tabandan CIKMAZ: yuzde birim
    # fiyat getirisi, TL ise gercekte tutulan adetle olculur (bkz.
    # config.COLUMN_BASIS_NOTE). Arayuzler bu hucreleri isaretler.
    flow_periods: set[str] = field(default_factory=set)

    @property
    def is_closed(self) -> bool:
        """Pozisyon pencere icinde tamamen satildi mi (elde adet yok)."""
        return self.closed_on is not None

    @property
    def weight_pct(self) -> float:
        return self.weight * 100.0

    def is_partial(self, period: str) -> bool:
        return period in self.partial_periods

    def has_flow(self, period: str) -> bool:
        """Fonun bu pencerede alim/satim islemi var mi."""
        return period in self.flow_periods

    def flow_mark(self, period: str) -> str:
        """Ayrisan hucrenin isareti ('†') ya da bos dize."""
        return config.FLOW_MARK if self.has_flow(period) else ""

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
        """Periyot icinde fonun urettigi TL kazanc/kayip (akis duzeltmeli).

        Rakami `analyze` lot bazli zaman-agirlikli atifla hesaplar (bkz.
        `_flow_adjusted_change`): pencere icindeki her ardisik islem gunu icin
        O GUN BASINDA elde olan adet ile fiyat farki carpilip toplanir.

        Eskiden bu deger fonun yuzde getirisinden geriye cikariliyordu
        (deger - deger / (1 + r)). O yol adedin pencere boyunca SABIT kaldigini
        varsayiyordu ve pencere icinde tamami satilan fonda coker: fonun bugunku
        degeri 0 oldugu icin kazanci/kaybi da 0 cikiyor, o gun gercekten yasanan
        hareket portfoy getirisinden tamamen dusuyordu.
        """
        return self.period_changes.get(period)

    def period_base(self, period: str) -> float | None:
        """Periyodun taban degeri: taban gunundeki adet x taban gunundeki fiyat.

        Yuzde getirinin PAYDASI budur. Fon pencere icinde alindiysa taban 0'dir
        (o gun elde yoktu); pencere icinde satildiysa taban DOLU kalir, cunku
        pencere basinda gercekten elde tutuluyordu - kapanmis pozisyonu paydadan
        dusurmek, kaybi kucuk bir tabana bolup buyutmek olurdu.
        """
        return self.period_bases.get(period)


@dataclass
class PortfolioAnalysis:
    """Portfoyun tamaminin analiz sonucu."""

    rows: list[FundRow]
    failures: dict[str, str]
    total_value: float
    weighted_returns: dict[str, float | None]  # yuzde
    # Her periyot icin, akis duzeltmeli getiri hesabina GIREBILEN kismin orani
    # (0-1). Payda artik bugunku agirlik degil PERIYOT BASI degeridir: getiri
    # de tam olarak o tabana bolunuyor, dolayisiyla kapsam da ayni buyuklukten
    # okunmali. Seri taban gunune ulasmayan fonlar disarida kalir ve paydaya
    # (en iyi vekil olarak) bugunku degerleriyle sayilir.
    coverage: dict[str, float]
    target_monthly_return: float
    as_of: date
    # Her periyodun GERCEKTE olctugu pencere: (taban gunu, bitis gunu). Etiket
    # "Aylık" tek basina "ay basindan bu yana" diye okunabiliyordu; olculen sey
    # ise kayan 1 aylik penceredir. Pencereyi yazmak bu belirsizligi bitirir.
    windows: dict[str, tuple[date, date]] = field(default_factory=dict)
    # Analizi ureten ham fiyat serileri. Takvim ayi karnesi fon bazli ay
    # getirilerini bunlardan cikarir; ayni veriyi ikinci kez cekmeyelim diye
    # sonucla birlikte tasiniyor.
    histories: dict[str, FundHistory] = field(default_factory=dict)
    # Analizi ureten portfoy. Karnenin "gerceklesen" kolu, dis para akisini
    # islem kayitlarindaki gercek fiyattan hesaplamak icin buna bakar - satis
    # fiyati tahmin edilirse fark dogrudan sahte getiri olur (bkz.
    # snapshots.sub_period_returns). Rapor satirlarina girmeyen KAPANMIS
    # pozisyonlar da burada durur.
    portfolio: Portfolio | None = None
    # Pencere icinde TAMAMEN SATILAN fonlarin satirlari. `rows`tan ayri
    # duruyorlar cunku ikisi farkli sorulari cevapliyor:
    #
    #   rows        -> "su an neye sahibim" (tablo, toplam deger, agirlik)
    #   closed_rows -> "bu pencerede ne yasadim" (getiri, TL degisim)
    #
    # Kapanmis fonu `rows`a koymak 0 adetlik hayalet bir holding satiri
    # uretirdi; getiriden dislamak ise hayatta kalanlarin sonucunu raporlamak
    # (survivorship bias) olurdu. Ayri liste her ikisinden de kaciniyor.
    closed_rows: list[FundRow] = field(default_factory=list)
    # Periyot -> getiri hesabina giremeyen fon kodlari (serisi taban gunune
    # ulasmiyor). `coverage` bunun ORANINI verir, bu alan ADINI: kapanmis bir
    # fonun kaybi sessizce yutulduğunda hangi fon oldugunu soyleyebilmek icin.
    uncovered: dict[str, list[str]] = field(default_factory=dict)

    def uncovered_codes(self, period: str) -> list[str]:
        """Periyodun getiri hesabina giremeyen fonlari."""
        return self.uncovered.get(period, [])

    @property
    def all_rows(self) -> list[FundRow]:
        """Getiri hesabinin kapsami: acik + pencere icinde kapanmis fonlar."""
        return self.rows + self.closed_rows

    def window(self, period: str) -> tuple[date, date] | None:
        return self.windows.get(period)

    def window_label(self, period: str) -> str:
        """'Son 1 ay · 27.07 → 27.08' - periyodun adi ve olctugu aralik."""
        name = config.PERIOD_WINDOW_LABELS.get(
            period, config.PERIOD_LABELS.get(period, period)
        )
        bounds = self.windows.get(period)
        if bounds is None:
            return name
        start, end = bounds
        return f"{name} · {start:%d.%m} → {end:%d.%m}"

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
        """Periyot icinde portfoyun urettigi TL kazanc/kayip.

        ACIK + KAPANMIS butun fonlarin akis duzeltmeli TL degisimlerinin
        toplamidir (bkz. `_flow_adjusted_change`). Kapanmislari da saymak sart:
        pencere icinde tamami satilan fon bugun tabloda yok ama o gunku fiyat
        hareketini kullanici GERCEKTEN yasadi. Yalnizca `rows` uzerinden
        toplandiginda PHE'nin 03.09'daki -%12,75'i (-28.820,61 TL) kayboluyor,
        portfoy 1.570 TL kaybederken ekranda +27.250 TL kar goruntusu cikiyordu.

        Getirisi hesaplanamayan fonlar disarida kalir, yani agirlikli
        ortalamayla ayni kapsami kullanir.
        """
        changes = [
            change
            for row in self.all_rows
            if (change := row.value_change(period)) is not None
        ]
        return sum(changes) if changes else None

    # ----------------------------------------------------------------------
    # Dis para akisi
    # ----------------------------------------------------------------------
    # NAKIT BAKIYESI BILEREK MODELLENMIYOR. Kullanicinin gecmis para giris ve
    # cikislari hicbir yerde kayitli degil; yalnizca fon islemlerinden bakiye
    # turetmeye kalkinca sonuc eksiye (-303 bin TL) dusuyor ve butun oranlar
    # anlamsizlasiyor. Onun yerine akis GORUNUR kilinir: getiri akistan
    # arindirilmis hesaplanir (bkz. `_flow_adjusted_change`), akisin kendisi de
    # ayri bir not olarak yazilir. Boylece "toplam deger neden dustu" sorusu
    # getiriyi bozmadan cevaplanmis olur.
    def _period_flows(self, period: str) -> list[tuple[str, float]]:
        """Pencere icindeki fon bazli net akis: (kod, TL). + alis, − satis.

        Pencere `(taban gunu, bitis gunu]` - yari acik. Taban GUNUNDEKI islem
        zaten taban degerin icinde (taban, o gunun SONUNDAKI adetle olculur);
        akisa da yazmak ayni parayi iki kez saymak olurdu.

        Fiyat once lotun kendi kaydindan alinir - satis hasilatini dogru
        veren tek kayit odur. Lotta fiyat yoksa TEFAS'in o gunku fiyatina
        dusulur; ikisi de yoksa lot atlanir, cunku fiyatsiz bir akisi sifir
        saymak "para hic hareket etmedi" demek olurdu.

        Fiyata dusme, lotun gunu fonun KENDI serisinin sonunu asmadigi surece
        yapilir. `_price_asof` `allow_last=True` ile hep bir sey dondurur;
        seri lotun gununden once bitmisse dondurdugu sey lotun gunune AIT
        DEGILDIR ve sessizce bayat bir fiyatla akis uydurmus oluruz. Pencerenin
        bitis gunu en uzun seriden okundugu icin bu, kendi serisini erken
        bitiren (o gun fiyat aciklamamis) fonlarda gercekten olabilir. Sinir
        icinde kalindiginda donen fiyat, tatil/hafta sonu icin zaten dogru
        degerleme gunune dusen fiyattir - `_flow_adjusted_change` ile ayni
        kural.
        """
        bounds = self.windows.get(period)
        if bounds is None or self.portfolio is None:
            return []
        base_day, end_day = bounds

        flows: list[tuple[str, float]] = []
        for code, position in sorted(self.portfolio.positions.items()):
            total = 0.0
            found = False
            for lot in position.lots:
                if lot.date is None or not (base_day < lot.date <= end_day):
                    continue
                price = lot.price
                if price is None:
                    history = self.histories.get(code)
                    lot_ts = pd.Timestamp(lot.date)
                    price = (
                        _price_asof(history.prices, lot_ts, allow_last=True)
                        if history is not None and lot_ts <= history.prices.index[-1]
                        else None
                    )
                if price is None:
                    continue
                total += lot.units * price
                found = True
            # Kurus altini gurultu sayip atiyoruz: yuvarlama artigi "akis var"
            # diye bir not dogurmasin.
            if found and abs(total) >= 0.005:
                flows.append((code, total))
        return flows

    def net_flow(self, period: str) -> float | None:
        """Periyottaki net dis para akisi (TL). + giris (alis), − cikis (satis).

        Hicbir islem yoksa None - 0,00 yazmakla "akis yok" demek arasindaki
        fark, arayuzde bos bir satir gostermemek icin onemli.
        """
        flows = self._period_flows(period)
        return sum(amount for _code, amount in flows) if flows else None

    def flow_note(self, period: str) -> str | None:
        """'197.279,36 ₺ çıkış (PHE satışı)' - akisin yonu, tutari ve kaynagi.

        Toplam degerin neden dustugunu/ciktigini tek satirda anlatir. Getiriyle
        KARISTIRILMAMASI icin ayri bir cumle olarak yaziliyor: 197 bin TL'lik
        cikis bir kayip degil, portfoyden nakde donen paradir.
        """
        flows = self._period_flows(period)
        if not flows:
            return None
        net = sum(amount for _code, amount in flows)

        girisler = [code for code, amount in flows if amount > 0]
        cikislar = [code for code, amount in flows if amount < 0]
        parcalar = []
        if cikislar:
            parcalar.append(f"{', '.join(cikislar)} satışı")
        if girisler:
            parcalar.append(f"{', '.join(girisler)} alışı")

        yon = "çıkış" if net < 0 else "giriş"
        # Hem alis hem satis varsa rakam ikisinin NETI; "net" demeden yazmak
        # tek bir islem olmus izlenimi verirdi.
        onek = "net " if (girisler and cikislar) else ""
        return f"{onek}{fmt_money(abs(net))} {yon} ({', '.join(parcalar)})"

    # ----------------------------------------------------------------------
    # Kapanmis pozisyonlarin tablo ozeti
    # ----------------------------------------------------------------------
    # Kapanmis satir fon tablosuna HOLDING olarak eklenmez: adedi 0'dir ve
    # tablonun cevapladigi soru "elimde ne var"dir; 0 adetlik bir satir o soruyu
    # bulandirir. Ama tablodan tamamen dislamak da olmaz - o zaman TL kolonunu
    # toplayan kullanici ozetteki rakama varamaz (gercek veride kolon +27.250,43
    # derken ozet -1.570,18 diyordu). Cozum: govdenin ALTINDA, gorsel olarak
    # ayrilmis tek bir ozet satiri. Asagidaki uc yardimci o satirin icerigini
    # tek yerden uretir; konsol ve web ayni rakami gostersin diye.
    def closed_value_change(self, period: str) -> float | None:
        """Kapanmis pozisyonlarin toplam TL degisimi."""
        changes = [
            change
            for row in self.closed_rows
            if (change := row.value_change(period)) is not None
        ]
        return sum(changes) if changes else None

    def closed_contribution(self, period: str) -> float | None:
        """Kapanmis pozisyonlarin toplam katkisi (yuzde puan)."""
        parts = [
            value
            for row in self.closed_rows
            if (value := row.contributions.get(period)) is not None
        ]
        return sum(parts) if parts else None

    @property
    def closed_label(self) -> str | None:
        """'PHE (03.09)' - kapanan fonlarin kodu ve kapanis gunu.

        Birden fazlaysa hepsi TEK satirda toplanir; fon basina ayri satir
        acmak, tablonun "elimde ne var" govdesini yeniden bulandirirdi.
        """
        if not self.closed_rows:
            return None
        return ", ".join(
            f"{row.code} ({row.closed_on:%d.%m})" if row.closed_on else row.code
            for row in self.closed_rows
        )

    @property
    def closed_note(self) -> str | None:
        """'PHE 03.09'da kapandı — getiri ve TL değişimine dahil'.

        Kapanmis fon tablodan dustugu icin kullanici onu "hesaba katilmamis"
        sanabiliyor; tam tersini soylemek gerekiyor.
        """
        if not self.closed_rows:
            return None
        parcalar = [
            f"{row.code} {row.closed_on:%d.%m}'da" if row.closed_on else row.code
            for row in self.closed_rows
        ]
        return f"{', '.join(parcalar)} kapandı — getiri ve TL değişimine dahil"

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


def _period_windows(prices: pd.Series) -> dict[str, tuple[date, date]]:
    """Her periyodun gercekte kullandigi (taban gunu, bitis gunu) ciftleri.

    Taban, `compute_returns`in secitigi gunun aynisidir: takvim gunu degil, o
    gune veya oncesine dusen en yakin ISLEM gunu. Yani etiket, hesabin gercekten
    baktigi gunu gosterir - takvimdeki teorik gunu degil.
    """
    if len(prices) < 2:
        return {}

    latest_ts = prices.index[-1]
    windows: dict[str, tuple[date, date]] = {}
    for period, offset in config.PERIODS.items():
        if offset is None:  # "gunluk": bir onceki islem gunu
            base_ts = prices.index[-2]
        else:
            position = prices.index.searchsorted(
                latest_ts - pd.DateOffset(**offset), side="right"
            ) - 1
            if position < 0:
                continue
            base_ts = prices.index[position]
        windows[period] = (base_ts.date(), latest_ts.date())
    return windows


def _units_asof(position, on: date) -> float:
    """`on` gununun SONUNDA elde olan adet.

    Tarihsiz lotlar hep elde sayilir: "ne zaman aldigim kayitli degil" demek,
    "o gun elimde yoktu" demek degil. Tersini varsaymak tarihsiz lotu pencere
    icinde alinmis gibi gosterir, tabani sifirlar ve getiriyi sonsuza iterdi.
    """
    return sum(
        lot.units for lot in position.lots if lot.date is None or lot.date <= on
    )


def _base_proxy(
    position, prices: pd.Series, base_day: date, closed_on: date | None
) -> float:
    """Tabani hesaplanamayan satirin KAPSAM paydasina yazilacak vekil degeri.

    Getiri hesabina GIRMEZ; yalnizca "bu periyodun ne kadarini olcebildik"
    oranini dogru tutar.

    KAPANMIS satirda bu kritik: kapanmis satirin bugunku degeri 0'dir. Vekil
    olarak bugunku degeri yazmak 0 yazmak demektir, kapsam orani 1,0'da kalir
    ve konsoldaki "donem basi degerin %X'i" uyarisi HIC tetiklenmez -
    gerceklesmis bir kayip "0,00 ₺ / %0,00" olarak, ustelik "kapsam tam"
    damgasiyla raporlanir. Sessiz yutulmayi engelleyen sey burasi.

    Vekil de TABAN GUNU parasiyla ifade edilir. Kapsam orani, taban gunu
    TL'siyle bugunku TL'yi ayni kefeye koyarsa iki farkli tarihin parasini
    toplamis olur; aradaki fark tam da olcmeye calistigimiz getiridir.
    """
    if len(prices) == 0:
        return 0.0

    units = _units_asof(position, base_day)
    if units <= 0 and closed_on is not None and closed_on > base_day:
        # Taban gunundeki adet okunamiyor (tarihsiz/eksik lot) ama pozisyon
        # pencere icinde kapanmis: kapanistan bir onceki gunun adedi, "ne kadar
        # para riskteydi" sorusunun eldeki en iyi cevabi.
        units = _units_asof(position, closed_on - timedelta(days=1))
    if units <= 0:
        return 0.0

    # Taban hesaplanamadiysa seri o gune ulasmiyor demektir; elimizdeki en
    # erken kayit taban gunune en yakin bilinen fiyattir.
    price = _price_asof(prices, pd.Timestamp(base_day), allow_last=True)
    if price is None:
        price = float(prices.iloc[0])
    return units * price


def _trading_calendar(histories: dict[str, FundHistory], codes) -> pd.DatetimeIndex:
    """Verilen fonlarin fiyat gunlerinin BIRLESIMI - periyot pencerelerinin ekseni.

    Eskiden pencere "en UZUN seri"den okunuyordu. Uzunluk tazelik demek degil:
    yayin yapmayi birakmis (ya da kapanmis) bir fonun serisi en uzun olabilir ve
    pencereyi bayat bir tarihe ceker - baslikta 03.09 yazarken hesabin 22.08
    penceresini olctugu durum budur. Birlesimde son gun, fonlarin EN GUNCELI
    tarafindan belirlenir; ayrica bir fonun atladigi gunu digerleri doldurur.
    """
    days: set = set()
    for code in codes:
        history = histories.get(code)
        if history is not None:
            days.update(history.prices.index)
    return pd.DatetimeIndex(sorted(days))


def _flow_adjusted_change(
    position, prices: pd.Series, base_day: date, end_day: date
) -> tuple[float, float] | None:
    """(taban deger, TL degisim) - lot bazli zaman-agirlikli atif.

    NEDEN BU HESAP: portfoy getirisini "bugunku deger / pencere basindaki
    deger" diye olcmek, pencere icinde alim veya satim yapildiginda coker.
    Satistan cikan nakit portfoyden ayrilir ve deger duser; bunu getiri saymak
    yanlis, hic saymamak da yanlis - o fon satilana kadar gercek bir fiyat
    hareketi yasadi.

    NEDEN NAKIT MODELLENMEDI: dogru cozum satis hasilatini nakit olarak
    portfoyde tutmakti. Kullanicinin gecmis para giris/cikislari kayitli
    olmadigi icin turetilen bakiye eksiye (-303 bin TL) dusuyor; negatif nakitle
    hesaplanan agirlik ve getiri, duzeltmeye calistigimiz sapmadan daha
    buyugunu uretirdi. Onun yerine akisi hesabin DISINDA birakip getiriyi
    gunluk fiyat hareketlerinden topluyoruz.

    Hesap:

        TL degisim = Σ  adet_as_of(d-1) x (P(d) - P(d-1))
                     d ∈ (taban gunu, bitis gunu] araligindaki islem gunleri
                     d-1 = SERIDE d'den bir onceki islem gunu (takvim gunu degil)
        taban      = adet_as_of(taban gunu) x P(taban gunu)

    Alim gunu: adet o gunun BASINDA henuz artmamistir, o gunun hareketi
    getiriye girmez - dogrusu bu, alis zaten o gunun fiyatindan yapilir.
    Satis gunu: adet gun basinda hala eldedir, o gunun hareketi getiriye GIRER -
    satis da o gunun fiyatindan yapilir. PHE'nin 03.09'daki -%12,75'i tam olarak
    bu kural sayesinde -28.820,61 TL olarak hesaba giriyor.

    `lot.price`a HIC dokunulmaz. Portfoydeki bazi lotlar fiyatsiz (tarih alani
    eklenmeden onceki eski kayitlar); fiyat sart kosulsaydi portfoyun buyuk
    kismi hesabin disinda kalirdi. Yalnizca TEFAS fiyat serisi ve adetler
    kullanilir.

    BILINEN SAPMA - bilerek kabul edildi: pencere ICINDE alinan fon tabana
    girmez (o gun elde yoktu) ama kazancini paya katar. Yani payda, paranin
    pencere boyunca ortalama ne kadarini temsil ettiginden kucuk kalir ve oran
    tek yonlu, YUKARI saplanir.

    Buyuklugu gercek portfoyde olculdu (03.09.2026, zincirlenmis gunluk TWR ile
    karsilastirildi):

        gunluk   -%0,0415  vs  -%0,0415   -> 0,00 puan
        haftalik +%1,6028  vs  +%1,6057   -> 0,00 puan
        aylik    +%16,6012 vs  +%16,1354  -> 0,47 puan

    Yani sapma ancak pencere icinde KAYDA DEGER bir alim yapildiginda ortaya
    cikiyor; gunluk rakamda - en sik bakilan rakamda - pratikte sifir.

    Neden yine de bu formul: alternatif (Modified Dietz / zincirlenmis TWR)
    yuzdeyi duzeltir ama TL degisimiyle AYNI KESIRDEN cikmaz. Kod tabaninin
    koruduğu invaryant, arayuzde yan yana duran "%" ve "₺" rakamlarinin
    birbirini tutmasidir; iki ayri hesaptan gelen iki rakam er ya da gec
    celisir ve celiskiyi goren kullanici ikisine birden guvenmez. 0,47 puanlik
    bilinen ve tek yonlu bir sapma, duzeltilen sapmanin (6,5 puan ve gunluk
    rakamda ISARET hatasi) yaninda kabul edilebilir.

    Seri taban gununu kapsamiyorsa None doner; cagiran taraf fonu hem paydan
    hem paydadan cikarir, kapsam oranina ise `_base_proxy` ile yazilir.
    """
    index = prices.index
    start = index.searchsorted(pd.Timestamp(base_day), side="right") - 1
    finish = index.searchsorted(pd.Timestamp(end_day), side="right") - 1
    if start < 0 or finish < start:
        return None  # o tarihte veya oncesinde kayit yok: taban kurulamiyor

    base = _units_asof(position, index[start].date()) * float(prices.iloc[start])

    change = 0.0
    for step in range(start + 1, finish + 1):
        units = _units_asof(position, index[step - 1].date())
        change += units * (float(prices.iloc[step]) - float(prices.iloc[step - 1]))
    return base, change


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

    # --- Kapanmis pozisyonlar ---------------------------------------------
    # Pencerelerden birine dusen bir tarihte tamami satilan fon, rapor
    # TABLOSUNA girmez (elde adet yok, holding degil) ama portfoy getirisine
    # GIRER. `histories` icinde olmasi zaten "yakin zamanda kapanmis" demek:
    # fiyati `Portfolio.codes_for_pricing` sectigi icin cekildi. Cok once
    # kapanmis fonun fiyati hic cekilmez, dolayisiyla buraya da girmez.
    closed_rows: list[FundRow] = []
    for code in portfolio.closed_codes:
        history = histories.get(code)
        if history is None:
            continue
        position = portfolio.positions[code]
        # `acquired_on` / `average_cost` bilerek verilmiyor: elde kalan lot
        # olmadigi icin ikisi de None doner ve kirpma zaten calismaz. Buradaki
        # yuzde, fonun kendi fiyat getirisidir - satirdaki asil rakam
        # `period_changes`teki akis duzeltmeli TL degisimidir.
        returns, partial = compute_returns(history)
        closed_rows.append(
            FundRow(
                code=code,
                title=history.title,
                units=0.0,
                price=history.latest_price,
                price_date=history.latest_date,
                value=0.0,
                source=history.source,
                returns=returns,
                partial_periods=partial,
                closed_on=position.closed_on,
            )
        )
    # En yeni kapanandan eskiye: kullaniciyi ilgilendiren once bugun kapanandir.
    closed_rows.sort(key=lambda r: r.closed_on or date.min, reverse=True)

    # Toplam deger DEGISMEDI: hala "fonlardaki deger"dir. Kapanmis pozisyonun
    # degeri 0'dir ve satistan cikan nakit modellenmiyor (bkz.
    # `_flow_adjusted_change`); toplamin neden dustugunu `flow_note` anlatir.
    total_value = sum(row.value for row in rows)

    # Agirlik = fonun degeri / portfoyun toplam degeri.
    # Katki BURADA hesaplanmaz: paydasi periyot bazli taban toplamidir ve o
    # toplam ancak pencereler kurulduktan sonra bilinir (bkz. asagidaki
    # "Katki" bloku).
    for row in rows:
        row.weight = (row.value / total_value) if total_value > 0 else 0.0

    # En degerliden en dusuge sirala - tablo ve grafikler ayni sirayi kullanir.
    rows.sort(key=lambda r: r.value, reverse=True)

    # Pencereler ACIK pozisyonlarin fiyat gunlerinin birlesiminden kurulur.
    # Kapanmislar takvime KATILMAZ: yayindan cekilmis bir fonun serisi pencereyi
    # gecmise ceker, oysa olculmek istenen bugun elde tutulanin penceresidir.
    # Acik pozisyon yoksa (her sey satilmis) kapanmislara duseriz, yoksa hic
    # pencere kurulamazdi.
    takvim = _trading_calendar(histories, [row.code for row in rows])
    if len(takvim) < 2:
        takvim = _trading_calendar(histories, [row.code for row in closed_rows])
    # `_period_windows` yalnizca indekse bakar; degerler onemsiz.
    windows = (
        _period_windows(pd.Series(range(len(takvim)), index=takvim, dtype=float))
        if len(takvim) >= 2
        else {}
    )

    # Pencerelerin hicbirine dokunmayan kapanislari dusur. Katkilari zaten
    # sifir (taban gunundeki adet 0, pencere boyunca da 0), ama listede
    # kalirlarsa `closed_note` ve JSON API aylar oncesinin kapanisini "getiriye
    # dahil" diye duyurur - dogru ama alakasiz bir gurultu.
    if windows:
        en_erken_taban = min(taban for taban, _bitis in windows.values())
        closed_rows = [
            row
            for row in closed_rows
            if row.closed_on is not None and row.closed_on > en_erken_taban
        ]

    all_rows = rows + closed_rows

    # Her fonun her periyot icin (taban, TL degisim) cifti.
    for row in all_rows:
        history = histories.get(row.code)
        position = portfolio.positions.get(row.code)
        for period, (base_day, end_day) in windows.items():
            result = (
                None
                if history is None or position is None
                else _flow_adjusted_change(position, history.prices, base_day, end_day)
            )
            base, change = (None, None) if result is None else result
            row.period_bases[period] = base
            row.period_changes[period] = change

            # Pencerede tarihli lot var mi: "%" ile "₺"nin ayrisabilecegi
            # hucreler tam olarak bunlar. Fiyati olmayan lot da sayilir -
            # isaretin sorusu "islem oldu mu", "kac paraydi" degil.
            if position is not None and any(
                lot.date is not None and base_day < lot.date <= end_day
                for lot in position.lots
            ):
                row.flow_periods.add(period)

    weighted_returns: dict[str, float | None] = {}
    coverage: dict[str, float] = {}
    # Periyot -> Σ taban. Katkinin PAYDASI; getirininkiyle ayni olmasi sart.
    taban_toplami: dict[str, float] = {}
    # Periyot -> hesaba giremeyen fon kodlari. Kapsam oraninin yaninda ADI da
    # gorunsun: "%88" tek basina hangi fonun eksik oldugunu soylemiyor.
    uncovered: dict[str, list[str]] = {}
    for period in config.PERIODS:
        # PAYDA BASLANGIC degeridir, bugunku deger degil - ve kapsam ACIK +
        # KAPANMIS butun fonlardir.
        #
        # Portfoy getirisi tanim geregi (bitis - baslangic) / baslangic'tir.
        # Bugunku agirlikla (V_bitis / toplam) carpmak kazanan fonu fazla,
        # kaybedeni az sayar - cunku kazanan tam da o getiriyi kazandigi icin
        # buyumustur. Sonuc SISTEMATIK olarak yukari sapar ve fonlar ne kadar
        # ayrisirsa sapma o kadar buyur; gercek portfoyde gunluk rakamin
        # ISARETINI bile ters cevirdigi gorulду (+%0,02 raporlanirken portfoy
        # 2.866 TL kaybetmisti).
        #
        # Ayni sapmanin ikinci ve daha buyuk kaynagi kapsamdi: pencere icinde
        # tamami satilan fon `rows`ta olmadigi icin hem paydan hem paydadan
        # dusuyordu. 03.09'da PHE tamamen satildiginda gunluk getiri +%0,77
        # (+27.250 TL) raporlandi; gercek rakam -%0,04 (-1.570 TL) idi.
        #
        # Bu yuzden hem pay hem payda tek bir yerden, akis duzeltmeli lot bazli
        # hesaptan gelir (bkz. `_flow_adjusted_change`):
        #
        #     yuzde = Σ TL degisim / Σ taban x 100
        #     TL    = Σ TL degisim
        #
        # Pay ve payda ayni hesaptan ciktigi icin arayuzde yan yana
        # gosterildiklerinde celismeleri MUMKUN DEGIL - bu bir tercih degil,
        # korunmasi gereken bir invaryant.
        base_day = windows[period][0] if period in windows else None
        baslangic = 0.0
        degisim = 0.0
        eksik = 0.0
        for row in all_rows:
            taban = row.period_base(period)
            fark = row.value_change(period)
            if taban is None or fark is None:
                # Serisi taban gunune ulasmayan fon: getiri hesabina giremez.
                # Kapsam paydasina yine de TABAN GUNU parasiyla bir vekil
                # deger yazilir (bkz. `_base_proxy`) - kapanmis satirin
                # bugunku degeri 0 oldugu icin 0 yazmak, eksigi hic olmamis
                # gibi gostererek kapsami sahte bicimde 1,0'da tutardi.
                history = histories.get(row.code)
                position = portfolio.positions.get(row.code)
                if history is not None and position is not None and base_day:
                    eksik += _base_proxy(
                        position, history.prices, base_day, row.closed_on
                    )
                uncovered.setdefault(period, []).append(row.code)
                continue
            baslangic += taban
            degisim += fark

        taban_toplami[period] = baslangic
        toplam_taban = baslangic + eksik
        coverage[period] = (baslangic / toplam_taban) if toplam_taban > 0 else 0.0

        if baslangic <= 0:
            # Pencerenin tamami akistan ibaret (butun fonlar pencere icinde
            # alinmis): taban yok, oran tanimsiz. TL degisimi yine de
            # raporlanir; `value_change` ondan bagimsiz calisir.
            weighted_returns[period] = None
            continue
        # Verisi eksik fonlar disarida kalir; kalan kisim kendi icinde
        # normalize olur, cunku hem pay hem payda yalnizca onlardan gelir.
        weighted_returns[period] = degisim / baslangic * 100.0

    # --- Katki (yuzde puan) -------------------------------------------------
    # Katki = fonun TL degisimi / portfoyun TABAN degeri.
    #
    # "Agirlik x fon getirisi" DEGIL. O hesap iki kez yanlisti: (1) agirlik
    # BUGUNKU degerden geliyordu, oysa getirinin paydasi periyot BASI degeri;
    # (2) yalnizca `rows` uzerinden toplaniyordu, yani kapanmis pozisyonu
    # gormuyordu - kisacasi bu modulun geri kalaninda reddedilen formulun ta
    # kendisiydi. Gercek portfoyde gunluk katki toplami +%0,77 cikarken ozet
    # -%0,04 diyordu (ISARET ters), ve `charts.contribution_chart` bu toplami
    # altyazida "agirlikli getiri" diye basiyordu.
    #
    # Bu tanimla Σ contributions == weighted_returns[period] TAM olarak tutar:
    # ikisi de ayni paylardan ve ayni paydadan cikiyor. Selale grafiginin
    # TOPLAM cubugu artik gercekten portfoyun getirisine iniyor.
    for row in all_rows:
        row.contributions = {
            period: (
                None
                if (fark := row.value_change(period)) is None
                or not taban_toplami.get(period)
                else fark / taban_toplami[period] * 100.0
            )
            for period in config.PERIODS
        }

    as_of = max((row.price_date for row in all_rows), default=date.today())

    return PortfolioAnalysis(
        rows=rows,
        closed_rows=closed_rows,
        failures=failures,
        total_value=total_value,
        weighted_returns=weighted_returns,
        coverage=coverage,
        target_monthly_return=portfolio.target_monthly_return,
        as_of=as_of,
        portfolio=portfolio,
        windows=windows,
        histories=histories,
        uncovered=uncovered,
    )


# --------------------------------------------------------------------------
# Takvim ayi karnesi
# --------------------------------------------------------------------------
@dataclass
class MonthReturn:
    """Bir takvim ayinin portfoy getirisi.

    `source` iki degerden biri:

    - `gerceklesen`: o ay boyunca kayitli anlik goruntulerden hesaplandi. Dis
      para akisindan arindirilmis GERCEK portfoy getirisidir.
    - `temsili`: o ay icin portfoy kaydi yok; fonlarin gercek ay getirileri
      BUGUNKU agirliklarla toplandi. Portfoy o ay bu bilesimde olmayabilir,
      dolayisiyla gerceklesmis performans degildir - geriye donuk bir
      canlandirmadir. GIPS bu ayrimi zorunlu tutar: temsili/backtest rakam
      gerceklesmis sicil gibi sunulamaz.
    """

    year: int
    month: int
    ret: float  # yuzde
    source: str
    coverage: float = 1.0  # o ay verisi olan agirlik orani (0-1)
    closed: bool = True  # ay kapandi mi

    @property
    def is_actual(self) -> bool:
        return self.source == "gerceklesen"

    @property
    def label(self) -> str:
        return f"{config.AY_ADLARI[self.month - 1]} {self.year}"

    @property
    def short_label(self) -> str:
        return f"{config.AY_ADLARI[self.month - 1][:3]} {str(self.year)[2:]}"


def _fund_month_returns(prices: pd.Series) -> dict[tuple[int, int], float]:
    """Fonun takvim ayi getirileri (oran): ay sonu / onceki ay sonu - 1.

    Ay basi degil ay SONU fiyatlari kullanilir; bir ayin getirisi onceki ayin
    son islem gununden kendi son islem gunune olan degisimdir. Serideki ilk ay
    tabanini kaybettigi icin raporlanamaz.
    """
    monthly = prices.groupby(prices.index.to_period("M")).last()
    returns: dict[tuple[int, int], float] = {}
    for previous, current in zip(monthly.index, monthly.index[1:]):
        # Seride ay atlanmissa (fon o ay hic fiyat aciklamamis) taban gecersiz.
        if (current - previous).n != 1:
            continue
        base = float(monthly[previous])
        if base <= 0:
            continue
        returns[(current.year, current.month)] = float(monthly[current]) / base - 1.0
    return returns


def _realized_months(
    snapshots: list, wanted: set[tuple[int, int]], portfolio=None
) -> dict[tuple[int, int], float]:
    """Anlik goruntulerden ay bazli zaman agirlikli getiri (oran).

    Ay icindeki dilim getirileri bilesikle zincirlenir; dilimler zaten dis para
    akisindan arindirilmistir (bkz. snapshots.sub_period_returns).
    """
    from .snapshots import sub_period_returns

    chained: dict[tuple[int, int], float] = {}
    for period in sub_period_returns(snapshots, portfolio):
        key = (period.end.year, period.end.month)
        if key not in wanted:
            continue
        chained[key] = (1.0 + chained.get(key, 0.0)) * (1.0 + period.ret) - 1.0
    return chained


def monthly_track_record(
    analysis: PortfolioAnalysis,
    snapshots: list | None = None,
    months: int = config.TRACK_RECORD_MONTHS,
) -> list[MonthReturn]:
    """Portfoyun takvim ayi karnesi, en eskiden yeniye.

    Kapanmis aylar once gelir; icinde bulunulan ay varsa en sona `closed=False`
    ile eklenir. Her ay icin once gerceklesen (anlik goruntu) kolu denenir,
    yoksa temsili (bugunku agirliklarla geriye donuk) kola dusulur.
    """
    if not analysis.rows:
        return []

    from .snapshots import covered_months

    snapshots = snapshots or []
    realized_keys = covered_months(snapshots)
    realized = _realized_months(snapshots, realized_keys, analysis.portfolio)

    # Temsili kol: fon bazli gercek ay getirileri x bugunku agirliklar.
    per_fund = {
        row.code: _fund_month_returns(analysis.histories[row.code].prices)
        for row in analysis.rows
        if row.code in analysis.histories
    }
    weights = {row.code: row.weight for row in analysis.rows}

    current_key = (analysis.as_of.year, analysis.as_of.month)
    keys = sorted({key for table in per_fund.values() for key in table} | realized_keys)

    record: list[MonthReturn] = []
    for key in keys:
        if key in realized:
            record.append(
                MonthReturn(
                    year=key[0], month=key[1], ret=realized[key] * 100.0,
                    source="gerceklesen", coverage=1.0, closed=key != current_key,
                )
            )
            continue

        # Verisi olan fonlarin agirligina gore normalize et; eksik fonu sifir
        # getirili saymak ayi oldugundan sonuk gosterirdi.
        covered = [code for code, table in per_fund.items() if key in table]
        covered_weight = sum(weights.get(code, 0.0) for code in covered)
        if covered_weight <= 0:
            continue
        weighted = sum(weights[code] * per_fund[code][key] for code in covered)
        record.append(
            MonthReturn(
                year=key[0], month=key[1], ret=weighted / covered_weight * 100.0,
                source="temsili", coverage=covered_weight, closed=key != current_key,
            )
        )

    closed = [item for item in record if item.closed][-months:]
    running = [item for item in record if not item.closed]
    return closed + running


def to_dataframe(analysis: PortfolioAnalysis) -> pd.DataFrame:
    """Analizi Excel/CSV'ye uygun bir DataFrame'e cevirir.

    KAPANMIS satirlar da yazilir (adet 0, deger 0, notta "KAPANDI gg.aa").
    Tablo `rows`, ozet blogu ise `all_rows` uzerinden hesaplaniyordu: "Günlük
    Değişim (₺)" kolonunu toplayan kullanici +27.250,43 buluyor, iki satir
    yukaridaki ozet -1.570,18 diyordu. Ayni sayfada birbirini tutmayan iki
    rakam, ikisine birden guveni bitirir. Adet ve deger 0 oldugu icin satirin
    eklenmesi "Toplam Değer" kolonunu degistirmez.
    """
    records = []
    for row in analysis.all_rows:
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
                # Sayisal hucreye yildiz konamaz; kirpilmis periyot ve kapanis
                # burada yazar.
                # Sayisal hucreye isaret konamaz; hepsi bu metin kolonunda.
                "Not": " · ".join(
                    parca
                    for parca in (
                        f"KAPANDI {row.closed_on:%d.%m}" if row.closed_on else "",
                        row.partial_note or "",
                        (
                            f"{config.FLOW_MARK} dönem içi alım/satım: "
                            + ", ".join(
                                config.PERIOD_LABELS[period].lower()
                                for period in config.PERIODS
                                if row.has_flow(period)
                            )
                            if row.flow_periods
                            else ""
                        ),
                    )
                    if parca
                ),
            }
        )
    return pd.DataFrame.from_records(records)
