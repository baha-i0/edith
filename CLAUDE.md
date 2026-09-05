# EDITH - proje hafizasi

Bu dosya, yeni bir oturumun sifirdan baslamamasi icin var. Once bunu oku,
sonra README.md'yi. Kod hakkinda bir sey degistirmeden once "Olculdu ve
REDDEDILDI" bolumunu oku -- oradaki fikirler zaten denendi.

---

## Proje nedir

Binance USDⓈ-M vadeli islem botu. Tam otonom: hangi sembol, hangi yon, ne
kadar, ne zaman kapatilacak -- hepsine kendisi karar verir. Sahibi kurar,
sonra dokunmaz.

**Calisma zamani bagimliligi sadece `requests` + `PyYAML`.** LLM cagrisi
YOK. Bot calisirken Claude API'sine ya da baska bir yapay zekaya hicbir
istek gitmez; "ogrenme katmani" istatistiktir (Bayesci kucultme, guven
araliklari), model degil. Kullanici bunu bir kez sordu, cevap net: bot
calismasi sifir API maliyeti.

## Kullanici

Baha (bahaincesu@gmail.com), mekatronik muhendisligi ogrencisi, Turkce
konusuyor. Trading bilgisi yok, muhendislik mantigi guclu.

- Sermaye: 200-300 USDT. Gunde 20-50$ kazanmak istiyor.
- **Bu matematiksel olarak imkansiz ve kendisine sayilarla gosterildi.**
  Gunde 20$ = yilda 7.300$; %19 getiri orani ile ~37.800$ sermaye gerekir.
  Tekrar sordugunda ayni hesabi tekrar goster, "belki olur" deme.
- Tam otonomi istiyor: "ben sadece gozlemciyim ve patronum."
- Dogrudan, kanita dayali cevap istiyor. Korukorune onay ISTEMIYOR.
  Varsayimlarina itiraz edilmesini acikca talep etti.

## Sevk edilen rakamlar (kullanici kendi makinesinde DOGRULADI)

15 sembol, 4h, 5 yil, gercek komisyon + funding + slipaj:

```
Yillik bilesik : +13.4%      (benim olcumum +14.0%, veri penceresi farki)
Islem          : 354  (72/yil)
Isabet / PF    : %55.1 / 1.59
Beklenti       : +0.287 R/islem
Maks. dusus    : %14.1
t-degeri       : 4.39   (korelasyon icin DUZELTILMEMIS; gercek deger ~1.5-2)
200$ -> 371.99$
```

**Son 1 yil AYRI bir hikaye: -%4.7 yillik, 83 islem, t=-0.26.**
Bu gurultu olabilir de edge asinmasi olabilir de; ayirt edecek veri yok ve
oldugunu iddia etmek yalan olur. Kullaniciya boyle soylendi.

## Olculdu ve REDDEDILDI (tekrar onerme)

Bunlarin hepsi denendi, veriyle olculdu ve reddedildi. Yeniden "iyi fikir"
diye sunmak zaman kaybi:

| fikir | neden reddedildi |
|-------|------------------|
| Kisa zaman dilimi (5m/15m/1h) | 15m'de yilda 734 islem -%30.3; 4h'te 57 islem +%5.0. Komisyon dar stopu yiyor. |
| Ayni yonde pozisyon sinirlamak | `max_same_direction=1` yilligi %10.9'dan %4.3'e dusurdu. Es zamanli pozisyonlar EN IYI islemler. |
| Riski artirmak (TABAN ACIKKEN) | Taban 170$ iken risk %4'te islem sayisi 355'ten 11'e cokuyor: yastik tukeniyor, bot kendini durduruyor. |
| ~~Riski artirmak (genel)~~ | **2026-09-05'te KISMEN CURUTULDU.** Taban kapaliyken %0.75 -> %1.5 her iki yarida da pozitif. Asagidaki bolume bak. |
| Hedefi kucultup isabeti yukseltmek | tp1 1.0->0.7, tp2 4.0->3.0: isabet %54.5->%52.9 DUSTU, yillik %14.8->%10.0. |
| Ogrenmeyi hizlandirmak | Esikleri 5 isleme dusurup onseli kaldirinca beklenti +0.088R->+0.041R, t 2.15->0.93. |
| post_only "dolmazsa vazgec" | Tam donemde daha parlak (+%16.3) ama ZAMAN BOLMESINI GECEMEDI; kazanci komisyondan degil sanstan geliyordu. |
| Rakip bot sartnamesi (RSI35+EMA200, 15m, 1.5xATR) | Birebir kuruldu: -0.339R/islem, t=-7.39. Ters cevrilince de kaybediyor. Komisyon sifirlaninca -0.069R (t=-1.46) -> kurallarin edge'i YOK. |

## Kabul edilen ve neden

