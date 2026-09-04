# Baslangic Rehberi

Bot **tam otonom** calisir: hangi sembol, hangi yon, ne kadar, ne zaman
kapatilacak -- hepsine kendisi karar verir. Sen kurarsin, sonra dokunmazsin.

---

## 0. Once gercek rakam

**Bot para basmaz.** 5 yillik gecmis veride yillik **+%15,4** olcusdu ve yolda
**%14 dusus** vardi. Gelecek daha kotu olabilir: ayni testin ikinci yarisinda
oran +%9'a dusuyor.

$200 ile:

| | tutar |
|---|---|
| Beklenen yillik getiri | ~$31 (gunde ~$0,08) |
| Olculen en kotu dusus | -$28 (hesap $172'ye iner) |
| Daha kotusunun olma ihtimali | var |

Bu rakamlar geleceği garanti etmez. Kripto pozisyonlari yuksek korelasyonlu:
4 acik long, cokuste tek buyuk pozisyon gibi davranir.

**Kaybetmeyi goze alamayacagin parayi koyma.**

---

## 1. Kurulum (bir kez, ~5 dakika)

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env          # Telegram bilgilerini buraya yaz (onerilir)
```

## 2. Servis olarak kur -- bundan sonra hicbir sey yapmayacaksin

```bash
python -m bot install --mode paper
```

Bu komut isletim sistemine gore dogru dosyayi uretir ve calistiracagin
**iki komutu** ekrana yazar. Onlari kopyala-yapistir, bitti.

Bundan sonra:

| Olay | Ne olur |
|---|---|
| Bilgisayari yeniden baslatirsin | Bot kendiliginden acilir |
| Bot cokerse | 30 saniye icinde kendini toparlar |
| Internet keserse | Baglanti gelince kaldigi yerden devam eder |
| Sen uyurken | Calisir, islem acar, kapatir |
| Oturumu kapatirsin | Calismaya devam eder (linger) |

Acik pozisyonlarin koruma emirleri **borsada durur** -- bilgisayar tamamen
kapansa bile stop'un aktiftir.

## 2.4. Panele bakmak (bilgisayardan)

Bot calisirken tarayicini ac ve su adrese git:

```
http://127.0.0.1:8787
```

Hepsi bu. Kurulum yok, sifre yok. Gordugun sey:

- **Bakiye egrisi** -- paranin zaman icinde nasil gittigi
- **Acik pozisyonlar** -- su an ne kadar kar/zarardasin
- **Isabet ve basabas** -- yan yana. Isabetin basabastan yuksekse
  bot kar ediyor demektir. Tek basina "isabet %55" bir sey soylemez.
- **Saglik kontrolleri** -- yesil nokta iyi, sari dikkat, kirmizi sorun
- **Ogrenme** -- bot neyi kapatti, hangi dersi cikardi

Bot kapaliyken gecmise bakmak istersen:

```bash
python -m bot dashboard
```

Iki sey bilmen yeter:

- **Panelde dugme yok, sadece bakarsin.** Bu bilerek boyle. Kontrol
  Telegram'da (asagida). Sebebi: localhost'ta acik duran bir sayfaya
  tarayicindaki baska siteler de istek gonderebilir; oraya "pozisyonu
  kapat" dugmesi koymak riskli olurdu.
- **Sadece senin bilgisayarindan gorulur.** Ayni evdeki telefon bile
  goremez. Bu da bilerek: panel bakiyeni gosteriyor.

---

## 2.5. Telefonundan bota bakmak (istege bagli, 3 dakika)

Bot sana kendisi rapor gonderir. Ama merak edip bakmak istersen Telegram
kur -- ayrica **acil durumda mudahale** edebilmen icin tek yol budur.

1. Telegram'da [@BotFather](https://t.me/BotFather) ac, `/newbot` yaz,
   verdigi token'i kopyala.
2. [@userinfobot](https://t.me/userinfobot) ac, sana yazdigi `Id` sayisini kopyala.
3. `.env` dosyana ekle:

```bash
TELEGRAM_BOT_TOKEN=buraya_token
TELEGRAM_CHAT_ID=buraya_id
```

Sonra kendi botuna yazabilecegin komutlar:

| yaz        | ne olur |
|------------|---------|
| `/durum`   | acik pozisyonlar, ne kadar kar/zarardasin |
| `/bakiye`  | hesapta ne var |
| `/rapor`   | gunun tam ozeti |
| `/ogrenme` | bot neyi ogrendi |
| `/dur`     | yeni islem acmayi durdur |
| `/devam`   | tekrar ac |
| `/kapat`   | HER SEYI simdi kapat (once `/onayla` ister) |

Bilmen gereken uc sey:

- **`/dur` acik pozisyonlari kapatmaz.** Onlarin stop emirleri borsada
  durmaya devam eder. Sadece yeni islem acilmaz.
- **`/kapat` geri alinamaz.** Bu yuzden iki adim: once `/kapat`, sonra
  `/onayla`. Yanlislikla basmani engellemek icin.
- **Bunlari kullanman gerekmiyor.** Bot zaten kendi karar veriyor. Bu
  komutlar "bir seyler ters gidiyor gibi" hissettigin an icin var.

---

## 3. Patron rolu: pasif

Bot gunde **bir kez** telefonuna ozet gonderir. Sen bota gitmezsin, o sana gelir.

```
GUNLUK OZET - 12.03.2026

Bakiye: 214.30 USDT
Toplam 38 islem | isabet %47 | net +14.30
Acik pozisyon: 2 (BTCUSDT, SOLUSDT)

DURUM: normal calisiyor
Kontroller: her sey yolunda, yapman gereken bir sey yok.
```

Merak edersen istedigin an bakabilirsin (zorunlu degil):

```bash
python -m bot doctor    # her sey yolunda mi?
python -m bot status    # islem gecmisi
python -m bot learn     # bot ne ogrendi
```

## 4. Canliya gecmek

Kagitta **en az 2 hafta** sorunsuz calistiktan sonra:

```bash
python -m bot install --mode live    # 'ANLADIM' yazmani ister
```

API anahtari kurallari:
- Sadece **Futures** izni ac
- **Withdraw iznini ASLA acma** (anahtarin calinsa bile paran cekilemez)
- IP whitelist tanimla

---

## Bot kendini nasil korur (sen uyurken)

| Durum | Botun karari | Senin rolun |
|---|---|---|
| Gunluk -%4 zarar | Bugunluk durur | Yok, yarin acilir |
| Gunluk +%6 kar | Bugunluk durur | Yok, yarin acilir |
| Ust uste 3 zarar | 4 saat soguma | Yok |
| Bir sembol bozuldu (kanitli) | 14 gun o sembole girmez | Yok |
| Stop surekli avlaniyor | Stop mesafesini genisletir | Yok |
| Ayni emir hatasi 3 kez | O sembolu 7 gun devre disi birakir | Yok |
| **Strateji tamamen bozuldu** | **Golge moduna gecer** | Yok |
| Bot cokerse | Servis 30 sn icinde yeniden baslatir | Yok |

### Golge modu nedir

Stratejinin edge'i gercekten olurse (50+ islem ve %99 kesinlikle negatif),
bot durmaz. **Kagit uzerinde islem yapmaya devam eder** -- sinyal uretir,
sanal pozisyon acar, yonetir, kapatir. Sadece borsaya emir gitmez.

Acik gercek pozisyonlar kapatilir, para guvende bekler.

Golgede 40 sanal islemde beklentinin pozitif oldugu kanitlanirsa bot
**kendiliginden canliya doner.** Kanitlanmazsa sonsuza kadar golgede kalir.

Sen bu surecte hicbir sey yapmazsin. Sadece telefonuna bilgi mesaji gelir.

Neden durmak yerine bu: **durmak bilgi uretmez.** Golge modu olcmeye devam
eder, boylece "gercekten bozuldu mu yoksa gecici bir rejim mi" sorusu
cevaplanabilir. Durmus bir bot bunu asla ogrenemez.

Neden kendini yeniden ayarlamak yerine bu: golge modu **parametreleri
degistirmez.** Ayni strateji, ayni ayarlar, sadece para yok. Geri donus
karari gercek bir sinavdir, gecmise uydurulmus bir parametre degil.

---

## Sik sorulanlar

**"Bot 5 islemde ust uste kaybetti, bir sorun mu var?"**
Hayir. Isabet orani %46. Ust uste 8 kayip bile %93 ihtimalle 5 yil icinde
gorulecek normal bir olay. Bot bu seride bilerek hicbir sey yapmaz.

**"Hesap %15 dustu, kapatayim mi?"**
Hayir. Backtest'te en kotu dusus %21 idi. Bu beklenen aralikta. Dususte
botu kapatmak, kaybi kalici hale getirmenin en yaygin yoludur.

**"Bot kendi kendine ne kadar karar veriyor?"**
Islem kararlarinin **tamami**: hangi sembol, hangi yon, ne kadar buyuklukte,
hangi kaldiracla, ne zaman kismi kar alinacak, stop nereye tasinacak, ne
zaman kapatilacak. Ayrica kendini durdurma, sembol banklama, stop kalibrasyonu
ve golge moduna gecis/donus kararlari.

**"Neyi kendi kendine DEGISTIRMEZ?"**
Stratejisini ve ana parametrelerini. Bu kasitli: serbest birakildiginda
beklenti +0.088R'den +0.041R'ye dusuyor (olculdu). Kendini surekli optimize
eden bir sistem gecmise uyum saglar, gelecege degil.

**"Daha cok kazanmak icin kaldiraci artirsam?"**
Kaldirac getiriyi artirmaz, sadece marji dusurur. Pozisyon boyutunu stop
mesafesi belirliyor. Getiri icin `risk_per_trade_pct`'i yukselt -- ama dusus
de ayni oranda buyur. %0.75 -> %1.5 yaparsan yillik beklenti ~%24'e cikar,
dusus ~%40'a.

---

## Ayarlayabilecegin gunluk sinirlar

`config.yaml` icinde, `risk:` bolumunde:

```yaml
risk_per_trade_pct: 0.75      # tek islemde riske atilan (equity yuzdesi)
daily_loss_limit_pct: 4       # gunluk bu kadar zarar edince durur
daily_profit_target_pct: 6    # gunluk bu kadar kar edince durur
max_trades_per_day: 8         # gunluk islem tavani
max_concurrent_positions: 4   # ayni anda acik pozisyon sayisi
```

Bunlarin disinda hicbir seye dokunma. Anlamadigin bir parametreyi
degistirmek, botun matematigini bozar.

---

## Neyi asla yapma

1. Zararin ortasinda riski artirma
2. Kar ederken riski artirma
3. Dususte botu kapatip yukseliste geri acma
4. Anlamadigin bir parametreyi degistirme
5. Kaybetmeyi goze alamayacagin parayi koyma

Botun en buyuk avantaji zeki olmasi degil, **duygusuz olmasi.**
O avantaji elinden alma.
