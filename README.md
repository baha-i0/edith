# Binance Futures Trading Bot

Binance USD-M Futures uzerinde calisan, risk kontrollu, backtest edilmis
otomatik islem botu. Python 3.11+, harici bagimlilik: `requests` + `PyYAML`.

---

## Once dogruyu soyleyelim: ilk fikrin matematiksel olarak kaybediyordu

Istenen kurulum soyleydi:

> 10x kaldirac, -40$ zararda kes, +20$ karda sat.

Bu **2:1 sana karsi** bir risk/odul orani. Basabas icin gereken isabet orani:

```
p * 20 = (1 - p) * 40   ->   p = 66.7%
```

Ustune gidis-donus komisyon + slipaj (fiyatin ~%0.14'u) eklendiginde gercek
basabas ~%70. Hicbir kisa vadeli sinyal bunu surdurulebilir sekilde tutturamaz.

Daha da onemlisi: **kaldirac risk degildir, stop mesafesi risktir.**

| stop mesafesi | komisyonun R cinsinden maliyeti | 2.2R hedef icin basabas isabet |
|---------------|--------------------------------|-------------------------------|
| %0.3          | 0.47R                          | %45.8                         |
| %0.5          | 0.28R                          | %40.0                         |
| %1.0          | 0.14R                          | %35.6                         |
| %2.0          | 0.07R                          | %33.4                         |
| %3.0          | 0.05R                          | %32.7                         |

Dar stop = komisyonun kazandigi oyun. Bot bu yuzden pozisyonu soyle kurar:

```
1. Bu islemde kaybetmeyi kabul ettigim para  = equity * risk%   (varsayilan %0.75)
2. Stop mesafesi                             = ATR ve yapi belirler
3. Miktar                                    = (1) / (2)
4. Kaldirac                                  = sadece bu miktarin marjini karsilamaya yetecek kadar
```

Kaldiraci artirmak riski **artirmaz**, sadece daha az marj bloke eder.
Kaldiracin gercek tehlikesi likidasyondur; o da ayrica sinirlanir: stop,
likidasyon mesafesinin en fazla %60'i kadar uzakta olabilir. Gerekirse bot
istenen kaldiraci kendiliginden asagi ceker.

---

## Kanit: parametreler nereden geldi

Hicbir ayar "iyi gorunduğu icin" secilmedi. Hepsi **data.binance.vision**
resmi arsivinden indirilen gercek 4h/1h/15m/5m verisiyle, kotumser
varsayimlarla (giris sonraki mumun acilisinda, ayni mumda stop hedeften once,
her fille komisyon + slipaj, 8 saatte bir funding) test edildi.

### 1. Zaman dilimi secimi (BNBUSDT, 5 yil, ayni strateji)

| timeframe | islem | beklenti (R/islem) |
|-----------|-------|--------------------|
| 5m        | 510   | **-0.300**         |
| 15m       | 356   | **-0.247**         |
| 1h        | 620   | **-0.130**         |
| 4h        | 135   | **+0.075**         |

Sebep yukaridaki tablo: 4h'te stop mesafesi ~%3, 5m'de ~%0.5. Ayni strateji,
ayni sinyal -- tek fark komisyonun R cinsinden agirligi.

Ters sinyal testi de yapildi (long/short yer degistirdi): o da kaybetti.
Yani 5m'de yon tahmininde hata yoktu, **hic edge yoktu**; iki taraf da
komisyona yeniliyordu.

### 2. Volatilite tavani (5 sembol, 5 yil, 4h)

| atr_pct_max | islem | beklenti | pozitif sembol |
|-------------|-------|----------|----------------|
| 1.5         | 193   | +0.111   | 4/5            |
| 2.0         | 379   | +0.036   | 4/5            |
| **2.5**     | 531   | +0.056   | 4/5            |
| 3.0         | 616   | +0.040   | 4/5            |
| 4.0         | 682   | +0.018   | 4/5            |
| 6.0         | 718   | +0.000   | 3/5            |

Iliski **monoton** -- tek bir sansli hucre degil. Overfit olsaydi duzensiz
sicramalar gorurduk.

### 3. Genislik testi (20 sembol, 5 yil, 4h, tek tek)

1562 islem, agirlikli beklenti **+0.093R**, 20 sembolun 15'i pozitif.
Dikkat cekici: altcoinlerin cogu al-tut'ta -%60 ile -%99 arasi kaybederken
bot pozitifti -- dusus trendlerini short tarafindan yakaladigi icin.

### 4. Portfoy backtesti (sevk edilen config: 15 sembol, 4 slot, risk %0.75)

Tek sembol backtesti yaniltir; ortak kasa ve es zamanli pozisyon limiti yokmus
gibi davranir. Gercek sayi bu:

```
Sure             : 4.9 yil
Equity           : 200.00 -> 386.48 (+93.2%)
Yillik bilesik   : +14.2%
Islem            : 877  (177/yil)
Isabet / PF      : %46.2 / 1.19
Beklenti         : +0.112 R/islem
Maks. dusus      : 21.4%
t-degeri (kaba)  : 2.63
```

Slot sayisinin etkisi (20 sembol, risk %0.75 sabit):

| es zamanli pozisyon | yillik bilesik | maks. dusus |
|---------------------|----------------|-------------|
| 2                   | +7.4%          | 16.8%       |
| 4                   | +14.2%         | 21.4%       |
| 5                   | +15.4%         | 23.6%       |
| 8                   | +22.5%         | 23.8%       |

**Kucuk bir edge'i buyutmenin yolu kaldirac degil, genislik.** Kaldirac hem
getiriyi hem riski buyutur; daha cok sembol getiriyi buyutup riski goreceli
sabit tutar (korelasyon sinirina kadar).

### 5. Zaman disi dogrulama

| donem              | yil | islem | yillik | beklenti | t    |
|--------------------|-----|-------|--------|----------|------|
| tamami             | 4.9 | 935   | +11.8% | +0.088   | 2.15 |
| ilk yari           | 2.4 | 422   | +14.8% | +0.113   | 1.82 |
| ikinci yari (OOS)  | 2.5 | 509   | +8.6%  | +0.063   | 1.14 |
| son 1 yil (OOS)    | 1.0 | 220   | +14.0% | +0.082   | 0.95 |

---

## Bu sonuclara ne kadar guvenmeli (durust degerlendirme)

**Guclu yanlar**
- Edge tum alt donemlerde pozitif, 20 sembolun 15'inde pozitif.
- Parametre yuzeyi duzgun ve monoton; keskin bir tepe noktasina oturmuyor.
- Kotumser fill varsayimlariyla ve gercek komisyonlarla test edildi.

**Zayif yanlar -- bunlari gormezden gelme**
- `t = 2.63` gibi gorunuyor ama **islemler bagimsiz degil**. Kripto piyasasi
  yuksek korelasyonlu; efektif ornek boyu cok daha kucuk. Duzeltilmis t
  muhtemelen 1.5 civari. Yani "sans" aciklamasi tam olarak elenemiyor.
- Beklenti ikinci yaride dusuyor (+0.113 -> +0.063). Edge asinmasi olabilir,
  gurultu de olabilir. Ayirt edecek veri yok.
- Sembol listesi **bugun var olan** coinlerden secildi. Delist olanlar yok
  (hayatta kalma yanliligi). Etkisi muhtemelen kucuk ama sifir degil.
- Backtest funding'i sabit %0.01 varsayiyor; gercekte degisken.
- 4 es zamanli long, piyasa cokusunde tek buyuk pozisyon gibi davranir.
  Cesitlendirme kripto icinde sinirli bir korumadir.

**Sonuc:** Bu bot bir para basma makinesi degil. Yillik %10-15 civari beklenti,
%20'nin uzerinde dususlerle. Al-tut'u boga piyasasinda yener mi? Hayir --
BTC/BNB'de al-tut 5 yilda +%72/+%77 yapti. Bota deger katan sey, ayi
piyasasinda (altcoinlerde -%90) pozitif kalabilmesi.

---

---

## Ogrenme katmani: "hatalarindan ders cikaran bot"

### Once tuzagi soyleyelim

Naif versiyon -- her zarardan sonra parametre ayarlamak -- calisan bir sistemi
bozmanin en guvenilir yoludur. Sayilarla:

```
islem basi beklenti  = +0.11R
islem basi std sapma = ~1.3R
10 islemlik serinin standart hatasi = 1.3/sqrt(10) = 0.41R
```

Yani 10 islemin ortalamasi, gercek beklentinin 4 katı kadar sapabilir.
**Kisa serilerde ogrenilecek hicbir sey yoktur** -- sadece gurultu vardir.

Bu bos bir uyari degil, olculdu. Esikleri 5 isleme dusurup Bayesci onseli
kaldirinca (yani "hizli ogrenen" bot):

| | islem | CAGR | beklenti | t |
|---|---|---|---|---|
| ogrenme kapali | 935 | +11.8% | +0.088R | 2.15 |
| **naif ogrenme (5 islem esigi, onselsiz)** | 766 | **+4.9%** | **+0.041R** | **0.93** |

Beklenti yariya indi, istatistiksel anlamlilik yok oldu. "Daha hizli ogrenen"
bot, edge'ini yiyen bottur.

### Bu yuzden ogrenme asimetrik

| Hiz | Ne ogrenilir | Kac ornek gerekir |
|-----|--------------|-------------------|
| **Hizli** | emir reddi, min notional yetersizligi, marj hatasi, koruma emri kurulamamasi | 3 tekrar |
| **Yavas** | bir sembolun/rejimin beklentisinin negatif olmasi, stop mesafesinin dar olmasi | 30+ islem **ve** %99 anlamlilik |
| **Hicbir zaman** | "son 3 islem zarardi, stratejiyi degistir" | -- bu ogrenme degil, panik |

Operasyonel hatalar deterministiktir: min notional yetmiyorsa yetmiyordur,
istatistik gerekmez. Strateji performansi ise stokastiktir; orada acele
etmek ogrenme degil, gurultuye uyum saglamaktir.

### Ne ogreniyor

**1. Stop kalibrasyonu (olculen tek net kazanc)**
Stop yedikten sonra fiyat 12 mum icinde orijinal hedefe ulastiysa bu bir
*stop avlanmasidir*: yon dogruydu, stop cok dardi. Zararli cikislarin %45'inden
fazlasi boyleyse stop mesafesi kademeli genisletilir (tavan 1.5x), R katlari
korunarak. Backtest'te 15 sembolun 7'sinde tetiklendi.

**2. Kova banklama (kanit gerektiren)**
Bir sembol veya rejim kovasinin beklentisi *kanitlanmis* sekilde negatifse
14 gun devre disi kalir, sonra kucuk pozisyonla gozetim altinda doner.
Ortalamanin negatif olmasi yetmez -- ust guven sinirinin de sifirin altinda
olmasi gerekir.

**3. Bayesci kucultme**
Canli beklenti tahmini, backtest beklentisine (onsel) cekilir. Onsele
40 islemlik guven verilir; yani 3 kotu islem tahmini devirmez.

**4. Hata defteri**
Ayni operasyonel hata 3 kez tekrarlanirsa sembol 7 gun devre disi kalir.
Ceza bitince **sayac sifirlanir** -- yoksa gecici bir sorun kalici yasaga
donusurdu.

**5. Dusus olcegi (varsayilan KAPALI)**
Zararda pozisyon boyutunu kucultur. Olculdu: ~3 puan CAGR maliyetine ~4 puan
dusus azaltiyor, yani risk-getiri oranini iyilestirmiyor. Sermaye korumasini
getiriye tercih ediyorsan `drawdown_scaling: true` yap.

### Onemli tasarim karari: ogrenme riski ASLA artiramaz

`max_risk_multiplier: 1.0`. Bot iyi giden bir seride pozisyonlari buyutemez.
Bu tek yonlu emniyet kasten: yanlis ogrenmenin maliyeti simetrik degil.
Kaciralan kar geri gelir, buyutulmus zarar gelmez.

### Yanlis alarm sorunu ve nasil olculup duzeltildi

Ilk kurulum her islemden sonra test yapiyordu, esik tek yonlu %95 (z=1.64).
Demo verisinde **gercekten pozitif beklentili** bir sembol banklandi. Sebep
istatistiksel: her islemde test etmek nominal hatayi asar (optional stopping /
coklu karsilastirma).

Simulasyonla olculdu (200 islem, 3000 tekrar, gercek beklenti +0.11R):

| z | test sikligi | yanlis bank | -0.3R yakalama | -0.6R yakalama |
|---|--------------|-------------|----------------|----------------|
| 1.64 | her islem | 6.1% | 98.1% | 100% |
| **2.33** | **her 10 islem** | **0.7%** | **88.9%** | **100%** |
| 2.58 | her 10 islem | 0.2% | 82.9% | 100% |
| 3.09 | her 10 islem | 0.1% | 65.6% | 100% |

Secilen ayar yanlis alarmi 9 kat azaltirken gercekten bozuk bir sembolu
hala %100 yakaliyor. Duzeltmeden once ogrenme risk-getiri oranini
kotulestiriyordu; sonra iyilestirdi.

### Olculen etki (portfoy backtesti, 15 sembol, 5 yil)

| | islem | CAGR | maks dusus | CAGR/dusus | beklenti |
|---|---|---|---|---|---|
| ogrenme kapali | 935 | +11.8% | 21.7% | 0.54 | +0.088R |
| **ogrenme acik (sevk edilen)** | 907 | +10.9% | **18.6%** | **0.59** | +0.091R |
| + dusus olcegi de acik | 905 | +9.2% | 18.7% | 0.49 | +0.088R |

5 yilda **sifir yanlis bank**, 7 sembolde stop genisletmesi.

**Durustce:** beklenti farki (+0.088 vs +0.091) gurultu icinde -- 900 islemde
standart hata ~0.043R, fark bunun onda biri. Anlamli olan dusus azalmasi
(21.7% -> 18.6%), o da stop genisletmesinden geliyor. Yani ogrenme katmani
mucize yaratmiyor; **zarar vermiyor ve bir failure mode'u (cok dar stop)
sistematik olarak duzeltiyor.** Asil degeri backtest'in gosteremedigi seyde:
gercekten bozulan bir sembolu/rejimi zamaninda kenara koymak.

### Ne ogrendigini gormek

```bash
python -m bot --config config.yaml learn
```

```
Ogrenme: AKTIF
Toplam islem: 62 | zirve equity: 241.30
Canli beklenti: +0.074R  (+/- 0.312 95% guven)
Kucultulmus tahmin: +0.096R (onsel +0.110R)

Kovalar:
  kova                            n    ort R  ust sinir  durum
  sym:ETHUSDT                    18   -0.204        inf  veri yetersiz (12 eksik)
  reg:adx_dusuk|vol_yuksek       31   -0.118     +0.240  aktif
  sym:BTCUSDT                    22   +0.310        inf  veri yetersiz (8 eksik)
```

Kara kutu yok: hangi kovada kac ornek var, ust guven siniri nerede, neden
karar alinmadi -- hepsi gorunur.

---

## Kendini denetleme: "her sey yolunda mi?"

Botu calistiran kisinin istatistik bilmesi gerekmiyor. Tek komut:

```bash
python -m bot doctor
```

Uc cevaptan biri gelir, hepsi duz Turkce:

| Cikti | Ne yapilacak |
|---|---|
| `HER SEY YOLUNDA` | Hicbir sey |
| `DIKKAT` | Yine hicbir sey, sadece haberin olsun |
| `MUDAHALE GEREKIYOR` | Ekrandaki `YAP:` satirini uygula |

Ornek -- strateji gercekten bozuldugunda:

```
================================================================
MUDAHALE GEREKIYOR - asagidaki adimi at
================================================================

 >>>  Strateji hala calisiyor mu
        60 islemde ortalama -0.640R ve ust guven siniri -0.482 < 0.
        Beklentinin negatif oldugu istatistiksel olarak KANITLANDI.
        Bu kotu bir seri degil, bozulmus bir sistem.
        YAP: Bot yeni pozisyon acmayi durdurdu. Once paper moda gec,
             sonra backtest'i guncel veriyle tekrar calistir.
```

### Tek yonlu kural

Kanit stratejinin bozuldugunu gosterirse bot **yeni pozisyon acmayi durdurur
ve haber verir.** Parametrelerini kendiliginden degistirmez.

Bu kasitli. Kendini yeniden optimize eden bir sistem **bozuldugunu asla
soylemez** -- cunku her seferinde gecmise uyan yeni bir parametre bulur.
Bozulmayi gizlemenin en kolay yolu, surekli yeniden fit etmektir.

Kontroller ve esikleri:

| Kontrol | Ne zaman uyarir | Neden bu esik |
|---|---|---|
| Bot calisiyor mu | Son kayit beklenenden eskiyse | surec olmus olabilir |
| Strateji calisiyor mu | 50+ islem **ve** %99 anlamlilikla negatifse | 12 ardisik zarar bunu tetiklemez, tetiklememeli |
| Dusus | %15 uyari, %22 mudahale | backtest'te en kotu dusus %21 idi |
| Komisyon yuku | Brut karin %40'indan fazlasi komisyonsa | cok sik islem isareti |
| Acik pozisyonlar | Limit asilmis ya da pozisyon takilmissa | borsa-yerel kayit uyusmazligi |
| Operasyonel hatalar | Tekrarlayan emir/bakiye hatasi varsa | genelde bakiye kucuklugu |

Bot ayrica saatte bir kendi kendine ayni kontrolu yapar; kritik bulguda
Telegram'dan haber verir (ayni konudan gunde en fazla bir kez -- bildirim
spami kotu kararlarin kaynagidir).

