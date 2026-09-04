# Baslangic Rehberi

Bu dosya, istatistik veya programlama bilmeden botu calistirmak icin yazildi.

---

## 0. Once sunu bil

**Bot para basmaz.** Yillik ~%12 beklentisi var ve yolda **%20 dusus** goreceksin.

$200 ile:

| | tutar |
|---|---|
| Beklenen yillik getiri | ~$25 |
| Normal karsilanan en kotu dusus | ~-$42 (hesap $158'e iner) |
| Bunun kalici olma ihtimali | var |

Hesap $158'e indiginde botu kapatirsan, kaybi kalici hale getirmis olursun.
Backtest'te bu dususler defalarca yasandi ve her seferinde toparlandi -- ama
bu gelecekte de toparlanacagini garanti etmez.

**Kaybetmeyi goze alamayacagin parayi koyma.**

---

## 1. Kurulum (bir kez)

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

## 2. Kagit modda calistir (en az 2 hafta, atlama)

```bash
python -m bot paper
```

Gercek fiyatlar, sahte para. Terminali kapatma -- kapatirsan bot durur.
Arka planda calistirmak icin:

```bash
nohup python -m bot paper > /dev/null 2>&1 &
```

## 3. Gunde bir kez tek komut

```bash
python -m bot doctor
```

Bu komut sana duz Turkce soyler. Uc cevaptan biri gelir:

| Cikti | Ne yapacaksin |
|---|---|
| `HER SEY YOLUNDA` | Hicbir sey. Kapat, git. |
| `DIKKAT` | Yine hicbir sey. Sadece haberin olsun diye. |
| `MUDAHALE GEREKIYOR` | Ekranda yazan `YAP:` satirini uygula. |

Baska hicbir sey yapmana gerek yok. Log okumana, grafik incelemene,
parametre ayarlamana gerek yok.

## 4. Telefona bildirim (opsiyonel ama onerilir)

`.env` dosyasina Telegram bilgilerini yaz. Bot sadece **gercekten onemli**
seyleri gonderir (gunde en fazla bir kez ayni konudan). Kar/zarar bildirimi
icin surekli telefona bakma -- o, kotu kararlarin kaynagidir.

## 5. Canliya gecmek

Kagitta 2 hafta sorunsuz calistiysa:

```bash
python -m bot testnet     # once sahte borsada gercek emir akisi
python -m bot live        # sonra gercek para ('ANLADIM' yazmani ister)
```

API anahtari kurallari:
- Sadece **Futures** izni ac
- **Withdraw iznini ASLA acma** (anahtarin calinsa bile paran cekilemez)
- IP whitelist tanimla

---

## Sik sorulanlar

**"Bot 5 islemde ust uste kaybetti, bir sorun mu var?"**
Hayir. Isabet orani %46. Ust uste 8 kayip bile %93 ihtimalle 5 yil icinde
gorulecek normal bir olay. Bot bu seride bilerek hicbir sey yapmaz.

**"Hesap %15 dustu, kapatayim mi?"**
Hayir. Backtest'te en kotu dusus %21 idi. Bu beklenen aralikta.
`doctor` sana "MUDAHALE GEREKIYOR" demediyse bir sey yapma.

**"Bot durdu, ne oldu?"**
`python -m bot doctor` calistir. Uc sebepten biri olur:
1. Gunluk zarar limiti (-%4) -- yarin kendiliginden acilir, bir sey yapma
2. Gunluk kar hedefi (+%6) -- ayni sekilde
3. Saglik kontrolu strateji bozuldu dedi -- ekranda yazani yap

**"Daha cok kazanmak icin kaldiraci artirsam?"**
Kaldirac getiriyi artirmaz, sadece marji dusurur. Pozisyon boyutunu stop
mesafesi belirliyor. Getiriyi artirmak istersen `risk_per_trade_pct`'i
yukselt -- ama dusus de ayni oranda buyur. %0.75 -> %1.5 yaparsan yillik
beklenti ~%24'e cikar, dusus ~%40'a. Hesabi yariya inerken izleyebilir misin?

**"Bot kendini gelistirir mi?"**
Sinirli ve kontrollu sekilde: stop mesafesini kalibre eder, bozulan sembolu
kenara koyar, tekrarlayan operasyonel hatayi ogrenir. Ama stratejisini
kendiliginden degistirmez. Bunu test ettim: serbest birakildiginda beklenti
+0.088R'den +0.041R'ye dusuyor. Kendini surekli optimize eden bir sistem,
gecmise uyum saglar, gelecege degil.

**"Bir sey bozulursa haberim olur mu?"**
`doctor` her calistirmada bakar; bot kendisi de saatte bir kontrol eder.
Kanit stratejinin bozuldugunu gosterirse **kendini durdurur** ve Telegram
kuruluysa sana yazar.

---

## Neyi asla yapma

1. Zararin ortasinda riski artirma ("kaybi kapatayim")
2. Kar ederken riski artirma ("nasil olsa calisiyor")
3. Dususte botu kapatip yukseliste geri acma
4. Anlamadigin bir parametreyi degistirme
5. Kaybetmeyi goze alamayacagin parayi koyma

Bu bes maddenin hepsi, botun matematigini bozar. Botun en buyuk avantaji
zeki olmasi degil, **duygusuz olmasi.** O avantaji elinden alma.
