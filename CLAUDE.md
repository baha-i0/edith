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
| Riski artirmak | Tepe noktasi var: %5'ten sonra getiri DUSERKEN dusus artiyor. Ustelik tepe SABIT DEGIL (ilk yari %10 riskle +%181, ikinci yari -%0.2). |
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

Siradaki adim:
- [ ] `python -m bot paper` -- EN AZ 2 HAFTA. Bu adim atlanmamali.
- [ ] Panel: http://127.0.0.1:8787
- [ ] Telegram (istege bagli): @BotFather + @userinfobot -> .env
- [ ] Servis kurulumu: `python -m bot install --mode paper`
- [ ] 2 hafta sonra sonuc makulse canli

**UYARI:** `--months` varsayilani 12'dir. Kullanici bir kez 12 aylik
backtest calistirip -%4.7 gordu ve kafasi karisti. 5 yillik rakamlar icin
`--months 60` sart.

**OneDrive notu:** repo OneDrive senkronize klasorde. Bot 7/24 calismaya
baslamadan once `data/` ve `logs/` OneDrive'dan haric tutulmali -- SQLite'a
saniyede onlarca yazma var, OneDrive dosya kilidi cakismasi yaratabilir.

## Yapilmamis / acik konular

- Kullanici botu baska bir yapay zekaya inceletmek istiyor. Brief hazir
  (repo linki + "once README'deki denetim bolumlerini oku, kodu
  CALISTIRARAK dogrula, sadece okuyup yorum yapma").
- Ikinci AI'dan gelecek bulgular Claude tarafindan dogrulanacak -- "farkli
  model buldu" her zaman "dogru" demek degil.
- Sermaye tabani config'de KAPALI (`capital_floor_usdt: 0`). Kullanici
  170$ taban + %70 cirpinan istemisti ama once kagit modunda %14 dususu
  gormesi kararlastirildi.
