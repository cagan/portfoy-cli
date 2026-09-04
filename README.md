# TEFAS Portföy Takip CLI

TEFAS'tan fon fiyatlarını çeker, portföyünüzün **ağırlıklı ortalama** getirisini
hesaplar, terminalde renkli tablo basar, Excel raporu ve grafikleri üretir.

## Kurulum

```bash
git clone https://github.com/cagan/portfoy-cli.git
cd portfoy-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### `portfoy` komutunu her yerden çalıştırmak

```bash
pip install -e . --no-deps                     # .venv/bin/portfoy üretir
ln -s "$PWD/.venv/bin/portfoy" ~/.local/bin/   # PATH'e bağla
```

Artık venv'i aktive etmeden, bilgisayarın herhangi bir dizininden `portfoy`
yazabilirsiniz — komutun shebang'i doğrudan `.venv/bin/python`'a işaret eder.
`~/.local/bin` PATH'inizde değilse `.zshrc`'ye ekleyin:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Kurulum **editable** (`-e`) olduğu için kodda yaptığınız değişiklik anında
geçerli olur; yeniden kurmanız gerekmez. Portföy dosyası ve çıktı klasörü
`portfoy/config.py`'nin konumundan türer, yani komutu nereden çalıştırırsanız
çalıştırın hep aynı `veri/portfoy.json` okunur ve raporlar hep depodaki
`ciktilar/` klasörüne yazılır.

Aşağıdaki örneklerde `portfoy` yerine `python main.py` de yazabilirsiniz;
ikisi aynı programı çalıştırır.

## Hızlı başlangıç

```bash
# 1) Fonlarınızı alış tarihi ve fiyatıyla birlikte bir kez girin
portfoy add TLY 1500 --date 12.05.2026 --price 21.4
portfoy add DFI  800 --date 03.06.2026 --price 10.9
portfoy add TMV 1200 --date 03.08.2026 --price 8.613999
portfoy add PBR  450 --date 27.06.2026 --price 3.05

# 2) Aylık getiri hedefinizi ayarlayın (varsayılan %12)
portfoy target 12

# 3) Güncel durumu görün
portfoy status

# 4) Excel + grafikleri üretin
portfoy report
```

Bütün komutları gruplanmış halde görmek için `portfoy help` (argümansız
çalıştırmak da aynı ekranı basar).

`--date` **önemlidir**: bu tarih olmadan uygulama fonun periyodun tamamında
elinizde olduğunu varsayar ve daha dün aldığınız bir fonu 1 aydır sizdeymiş gibi
aylık getiriye katar. `--price` verirseniz gerçek maliyet ve kar/zarar da
hesaplanır. Ayrıntı: [Alış tarihi ve kısmi periyot](#alış-tarihi-ve-kısmi-periyot).

Portföy `veri/portfoy.json` dosyasında saklanır — her çalıştırmada yeniden
girmeniz gerekmez. Her fon bir **işlem listesi** olarak tutulur; elde kalan adet
bu işlemlerin toplamıdır:

```json
{
  "version": 2,
  "hedef_aylik_getiri": 12.0,
  "fonlar": {
    "TMV": {
      "islemler": [
        { "tarih": "2026-08-03", "adet": 40631, "fiyat": 8.613999 }
      ]
    }
  }
}
```

Eski `"TMV": 40631` biçimi de okunmaya devam eder; o fonlar "alış tarihi
bilinmiyor" sayılır ve `list` çıktısında uyarı verirler.

Her yazma öncesi dosyanın önceki hali `veri/portfoy.json.bak` olarak saklanır;
yanlış bir adet girerseniz bu dosyadan geri alabilirsiniz:

```bash
cp veri/portfoy.json.bak veri/portfoy.json
```

## Komutlar

Komut adları ve bayraklar İngilizce, çıktılar Türkçedir.

| Komut | Ne yapar |
|---|---|
| `add CODE UNITS` | Fonu ekler / adedini **değiştirir** (üzerine yazarsa uyarır) |
| `add ... --date GG.AA.YYYY` | Alış tarihi — periyot getirisi bu tarihten itibaren sayılır |
| `add ... --price FİYAT` | Alış birim fiyatı — maliyet ve gerçek kar/zarar hesaplanır |
| `add CODE UNITS --accumulate` | Mevcut adedin **üzerine ekler** (negatif değer düşer) |
| `add CODE -UNITS --accumulate --date ... --price ...` | **Gerçekleşmiş** satışı kaydeder; tamamı satılırsa pozisyon kapanır |
| `emir CODE UNITS --sat --tarih ... --saat ...` | **Satış emri** kaydeder; işlem gününü emir saatinden türetir |
| `emir CODE --al --tutar TL --tarih ... --saat ...` | **Alış emri**; adet, işlem günü fiyatı yayımlanınca hesaplanır |
| `bekleyen` | Gerçekleşmeyi bekleyen emirleri gösterir |
| `valor [CODE]` | Fon valör kurallarını gösterir/düzenler |
| `remove CODE` | Fonu portföyden **siler** (işlem geçmişi de gider) |
| `list` | Kayıtlı fonları gösterir (TEFAS'a bağlanmadan) |
| `lots [CODE]` | Kayıtlı alış/satış işlemlerini gösterir |
| `target PERCENT` | Aylık getiri hedefini ayarlar |
| `status` | Güncel tabloyu terminalde gösterir |
| `web` | **Web arayüzünü başlatır** (varsayılan: http://127.0.0.1:8000) |
| `report` | Tablo + Excel + grafikleri üretir **ve e-posta gönderir** |
| `mail-setup` | E-posta gönderimini yapılandırır |
| `mail-test` | Deneme e-postası gönderir (gerçek rapordaki eklerle birlikte) |
| `clear-cache` | Yerel veri önbelleğini siler |
| `help` | Bütün komutları gruplanmış halde gösterir |

Faydalı bayraklar:

```bash
portfoy report --show             # grafikleri ekranda da aç
portfoy report --theme dark       # koyu tema grafikler
portfoy report --period aylik     # sadece aylık katkı/değişim grafiği
portfoy report --no-excel         # sadece grafik
portfoy report --months 6         # karnede 6 kapanmış ay göster (varsayılan 3)
portfoy report --no-track         # takvim ayı karnesini atla
portfoy report --no-snapshot      # bu çalıştırmayı geçmişe yazma
portfoy status --no-cache         # önbelleği atla, taze veri çek
portfoy status --verbose          # hata ayıklama günlüğü
portfoy --data-file ~/alt.json status  # ikinci bir portföy
```

`--period` birden fazla değer alır; varsayılan `haftalik aylik`, yani her
çalıştırmada iki katkı grafiği ve iki kartlı değişim grafiği üretilir.

`--period` değerleri (`gunluk` / `haftalik` / `aylik`) bilerek Türkçe kaldı:
bunlar hesaplama katmanının periyot anahtarlarıdır, Excel ve grafiklerde de aynı
adlarla geçer.

## E-posta

`report` komutu dosyaları diske yazdıktan sonra aynı değerleri e-posta olarak da
gönderir. Alıcı belirtmezseniz rapor **gönderen hesabın kendi adresine** gider.

### Kurulum (tek seferlik)

Gmail için **normal hesap şifresi çalışmaz.** İki adımlı doğrulamayı açıp
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
adresinden 16 haneli bir **Uygulama Şifresi** üretin, sonra:

```bash
portfoy mail-setup --user kendi.adresiniz@gmail.com
# şifre gizli sorulur (ekranda görünmez)