**Programlama/istatistik bilmiyorsan:** `BASLA.md` dosyasi senin icin yazildi.

---

## Kurulum

```bash
git clone <repo> && cd edith
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env          # API anahtarlarini buraya (LIVE icin)
```

API anahtari kurallari (canli mod icin):
- Sadece **Futures** izni ac. **Withdraw iznini asla acma.**
- IP whitelist tanimla.
- Anahtarlar sadece ortam degiskeninden okunur, `config.yaml`'a yazilmaz.

## Kullanim

```bash
python -m bot check                         # config + baglanti + risk matematigi
python -m bot backtest --portfolio          # gercekci portfoy backtesti (onerilen)
python -m bot backtest --symbol BNBUSDT     # tek sembol, detayli
python -m bot paper                         # gercek fiyat, sahte para
python -m bot testnet                       # Binance testnet, gercek emir akisi
python -m bot live                          # GERCEK PARA ('ANLADIM' yazmani ister)
python -m bot status                        # kayitli istatistikler
```

Veri kaynagi: `--source archive` (varsayilan, data.binance.vision, 5+ yil
gecmis, bolge kisiti yok) veya `--source api` (REST, son N gun).

### Onerilen sira -- atlama

1. `python -m bot backtest --portfolio` -- sayilari kendin gor
2. `python -m bot paper` -- **en az 2 hafta**, canli fiyatla
3. `python -m bot testnet` -- emir akisinin gercekten calistigini gor
4. `python -m bot live` -- kaybetmeyi goze aldigin bir miktarla basla