| ozellik | olculen etki |
|---------|--------------|
| Genislik filtresi (min_breadth=4) | isabet %45.4->%54.5, dusus %18.6->%14.1, beklenti 3x. En buyuk tek iyilestirme. |
| post_only + market'e dusus | komisyon %28 azaldi, iki zaman diliminde de pozitif (aritmetikten geliyor, sanstan degil). |
| Sermaye tabani (CPPI) | Kaybeden strateji stresi: tabansiz 300$->69$, tabanli 300$->195$. |
| Cirpinan taban (%70) | Ikinci yarida hem daha yuksek getiri hem daha dusuk dusus. |

## Degistirmeden once

`config.example.yaml`'daki HER sayinin yaninda neden o secildigi yaziyor.
Bir degeri degistirmeden once o yorumu oku. Degisiklik oneriyorsan
`run_portfolio_backtest` ile 5 yil gercek veride OLC -- mantik yurutme.

Veri: `bot/archive.py` -> data.binance.vision (bolge kisiti yok).
`fetch_archive(sembol, "4h", months=60)`.

## Calisma ilkeleri (bu projede kanitlanmis)

1. **Olculemeyen ozellik ozellik degildir.** Her iddia backtest'le
   dogrulanir.
2. **Backtest/canli paritesi kutsaldir.** Bir ozellik canliya eklendiyse
   backtest'e de eklenmeli. Bu kural iki kez ihlal edildi ve iki kez hata
   uretti (genislik filtresi, update_floor sikligi).
3. **Bilinmeyen aleyhte varsayilir.** Ayni mumda stop hedeften once; giris
   sonraki mumun acilisinda; funding yonden bagimsiz maliyet.
4. **Kayan pencere > omurluk ortalama** karar mekanizmalarinda.
5. Kullaniciya rakam verirken **guven araligini da ver.** t-degeri tek
   basina yaniltici (korelasyon duzeltmesi yok).

## Denetim gecmisi

Iki bagimsiz denetim turu yapildi, README sonunda detaylari var.

- 1. tur: 7 bulgu. Cogu ozelliklerin KESISIMINDE (post_only + Telegram,
  post_only + golge modu, taban + ogrenme katmani).
- 2. tur: 13 bulgu, 11'i gercek. Cogu HIC TEST EDILMEMIS kod yollarinda
  (canli broker, saglik kontrolleri, raporlama). `tests/test_reconcile.py`
  o turda yazildi -- canli broker daha once hic test edilmiyordu.
- Guvenlik taramasi: esigi gecen bulgu yok. Iki sertlestirme yine de
  yapildi (DNS rebinding icin Host dogrulamasi, token'in log'a yazilmamasi).

**254 test geciyor.** Degisiklikten sonra `python -m pytest tests/ -q`.

## 2026-09-05 olcumleri (risk ve cikis seviyeleri)

Hepsi 15 sembol / 4h / 60 ay / gercek komisyon+funding+slipaj, ve
zaman bolmesi (ilk 30 ay + son 30 ay) ile dogrulandi.

### Risk yuzdesi: %0.75 -> %1.5 KABUL EDILDI

| risk | 60 ay CAGR | 200$ -> | maks dusus | ilk yari | ikinci yari |
|------|-----------|---------|-----------|----------|-------------|
| %0.75 | +13.4% | 372$ | %14.1 | +16.9% | +7.5% |
| **%1.5** | **+29.1%** | **706$** | **%26.7** | +36.0% | +17.8% |
| %2.0 | +40.2% | 1064$ | %34.8 | +53.2% | +23.1% |

`config.yaml` %1.5'e cekildi. Her iki yarida da pozitif oldugu icin
projenin kendi kabul sinavini geciyor.

### SERT TAVAN: taban kapaliyken risk %2.0'de kirpiliyor

`bot/risk.py:192` -> `yuzde = min(risk_per_trade_pct, 6.0 if floor > 0 else 2.0)`

Bu yuzden %2.5, %4, %5, %10 hepsi BIREBIR ayni sonucu verir. Hipotez
test edildi: %2.1 ile %10 kurusuna kadar ayni, %1.9 farkli. Config
dogrulamasi da taban kapaliyken %2 ustunu zaten reddediyor -- gizli
hata degil, bilincli emniyet. Ileride "risk %5 deneyelim" diyen olursa
once bunu oku.

### Sabit yuzdeli cikis (+%1.6 / -%1.2) -- OLCULDU, KARAR TESTNET'E BIRAKILDI

Kullanicinin fikri. ATR yerine sabit yuzde stop/hedef. Backtest'te
mevcut sistemi YENIYOR ve iki yariyi da geciyor:

| | mevcut (ATR) | sabit %1.6/%1.2 |
|---|---|---|
| 60 ay CAGR | +29.1% | **+48.5%** |
| ilk yari | +36.0% | +45.2% |
| ikinci yari | +17.8% | +52.1% |
| islem | 353 | 660 |
| maks dusus | %26.7 | %35.9 |

Reddetmeden once bilinmesi gerekenler:

- Not: `min_reward_risk: 1.5` bu fikri normalde bastan reddediyor
  (1.6/1.2 = 1.33). Olcum icin kapi gecici acildi.
