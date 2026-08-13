# TEFAS Portföy Takip CLI

TEFAS'tan fon fiyatlarını çeker, portföyünüzün **ağırlıklı ortalama** getirisini
hesaplar, terminalde renkli tablo basar, Excel raporu ve iki grafik üretir.

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
| `remove CODE` | Fonu portföyden çıkarır |
| `list` | Kayıtlı fonları gösterir (TEFAS'a bağlanmadan) |
| `lots [CODE]` | Kayıtlı alış/satış işlemlerini gösterir |
| `target PERCENT` | Aylık getiri hedefini ayarlar |
| `status` | Güncel tabloyu terminalde gösterir |
| `report` | Tablo + Excel + grafikleri üretir **ve e-posta gönderir** |
| `mail-setup` | E-posta gönderimini yapılandırır |
| `mail-test` | Deneme e-postası gönderir (gerçek rapordaki eklerle birlikte) |
| `clear-cache` | Yerel veri önbelleğini siler |
| `help` | Bütün komutları gruplanmış halde gösterir |

Faydalı bayraklar:

```bash
portfoy report --show             # grafikleri ekranda da aç
portfoy report --theme dark       # koyu tema grafikler
portfoy report --period haftalik  # katkı grafiğini haftalık yap
portfoy report --no-excel         # sadece grafik
portfoy status --no-cache         # önbelleği atla, taze veri çek
portfoy status --verbose          # hata ayıklama günlüğü
portfoy --data-file ~/alt.json status  # ikinci bir portföy
```

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
- İki grafik **gövdeye gömülü** (ek açmadan telefonda görünür)
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
- `getiri_katkisi_aylik.png` — toplam getiriye fon bazında katkı

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
Fonda **satış** varsa hangi lotun kapandığı belirsiz olduğundan maliyet ve
kar/zarar hesaplanmaz (boş gösterilir).

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
- **Getiri katkısı grafiği** normalde pasta grafiktir. Herhangi bir fonun katkısı
  negatifse pasta matematiksel olarak anlamsız olacağından (negatif dilim
  çizilemez) otomatik olarak sıfır eksenli yatay bar grafiğe geçilir.
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
  excel_report.py          pandas + openpyxl raporu
  console.py               rich tablo / düz metin çıktısı
  formatting.py            Türkçe sayı biçimlendirme (1.234,56)
  mailer.py                SMTP gönderimi (HTML + gömülü grafik + ek)
veri/portfoy.json          kayıtlı adetler
veri/mail.json             e-posta ayarları (gitignore'da)
ciktilar/                  üretilen xlsx + png dosyaları
```

Bu araç kişisel takip amaçlıdır, yatırım tavsiyesi değildir.