Paper'da kar etmeyen bir kurulum canlida asla kar etmez.

---

## Guvenlik katmanlari

| Katman | Ne yapar |
|--------|----------|
| Pozisyon boyutu | Her islemde risk = equity'nin sabit yuzdesi (varsayilan %0.75) |
| Kaldirac tavani | Stop, likidasyon mesafesinin %60'ini gecerse kaldirac otomatik duser |
| Borsada duran stop | `STOP_MARKET closePosition=true` -- bot cokse de stop aktif kalir |
| Koruma emri garantisi | Koruma emri konulamazsa pozisyon aninda kapatilir, ciplak tasinmaz |
| Gunluk zarar limiti | -%4'te bot kendini kapatir (UTC gunu), restart'ta da hatirlar |
| Gunluk kar hedefi | +%6'da durur. Kazandiktan sonra devam etmek pahali bir aliskanlik |
| Soguma | Her zarardan sonra 20 dk, ust uste 3 zarardan sonra 4 saat |
| Spread filtresi | Spread 6 bps'i gecerse islem yok |
| Funding filtresi | Funding saatine 10 dk kala, oran yuksekse islem yok |
| Mutabakat | Her dongude borsadaki gercek pozisyonla yerel kayit karsilastirilir |
| Kalici durum | SQLite: limitler ve acik pozisyon restart'ta kaybolmaz |
| Config dogrulama | Sinir disi ayar uyari degil **hata** verir, bot baslamaz |
| Ogrenme tek yonlu | Ogrenme riski asla artiramaz (`max_risk_multiplier: 1.0`) |
| Kendini denetleme | Edge'in oldugu KANITLANIRSA bot durur ve haber verir |
| Hata defteri | Tekrarlayan operasyonel hata sembolu gecici devre disi birakir |