portfoy mail-test     # ayarları doğrula
```

Birden çok alıcı için `--to` bayrağını tekrarlayın. Diğer seçenekler: `--host`,
`--port`, `--no-password-prompt`.

Şifre `keyring` kuruluysa **macOS Anahtar Zinciri'ne** yazılır, koda veya
depoya asla girmez. Anahtar zinciri yoksa `veri/mail.json` dosyasına yalnızca
sizin okuyabileceğiniz izinlerle (`600`) düşer.

Ortam değişkeni tercih ederseniz (bunlar ayar dosyasını ezer):

```bash
export PORTFOY_SMTP_USER=kendi.adresiniz@gmail.com
export PORTFOY_SMTP_PASSWORD=uygulama-sifresi
export PORTFOY_MAIL_TO=alici@ornek.com         # virgülle birden çok alıcı
export PORTFOY_SMTP_HOST=smtp.gmail.com        # opsiyonel
export PORTFOY_SMTP_PORT=465                   # 465=SSL, 587=STARTTLS
```

### Gelen e-postada ne var

- Hedef durumunu gösteren renkli şerit (tuttu / altında)
- Fon tablosu: adet, fiyat, değer, ağırlık, günlük getiri **ve günlük TL değişimi**,
  haftalık/aylık getiri, katkı
- Özet: toplam değer, **önceki güne göre değişim**, ağırlıklı ortalama getiriler,
  hedefe fark
- Grafikler **gövdeye gömülü** (ek açmadan telefonda görünür)
- Excel dosyası **ek** olarak
- Düz metin alternatifi (HTML göstermeyen istemciler için)

### Kontrol

```bash
portfoy report --no-email   # bu çalıştırmada gönderme
portfoy report --email      # ayar eksikse sebebini yazdır
```

Ayar yapılmamışsa `report` sessizce e-postayı atlar; raporlar yine üretilir.
Gönderim başarısız olursa sebep yazdırılır ama diske yazılan dosyalar korunur.

## Çıktılar

`ciktilar/` klasörüne yazılır:

- `portfoy_raporu_YYYYAAGG.xlsx` — Tarih, Fon Kodu, Fon Adı, **Alış Tarihi**, Adet,
  Güncel Fiyat, Toplam Değer, **Toplam Maliyet**, **Kar/Zarar (₺ ve %)**,
  Portföy Ağırlığı (%), Günlük Getiri (%), **Günlük Değişim (₺)**,
  Haftalık/Aylık Getiri (%), Aylık Katkı ve **Not** (kısmi periyot açıklaması).
  En altta **Toplam Portföy Değeri**, **Önceki Güne Göre Değişim** ve
  **Ağırlıklı Ortalama Aylık Getiri** özeti, hedefe fark ve durum satırı yer alır.
  Periyodun tamamında portföyde olmayan fonlar ayrıca `KISMİ PERİYOT` başlığı
  altında listelenir.
  Fon bazlı günlük TL değişimlerinin toplamı, özetteki günlük değişime birebir eşittir.
- `portfoy_dagilimi.png` — hangi fon portföyün yüzde kaçı (pasta grafik)
- `getiri_katkisi_haftalik.png` / `getiri_katkisi_aylik.png` — toplam getiriye fon
  bazında katkı (şelale grafik)
- `portfoy_degisimi.png` — portföyün haftalık ve aylık değişimi; yüzde ve TL
- `aylik_karne.png` — kapanmış takvim aylarının getirisi, hedef çizgisiyle

## Hesaplamalar

- **Ağırlık** = Fonun Toplam Değeri ÷ Portföyün Toplam Değeri
- **Getiri** = (Güncel Fiyat ÷ Geçmiş Fiyat − 1) × 100
  - *Günlük*: bir önceki **işlem günü** (TEFAS hafta sonu fiyat yayınlamaz)
  - *Haftalık*: 7 takvim günü öncesindeki en yakın işlem günü
  - *Aylık*: **1 ay önceki aynı takvim günü** — 30 gün değil. TEFAS'ın
    "Son 1 Ay Getirisi" rakamı bu kuralı kullanır, bu yüzden uygulamanın aylık
    getirisi TEFAS sitesindekiyle birebir aynıdır. Ay sonlarında gün kırpılır
    (31.07 → 30.06). Fark önemsiz değildir: ay başında sıçrama yapan bir fonda
    30 günlük kural TEFAS'tan 4 puana varan sapma verebiliyor.
- **Katkı (yüzde puan)** = Fonun getirisi × Fonun ağırlığı
- **Portföy getirisi** = Σ (Fon getirisi × Fon ağırlığı) — ağırlıklı ortalama
- **TL değişimi** = Güncel Değer − (Güncel Değer ÷ (1 + Getiri)). Yani bugünkü
  değerden dünkü değer çıkarılır; getiriyi doğrudan bugünkü değerle çarpmak
  sonucu şişirirdi (%8,66 aylık getiride ~%8 fazla gösteriyordu).
  Portföyün TL değişimi, fonların TL değişimlerinin **toplamıdır** — böylece
  rapordaki fon satırları özetteki toplamı tutturur. Adedin periyot boyunca sabit
  kaldığı varsayılır; günlükte bu neredeyse her zaman doğrudur, ara dönemde alım
  satım yaptıysanız haftalık/aylık rakam yaklaşık kalır.

Bir fonun geçmiş verisi eksikse o fon ağırlıklı ortalamadan çıkarılır ve kalan
ağırlığa göre normalize edilir; terminalde `[ağırlığın %X'i]` uyarısı belirir.
Böylece eksik veri, getiriyi sessizce aşağı çekmez.

### Kayan pencere mi, ay başından mı?

Periyot getirileri **kayan (rolling) penceredir**, ay başından bu yana değil:
aylık getiri "bir ay önceki aynı takvim gününden bugüne"dir (ör. 27.07 → 27.08).
Rapor, grafik ve e-postada ölçülen pencere açıkça yazılır — `Aylık` etiketi tek
başına "ay başından bu yana" diye okunabiliyordu.

Bu tercih hedef takibi için bilinçlidir: **ölçüm penceresi hedef penceresiyle
aynı uzunlukta olmalıdır.** Hedef "ayda %12" ise, karşılaştırılan rakam da tam
1 aylık bir pencereyi ölçmelidir. Ay başından bu yana (MTD) hesaplansaydı
pencere ayın 1'inde 1 gün, 30'unda 30 gün olurdu; ayın 3'ünde %1,2 getiri
"hedefin %10,8 altında" diye raporlanır, oysa ortada bir sapma yoktur.

Kayan pencerenin bedeli şudur: hiçbir ayı **kapatmaz**. "Ağustos'u şu sonuçla
bitirdim" diyen bir karne oluşmaz ve 30 gün önceki iyi bir gün pencereden
düştüğünde, portföy o gün yükselmiş olsa bile aylık rakam düşebilir. Bu boşluğu
takvim ayı karnesi doldurur.

### Takvim ayı karnesi

`aylik_karne.png` ve e-postadaki karne bloğu, **kapanmış** takvim aylarını
gösterir; hedef çizgisi grafiğin üzerindedir. "%12'yi tutturabiliyor muyum"
sorusunun cevabı budur, çünkü verdikt ancak kapanmış bir dönem için verilebilir.

Özetteki ortalama **bileşiktir**, aritmetik değil: aylık hedef ard arda gelen
ayların çarpımıyla tutturulur, toplamıyla değil.

Her ay iki kaynaktan birine dayanır:

| Kaynak | Ne demek |
|---|---|
| **gerçekleşen** | O ay boyunca kayıtlı portföy görüntülerinden hesaplandı. Dış para akışından arındırılmış gerçek portföy getirisidir. |
| **temsili** (grafikte taramalı, e-postada `†`) | O ay için portföy kaydı yoktu. Fonların **gerçek** ay getirileri **bugünkü** ağırlıklarla toplandı. Portföy o ay bu bileşimde olmayabilir. |

Ayrım şart: temsili rakam geriye dönük bir canlandırmadır, gerçekleşmiş sicil
değildir. GIPS bu ayrımı zorunlu tutar — backtest/temsili performans
gerçekleşmiş performans gibi sunulamaz. Uygulama bu yüzden temsili ayları hem
renkten bağımsız bir dokuyla hem de açık bir dipnotla işaretler.

Karne ilk kurulumda **tamamen temsili** başlar; portföy görüntüleri biriktikçe
aylar kendiliğinden gerçekleşene döner.

**Varsayılan pencere bilerek kısa: 3 kapanmış ay.** Temsili kolda uzun bir
geçmiş, güven verdiği ölçüde yanıltır — portföy o aylarda bu bileşimde
değildiyse rakam gerçek performans değildir. Görüntüler birikip aylar
gerçekleşene döndükçe `--months` ile pencereyi açmak anlamlı hale gelir.

### Portföy geçmişi (`veri/gecmis.json`)

Fiyat geçmişi TEFAS'ta durur ama **portföyün** geçmişi hiçbir yerde durmaz —
elde yalnızca bugünkü pozisyon vardır. Bu yüzden her `report` / `status`
çalıştırması o günün pozisyonunu (fon, adet, fiyat) `veri/gecmis.json` içine
yazar. Aynı gün tekrar çalıştırmak kaydı **günceller**, çoğaltmaz; tarih olarak
bugün değil fiyatın ait olduğu gün (`as_of`) kullanılır.

Getiri neden doğrudan değer farkından hesaplanmıyor: yeni alım yaptığınızda
portföy değeri artar ama bu getiri değildir. İki görüntü arasındaki **adet**
değişimi dış para akışı sayılıp getiriden düşülür — zaman ağırlıklı getirinin
(TWR) tanımı budur. Görüntüler seyrekse (haftada bir gibi) ve arada alım/satım
yapıldıysa o dilim yaklaşık kalır.

Akış, işlem kaydındaki **gerçek fiyattan** hesaplanır; kayıt yoksa adet değişimi
dönem sonu fiyatıyla çarpılır. Fark özellikle tamamı satılan fonlarda büyür:
fon bitiş görüntüsünde bulunmadığı için fiyatı tahmin etmek gerekir ve tahminle
gerçekleşme fiyatı arasındaki fark doğrudan sahte kâr/zarar olarak getiriye
yazılır.

Kaydı istemiyorsanız: `--no-snapshot`.

### Alış tarihi ve kısmi periyot

Bir fonun **TEFAS'taki 1 aylık getirisi** ile **sizin o fondan 1 ayda
kazandığınız** aynı şey değildir — fonu bir hafta önce aldıysanız aradaki üç
haftalık hareket sizin cebinizden geçmemiştir.

Bu yüzden periyot başı, fonun alış tarihinden eskiyse **taban fiyat alış anına
çekilir**:

- Taban olarak önce gerçek alış maliyetiniz (`--price`) kullanılır; yoksa
  TEFAS'ın alış tarihindeki kapanış fiyatına düşülür.
- Böyle hesaplanan hücreler tabloda **yıldızla** işaretlenir (`-%0,80*`), altına
  da `TMV — 7 gündür portföyde` dipnotu düşülür. Aynı işaret Excel'in `Not`
  sütununda, e-postada ve katkı grafiğinde de görünür.
- Bu rakam fonun portföye **gerçek katkısıdır**, dolayısıyla ağırlıklı ortalamaya
  da bu hâliyle girer — özet satırında `[TMV kısmi]` uyarısı belirir.
- Yıldızlı bir değerin TEFAS'ın tam periyot getirisiyle **aynı çıkmaması
  beklenir**; farkın sebebi budur.

Alış tarihi bilinmeyen fonlarda (eski kayıtlar) kırpma yapılmaz: fonu
olduğundan yeni göstermek, olduğundan eski göstermekten daha yanıltıcı olurdu.
`list` bu fonları işaretler.

Bir fonda birden çok alış varsa en eski tarih esas alınır — periyot boyunca fon
zaten elinizdeydi; ara dönemdeki ekleme/azaltmalar TL rakamını yaklaşık bırakır.

## Satış ve kapanmış pozisyonlar

Satış negatif adetle girilir:

```bash
portfoy add PHE -69991 --accumulate --date 02.09.2026 --price 3.230415
```

Satışta **önce en eski alış kapanır** (FIFO) — Türkiye'de fon satışlarında
uygulanan ve aracı kurum ekranlarının da kullandığı sıra budur. Elde kalanın
maliyeti yalnızca kapanmamış lotlardan hesaplanır; gerçekleşmiş kâr/zarar ise
kapanan (alış, satış) çiftlerinden. Eşleşen lotlardan birinde fiyat yoksa rakam
hesaplanmaz — eksik veriyle sıfır göstermek "ne kâr ne zarar" diye okunurdu.

**Tamamı satılan fon kayıttan silinmez**, adedi 0 olan bir *kapanmış pozisyon*
olarak durur. Silinseydi satışın tarihi ve fiyatı da giderdi; gerçekleşmiş
kâr/zarar bir daha hesaplanamaz, geçmiş görüntülerdeki çıkış akışı fiyatsız
kalırdı. Kapanmış pozisyon raporlara ve TEFAS çekimine girmez, `list` altında
tek satır olarak anılır, işlem geçmişi `lots CODE` ile görülür. Kaydı gerçekten
silmek isterseniz `remove CODE`.

### Satışın valörü ve emir kaydı

Kesim saatinden (13:30) sonra verilen satış emri, emrin verildiği günün
fiyatından **gerçekleşmez**: bir sonraki iş gününe kayar. Zincir şöyle işler:

```
Emir (01.09 18:22)
  └─ kesim 13:30'dan sonra → işlem günü T = 02.09 (bir sonraki iş günü)
       └─ FİYAT = 02.09 kapanış değerlemesi  ← burada kilitlenir
            └─ satış valörü T+1 → paylar 03.09'da emanetten çıkar
                 └─ nakit T+2   → para 04.09'da hesapta
```

**Valör fiyat gününü kaydırmaz.** Fiyat işlem gününün kapanışında kilitlenir;
valör yalnızca payların, `nakit` ise paranın hareket gününü belirler. İşlem günü
kapanışından sonra fiyat riski taşımazsınız — fonun sonraki günlerde ne yaptığı
sizi etkilemez.

Bu ayrım kâğıt üstünde değil: 01.09.2026 18:22'de verilen PHE satış emri işlem
günü 02.09'a bağlandı ve 02.09 kapanışından (3,230415) gerçekleşti; 69.991 adet
için 226.099,98 ₺. Kod bir dönem fiyatı valör gününden (03.09, 2,818639) alıyordu
ve aynı işlemi 197.279,36 ₺ yazıyordu — 28.820,61 ₺'lik sahte kayıp. Fon o iki
günde sert düştüğü için bir günlük kayma doğrudan paraya dönüştü.

Taşıdığınız gerçek risk **emri verdiğiniz an ile işlem günü kapanışı arasındadır**;
kesim sonrası verilen emirde bu pencere bir günü aşar.

Bu aritmetiği kafanızda yapmayın — `emir` komutu yapar:

```bash
portfoy emir PHE 69991 --sat --tarih 01.09.2026 --saat 18:22 --nakit 04.09.2026
```

**Alışta adet yerine TL tutarı girin.** Aracı kurum alış emrini TL cinsinden
alır; adet ancak işlem gününün kapanış fiyatı yayımlanınca belli olur. Bu yüzden
alışta `--tutar` kullanılır ve adet, emir çözülürken `tutar ÷ işlem günü fiyatı`
olarak hesaplanır — lotun kaydedileceği fiyatın ta kendisiyle, ek varsayım
girmeden:

```bash
portfoy emir THF --al --tutar 226000 --tarih 04.09.2026 --saat 17:01 --nakit 08.09.2026
```

Adet bilinmediği sürece emir portföye **girmez** (alışta doğrusu bu: henüz
payınız yok, yalnızca paranız taahhüt edilmiş). Satışta `--tutar` kabul
edilmez: adet bilinmeden beklemedeki satış rezervesi sayılamaz ve aynı paylar
iki kez satılabilirdi.

`--saat` bilinmiyorsa `--kesim-oncesi` / `--kesim-sonrasi` kullanın. Saat
**zorunludur**: "herhalde erkendi" varsayımı, bu aracı en pahalı şekilde
yanıltan senaryonun ta kendisidir.

`--nakit` isteğe bağlıdır ama girin: aracı kurum ekranındaki nakit tarihi,
türetilen zincirin **doğrulanabilir tek ucudur**. Tutmuyorsa emir kaydedilmez
ve sebebi söylenir (ya emir saati ya nakit kuralı yanlış; ikisinden yalnızca
emir saati fiyat gününü kaydırır).

Gerçekleşme gününü ve fiyatını zaten biliyorsanız `emir` yerine
`add ... --accumulate` kullanın; orada valör hesabı yapılmaz — yazdığınız gün
işlem günü, yazdığınız fiyat da o günün kapanış değerlemesi sayılır.

### Bekleyen emirler

Emir verildiğinde gerçekleşme günü bellidir ama o günün değerleme fiyatı henüz
yayımlanmamıştır — TEFAS bir günün fiyatını ertesi gün ilan eder. Bu aralık
`veri/bekleyen.json`'da tutulur:

- **Adetler portföyde kalmaya devam eder.** Gerçekleşme gününe kadar fiyat
  riskini siz taşıyorsunuz; emri verir vermez pozisyonu düşmek o günlerin
  hareketini portföyden silmek olurdu.
- Aynı payları iki kez satmayı engellemek için beklemedeki satışlar rezerve edilir.
- `status` / `report` her çalıştığında fiyat gelmiş mi diye bakılır; gelmişse
  emir kendiliğinden işlem kaydına dönüşür.
- Tahmin edilen gün resmi tatile denk gelirse ilk işlem gününe **hizalanır** —
  seri ötesindeki tatiller önceden bilinemediği için.
- **Çözülme yeniden çalıştırılabilir.** Emri çözen lot, emrin kimliğini taşır
  (`emir_id`). Portföy ve bekleyen dosyası ayrı ayrı yazıldığı için ikisi
  arasında bir hata olursa (disk dolu, süreç öldürülmesi) emir hem işlenmiş hem
  bekliyor kalabilir; kimlik sayesinde bir sonraki çalışma onu tekrar
  uygulamaz, sessizce düşer. Bu koruma olmasaydı aynı satış iki kez işlenir ve
  adet geri dönüşsüz kaybolurdu.
- Aynı fon ve aynı gün için **alışlar önce** işlenir: satış önce gelip adet
  yetmeseydi emir sonsuza dek beklemede kalırdı.

### Valör tablosu

`veri/valor.json`. **TEFAS valör bilgisini yayınlamıyor**: çalışan
`fonBilgiGetir` ucu 11 alan döndürüyor (fiyat, portföy büyüklüğü, kategori,
yatırımcı sayısı...) ve valör bunların arasında yok; fon detay sayfası da
JavaScript korumasının arkasında. Dolayısıyla tablo elle beslenir.

Kategori varsayılanları (`fonKategori`'den) bir **başlangıç noktasıdır** ve
doğrulanana kadar *şüpheli* sayılır. Yanlış bir valör tablosu, tablo
olmamasından kötüdür: elle yapılan hata ara sıra olur ve fark edilir, yanlış
tablo her işlemde kendinden emin ve sessiz bir hata üretir.

```bash
portfoy valor PHE                              # kuralı gör
portfoy valor PHE --satis-valor 1 --satis-nakit 2 --dogrula
```

**Kayma neden olmuyor:** tablo kaydı *doldurur*, hesabı *sürmez*. Türetilen
tarih işlem kaydına bir kez yazılır ve orada donar; raporlar yalnızca yazılı
tarihi okur. Tablo altı ay sonra düzeltilse bile geçmiş ayların karnesi
kıpırdamaz. Tersi yapılsaydı — her hesapta yeniden türetme — tablodaki tek bir
düzeltme, ayların karnesini sessizce değiştirirdi.

**İş günü takvimi** ayrı bir tatil tablosundan değil, TEFAS fiyat serisinden
türetilir: TEFAS hafta sonu ve resmi tatillerde fiyat yayımlamadığı için serinin
kendisi zaten işlem günü takvimidir. Bayram, yarım gün, idari tatil — hepsi
kendiliğinden doğru çıkar ve takvim her veri çekiminde güncellenir.

Bunun iki sınırı var, ikisi de açıkça ele alınır:

- **Serinin öncesi.** Çekilen pencereden (`--days`, varsayılan 155 gün) eski bir
  emir girilirse hangi günlerin işlem günü olduğu bilinemez. Sessizce serinin
  ilk gününe yapıştırmak aylarca kayma üretirdi; onun yerine hata verilir ve
  pencereyi genişletmeniz istenir.
- **Serinin ötesi.** Gerçekleşme günü henüz yayımlanmamışsa hafta sonu kuralına
  düşülür ve sonuç *kesin değil* diye işaretlenir; fiyat geldiğinde kayıt gerçek
  takvime göre hizalanır.

**Kesim saati yalnızca seans günlerinde uygulanır.** Cumartesi 15:00'te verilen
emir de Pazartesi seansına girer ve Pazartesi'nin 13:30 kesimi henüz geçmemiştir;
duvar saatine bakıp fazladan bir gün itmek, bu modülün önlemek için var olduğu
bir günlük kaymayı üretirdi.

## Web arayüzü

Terminalden yönetmek zorlaştığında:

```bash
pip install 'portfoy-cli[web]'     # fastapi, uvicorn, jinja2, python-multipart
portfoy web --open                 # http://127.0.0.1:8000
```

| Sayfa | Ne yapar |
|---|---|
| **Durum** | Toplam değer, günlük/haftalık/aylık ağırlıklı getiri (**yüzde + TL**), fon tablosu, hedef karşılaştırması |
| **İşlemler** | Emir verme (valör türetmeli), bekleyen emirler, gerçekleşmiş alış/satış kaydı, lot geçmişi, kapanmış pozisyonlar ve gerçekleşmiş kâr/zarar, valör kuralları, hedef ayarı |
| **Raporlar** | Takvim ayı karnesi, Excel + grafik üretimi, üretilen dosyaların önizlemesi ve indirilmesi |
| **E-posta** | SMTP ayarları ve deneme gönderimi |

Arayüz CLI ile **aynı** iş mantığını kullanır (`storage`, `analytics`, `charts`,
`mailer`); ikisi aynı `veri/portfoy.json` üzerinde çalışır ve biri diğerinin
kaydını görür. `/api/docs` adresinde JSON API ve şeması da hazır durur.

### Erişim ve güvenlik

Sunucu **yalnızca 127.0.0.1**'e bağlanır; başka bir makineden erişilemez.
İki ek koruma vardır:

- **Konak adı doğrulaması.** Kötü niyetli bir site kendi alan adını 127.0.0.1'e
  çözdürüp tarayıcınızdan bu uygulamaya istek attırabilir (DNS rebinding).
  `Host` başlığı 127.0.0.1/localhost değilse istek 403 döner.
- **Kaynak doğrulaması.** Yazan isteklerde `Origin` başlığı **zorunludur** ve
  şema+konak+port olarak tam karşılaştırılır. Sadece konak adına bakmak
  yetmezdi: makinede çalışan başka bir yerel sunucudaki sayfa
  (`http://localhost:3000`) ya da `http://localhost:8000.evil.com` gibi bir
  alan adı kabul edilirdi. Başlığı hiç göndermeyen istek de reddedilir —
  CSRF'in en kolay yolu odur. Modern tarayıcılar same-origin form
  gönderimlerinde de `Origin` gönderdiği için bu katılık arayüzü kırmaz;
  **elle POST atan betiklerin başlığı eklemesi gerekir.**

`--host` ile bağlanma adresini değiştirebilirsiniz; verilen adres izinli konak
listesine eklenir. Ama **arayüzde parola koruması yoktur**: ağdaki herkes
portföyü görebilir ve değiştirebilir. Komut bu durumda uyarı basar.

`/api/docs` (Swagger arayüzü) betiğini bir CDN'den çeker, yani çevrimdışıyken
boş görünür. Şemanın kendisi (`/openapi.json`) her zaman yereldir.

### Doğrulama kuralları

Biçimsel kurallar `portfoy/web/schemas.py`'de (Pydantic), portföyün o anki
durumunu gerektirenler `portfoy/web/services.py`'de — ikincisi yazma kilidi
altında çalışır, yoksa doğrulama ile kaydetme arasında yarış oluşurdu.

| Kural | Neden |
|---|---|
| Fon kodu harf+rakam | TEFAS'ın beklediği biçim |
| Adet pozitif, sonlu, makul | İşaret `Alış/Satış` seçiminden gelir; NaN/∞ form üzerinden gerçekten gelebiliyor |
| Tarih gelecekte olamaz | Gün/ay karıştırmanın en sık belirtisi |
| **Satışta tarih ve fiyat zorunlu** | Gerçekleşmiş kâr/zarar ve aylık karnedeki çıkış akışı yalnızca onlardan hesaplanabiliyor |
| Elde olandan fazlası satılamaz | Kapanmış/olmayan pozisyonda ayrı, açıklayıcı mesaj |
| Yeni fon kodu TEFAS'ta doğrulanır | Yazım hatası olan kod portföye girerse rapor sessizce eksik çalışır. Bağlantı sorununda reddetmez, uyarır; kod doğruysa "doğrulamayı atla" ile geçilir |
| Adet düzeltme ve fon silme onay ister | İkisi de işlem geçmişini siler |
| Kapanmış pozisyonun üzerine yazılamaz | Gerçekleşmiş kâr/zarar geri dönülmez şekilde giderdi |
| Adet düzeltme yalnızca **mevcut** fonda çalışır | Aksi halde alış formundaki TEFAS kod doğrulaması ikinci kapıdan atlanırdı |
| Emirde saat veya kesim tarafı **zorunlu** | "Herhalde erkendi" varsayımı gerçekleşme gününü bir gün kaydırır |
| Nakit tarihi girildiyse tutmalı | Türetilen zincirin doğrulanabilir tek ucu; tutmuyorsa emir kaydedilmez |
| Beklemedeki satışlar rezerve edilir | Aynı payları iki kez satmayı engeller |
| Valör 0-10 iş günü | Elle girilen saçma değer sessizce kabul edilmesin |
| Grafik seçildiyse en az bir periyot | Tüm kutuları kaldıran kullanıcının niyeti "hepsini üret" değildir |
| İndirmede yalnızca `.png`/`.xlsx`, çıktı klasörü altında | Yol kaçışıyla portföy dosyası okunamasın |

Her mutasyon **POST-Redirect-GET** ile biter: tarayıcıyı yenilemek işlemi
tekrarlamaz. Para kaydeden bir formda ikinci kez gönderilen alış, sessizce iki
katına çıkan pozisyon demektir. Onay/hata mesajları yönlendirme sırasında
tek kullanımlık bir jetonla taşınır — sunucuda ortak bir listede tutulsalardı
iki açık sekmede mesaj yanlış sekmeye gider, doğru sekmede kaybolurdu ve
kullanıcı onay göremediği işlemi tekrar girerdi.

## Testler

```bash
pip install 'portfoy-cli[web,test]'
pytest
```

Testler **gerçek veri dosyalarına dokunmaz**: her test kendi geçici portföyüyle
çalışır (`tests/conftest.py`). Kapsam: FIFO eşleştirme ve gerçekleşmiş kâr/zarar
(`test_storage.py`), zaman ağırlıklı getiride akış fiyatlaması
(`test_snapshots.py`), valör türetmesi ve kesim saati (`test_valor.py`),
bekleyen emirlerin çözülmesi (`test_bekleyen.py`), web doğrulama + güvenlik +
mutasyon akışı (`test_web.py` — web ekstrası kurulu değilse atlanır).

Portföyün yan dosyaları (`gecmis.json`, `bekleyen.json`, `valor.json`) portföy
dosyasına bağlıdır: `--data-file` verildiğinde onlar da o dosyanın yanına yazılır
(`config.yan_dosyalar`). Sabit yola yazsalardı bir test koşusu asıl portföyün
karnesini ve valör tablosunu bozardı.

## Ağırlıklı getiri nasıl hesaplanır

Portföy getirisi tanım gereği `(bitiş − başlangıç) / başlangıç`. Fon bazında
`V_bitiş = V_başlangıç × (1 + r)` olduğu için ağırlık **başlangıç** değeridir,
bugünkü değer değil.

Bugünkü ağırlıkla çarpmak kazanan fonu fazla, kaybedeni az sayar — çünkü
kazanan tam da o getiriyi kazandığı için büyümüştür. Sapma **sistematik olarak
yukarı**dır ve fonlar ne kadar ayrışırsa o kadar büyür. Gerçek portföyde günlük
rakamın işaretini bile ters çevirdiği görüldü: `+%0,02` raporlanırken portföy
2.866 TL kaybetmişti.

Basit bir örnek — iki fon, ikisi de 100.000 TL:

| | Getiri | Bitiş değeri |
|---|---|---|
| A | +%10 | 110.000 |
| B | −%50 | 50.000 |

Başlangıç 200.000, bitiş 160.000 → gerçek getiri **−%20**. Bitiş ağırlığıyla
hesaplanırsa `%10 × (110/160) + (−%50) × (50/160) = −%8,75` çıkar; portföyün
gerçekte kaybettiğinin yarısından azı.

Aynı taban `value_change` ile paylaşıldığı için **yüzde ve TL birebir tutar**:
arayüzde yan yana gösterildiklerinde çelişmezler. Fon satırlarındaki TL
değişimlerinin toplamı da tablo altındaki toplamı verir.

Yüzde tek başına "ne kadar para" sorusunu cevaplamıyor: %1'lik hareket 1,9
milyonluk bir pozisyonda 19 bin TL, 350 binlikte 3,5 bin TL demek. Bu yüzden
hem durum tablosunda hem özet kutularında ikisi birlikte gösteriliyor.

## Notlar

- **Veri kaynağı:** önce `tefas-crawler` paketi, o başarısız olursa doğrudan
  TEFAS JSON API'si (`/api/funds/fonFiyatBilgiGetir`) denenir. İki yol da aynı
  sonucu verir. Bir fon çekilemezse diğerleri raporlanmaya devam eder; program
  çökmez, eksik fonlar ayrıca listelenir.
  *Not: eski `BindHistoryInfo` / `TarihselVeriler.aspx` ucu 2026'da kapatıldı ve
  tefas.gov.tr'nin HTML sayfaları JavaScript koruması arkasında — sayfayı
  kazımak yerine yukarıdaki JSON ucu kullanılıyor.*
- **Fon adları** tabloda kodun altında gösterilir. Beklediğinizden farklı bir ad
  görürseniz fon kodu yanlış demektir.
- **Önbellek:** çekilen veri 3 saat `veri/cache/` altında tutulur. Taze veri için
  `--no-cache` kullanın.
- **Geçmiş penceresi karne penceresinden türetilir.** `--months N` için
  `(N + 2) × 31` gün çekilir (taban ayı + içinde bulunulan ay payı). Sabit bir
  sayı olsaydı `--months` büyütüldüğünde çekilen veri yetmez, karne sessizce
  kırpılırdı. `--days` bir taban olarak hâlâ verilebilir; büyük olan kazanır.
- **Getiri katkısı grafiği şelaledir (waterfall).** Sıfırdan başlar, her fon
  kendinden önceki birikimin üstüne bir basamak ekler veya onu aşağı çeker, en
  sağdaki `TOPLAM` çubuğu portföyün ağırlıklı getirisine iner. "Kim yukarı itti,
  kim aşağı çekti, sonuç ne oldu" tek bakışta okunur.
  (Eskiden pasta çiziliyordu; pasta yalnızca bütün katkılar pozitifken anlamlıydı
  ve negatif çıktığında rapor bambaşka bir grafiğe atlıyordu.)
- **Şelalede renk işareti taşır, fonu değil:** mavi = artıya katkı, kırmızı =
  eksiye, koyu gri = portföy toplamı. Fon kimliğini x ekseni etiketleri taşır.
  Yeşil/kırmızı ayrımı bilerek kullanılmadı — renk körlüğünde bu iki renk ayırt
  edilemiyor (protanopi ΔE 4,6); mavi/kırmızı tüm CVD kontrollerinden geçiyor.
- **Portföy değişimi grafiği** her periyot için bir kart gösterir: büyük rakam
  yüzde, altında TL karşılığı, en altta yüzdeyi kartlar arasında **ortak
  ölçekte** karşılaştıran şerit. Yüzde ile TL farklı ölçekte olduğundan bilerek
  tek eksene bindirilmedi.
- **Pasta grafiklerde her fon adlandırılır.** %4'ten ince dilimlerde etiket
  dilimin içine sığmadığı için dışarıya, kılavuz çizgiyle yazılır; üst üste
  binenler dikeyde ayrıştırılır. (Eskiden ince dilimler etiketsiz kalıyor, o
  fonlar grafikte adsız görünüyordu.)
- **Fiyat = pay fiyatı.** Adet olarak elinizdeki **pay sayısını** girin, yatırdığınız
  TL tutarını değil.
- TEFAS fiyatları bir gün gecikmeli yayınlanabilir; rapor başlığındaki tarih
  verinin gerçekte ait olduğu günü gösterir.

## Proje yapısı

```
pyproject.toml             paket tanımı + `portfoy` komutu (project.scripts)
main.py                    depodan çalıştırma girişi (portfoy.cli'yi çağırır)
portfoy/
  cli.py                   CLI arayüzü (argparse)
  config.py                sabitler, dosya yolları, renk paleti
  storage.py               portföyün JSON'da saklanması (atomik yazma)
  tefas_client.py          TEFAS veri çekme + önbellek + yeniden deneme
  analytics.py             ağırlık, getiri, ağırlıklı ortalama
  charts.py                matplotlib grafikleri
  snapshots.py             günlük portföy görüntüleri + TWR
  excel_report.py          pandas + openpyxl raporu
  console.py               rich tablo / düz metin çıktısı
  formatting.py            Türkçe sayı biçimlendirme (1.234,56)
  mailer.py                SMTP gönderimi (HTML + gömülü grafik + ek)
veri/portfoy.json          kayıtlı adetler
veri/mail.json             e-posta ayarları (gitignore'da)
ciktilar/                  üretilen xlsx + png dosyaları
```

Bu araç kişisel takip amaçlıdır, yatırım tavsiyesi değildir.