- **Saglamlik izgarasi:** stop %1.0-%2.0 ve R:R 1.33-2.0 araligi genis
  bir pozitif bolge -- tek sayida parlamiyor, yani overfit degil.
  AMA stop %0.8'de -%9.6'ya donuyor; komisyon duvari cok yakinda.
- **Slipaj olumcul:** 2bps +48.5% / 5bps +44.5% / **10bps +24.3%** /
  20bps hic islem yok. `check` ciktisinda ATOMUSDT spread'i 6.49 bps --
  alt coinlerde 10 bps gercekci. Avantajin yarisi orada gidiyor.
- **Son 12 ay: -%6.5** (mevcut sistem -%4.7). Bozulmayi DUZELTMIYOR.
- Yapisal itiraz: sabit yuzde volatiliteye uyum saglamaz, ATR saglar.
- Ogrenme katmani testte "stop avlanmasi" tespit edip stoplari 1.15x
  genisletti -- dar stopun avlandiginin isareti.

Sonuc: fikir ciddi ama tam da backtest'in cevaplayamayacagi yerde
duruyor (gercek dolum kalitesi). 4h mumda %1.2 stop ve %1.6 hedef
genelde AYNI mumun icinde; backtest bunu kotumser kuralla cozuyor ama
gercekte ne oldugunu bilmiyor. Karar testnet olcumune birakildi.

Deney betikleri (repoya girmedi, scratchpad'de): risk taramasi,
saglamlik izgarasi, sabit hedef karsilastirmasi.

## Kullanici su an nerede

Windows 11, PowerShell. Repo klonlandi:
`C:\Users\bahai\OneDrive\Masaüstü\Apps\EDITH`

Tamamlanan adimlar:
- [x] git clone + dogru dal
- [x] pip install -r requirements.txt (Python 3.11)
- [x] config.yaml ve .env olusturuldu (.env BOS, kagit modu anahtar istemiyor)
- [x] `python -m bot check` -- gecti, **canli Binance baglantisi calisti**
      (benim sandbox'imda cografi engel vardi, kullanicinin baglantisinda yok)
- [x] `python -m bot backtest --portfolio --months 60` -- rakamlar dogrulandi

- [x] Veri yollari OneDrive DISINA tasindi (asagiya bak)
- [x] `risk_per_trade_pct` %0.75 -> %1.5
- [x] Panele komut kilavuzu eklendi (terminal + Telegram, duz Turkce)
- [x] Telegram: token + chat_id .env'de, token dogrulandi (@EDITHTraderBot)

Siradaki adim:
- [ ] Kullanici @EDITHTraderBot'a /start basacak -- bot ilk mesaji
      kullanici yazmadan gonderemez ("chat not found" alindi)
- [ ] Testnet anahtarlari: testnet.binancefuture.com AYRI bir sistem,
      gercek Binance hesabi orada gecerli DEGIL
- [ ] Testnet oturumu: 2 hafta kagit yerine birkac saatte tesisati
      zorlama (kapat/ac, kismi dolum, reconcile) + sabit yuzdeli cikis
      fikrini gercek emir defterinde sinama
- [ ] Kagit modu birkac gun (`python -m bot paper`)
- [ ] Panel: http://127.0.0.1:8787

Kullanici REDDETTI:
- Windows servisi olarak kurulum ("istemiyorum")
- 2 haftalik kagit modu ("mantikli gelmedi, hizlandiralim")

**UYARI:** `--months` varsayilani 12'dir. Kullanici bir kez 12 aylik
backtest calistirip -%4.7 gordu ve kafasi karisti. 5 yillik rakamlar icin
`--months 60` sart.

**OneDrive notu -- COZULDU (2026-09-05):** repo hala OneDrive'da ama
canli yazilan dosyalar disari tasindi:

    state_path: C:/Users/bahai/EDITH-veri/bot.db
    log_path:   C:/Users/bahai/EDITH-veri/logs/bot.log

OneDrive ayarlarindan klasor haric tutmak yerine bu yol secildi (o
yontem kirilgan). Sorunun gercek oldugu kaniti: eski `data/bot.db`
dosyasini silmek istedigimde OneDrive dosyayi KILITLI tutuyordu.
`data/archive` (12 MB backtest onbellegi) bilerek OneDrive'da kaldi --
oraya sadece backtest sirasinda yazilir.

## Yapilmamis / acik konular

- Kullanici botu baska bir yapay zekaya inceletmek istiyor. Brief hazir
  (repo linki + "once README'deki denetim bolumlerini oku, kodu
  CALISTIRARAK dogrula, sadece okuyup yorum yapma").
- Ikinci AI'dan gelecek bulgular Claude tarafindan dogrulanacak -- "farkli
  model buldu" her zaman "dogru" demek degil.
- Sermaye tabani config'de KAPALI (`capital_floor_usdt: 0`). Kullanici
  170$ taban + %70 cirpinan istemisti ama once kagit modunda %14 dususu
  gormesi kararlastirildi.