Bot **kendisine ait olmayan** acik pozisyonlara dokunmaz.

---

## Mimari

```
bot/
  config.py       ayarlar + sert dogrulama (R:R, kaldirac, limitler)
  indicators.py   saf Python EMA/RSI/ATR/ADX (numpy bagimliligi yok)
  strategy.py     sinyal uretimi + pozisyon yonetim kurallari
  risk.py         pozisyon boyutu, gunluk limitler, soguma
  backtest.py     tek sembol + portfoy backtesti
  engine.py       canli/paper dongu (broker arayuzune karsi calisir)
  learning.py     ogrenme katmani (istatistiksel kapilar + hata defteri)
  health.py       kendini denetleme (bozulunca DUR ve haber ver)
  state.py        SQLite kalici durum
  archive.py      data.binance.vision gecmis veri
  exchange/
    binance.py    USD-M Futures REST (imzalama, retry, suzgecler)
    paper.py      kagit broker (gercek fiyat, sahte para)
    live.py       canli/testnet broker (borsada duran koruma emirleri)
```

Motor hangi ortamda oldugunu bilmez; `Broker` arayuzune karsi calisir.
Backtest, paper ve live **ayni strateji ve ayni risk kodunu** kullanir --
yoksa backtest yalan soyler.

## Strateji: trend + geri cekilme

Uc filtre ayni anda uymadan pozisyon acilmaz:

1. **Yapisal trend** -- EMA50 > EMA200 ve fiyat EMA200 ustunde (long icin)
2. **Trend gucu** -- ADX >= 22 ve +DI > -DI (yatay piyasada islem yok)
3. **Volatilite bandi** -- ATR/fiyat %0.30 ile %2.5 arasi

Giris tetigi trendin kendisi degil, trend icindeki geri cekilme sonrasi devam
sinyali. Cikis: %50'si 1R'de (islem bedava hale gelir), kalani 4R'de veya
3 ATR iz suren stopta.

## Testler

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q      # 133 test
```

Testler sadece "calisiyor mu"yu degil, **kaybetmeyi reddediyor mu**yu de
kontrol eder: lookahead yok mu, ayni mumda stop hedeften once mi geliyor,
gunluk limit gercekten duruyor mu, kaldirac riski degistirmiyor mu.

Ogrenme katmaninda en kritik testler "ogreniyor mu" degil **erken ogrenmiyor
mu**: 29 ardisik zarar bile esigin altindaysa karar aldirmamali, yuksek
varyansli zarar serisi bank tetiklememeli, yanlis bank orani simulasyonla
%3'un altinda kalmali.

## Bilinen sinirlar

- Tek yonlu pozisyon modu (hedge mode desteklenmiyor).
- WebSocket yok; REST polling (4h icin fazlasiyla yeterli, 1m icin degil).
- Kismi doldurma senaryolari basitlestirilmis.
- Funding maliyeti backtest'te sabit varsayiliyor.
- Emir turu MARKET (taker). Maker emirleri komisyonu dusururdu ama
  doldurulmama riski getirir.
