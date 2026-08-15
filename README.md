# EDITH — Reddit Growth & Networking Assistant

Zihinsel Güç için Reddit büyüme asistanı. İlgili toplulukları tarar, katkı
sağlayacak konuşmaları bulur, Baha'nın ağzından taslak yazar — ve **hiçbir şeyi
onay almadan yayınlamaz.**

> ⚠️ Bu klasör şu an `zihinsel-guc-app` reposunun içinde duruyor ama **tamamen
> bağımsız bir proje.** Uygulama koduna hiç dokunmuyor. Kendi reposuna taşımak
> için `edith/` klasörünü olduğu gibi kopyalaman yeterli.
>
> **Taşımadan GitHub Actions çalışmaz:** GitHub yalnızca repo **kökündeki**
> `.github/workflows/` klasörünü okur. Şu an dosya `edith/.github/workflows/`
> içinde, yani otomatik tarama ancak ayrı repoya taşındıktan sonra devreye girer.
> O zamana kadar taramayı elle çalıştırabilirsin (`npm run scan`).

## Ne yapar

```
Discover → Filter → Analyze → Suggest → [SEN ONAYLA] → Publish
```

1. **Tarama** (`npm run scan`, sonra 3 saatte bir otomatik): hedef subreddit'lerdeki
   taze gönderileri çeker.
2. **Ucuz filtre**: eski / NSFW / çok kısa / konu dışı / zaten görülmüş olanlar
   AI'ya *gitmeden* elenir. Maliyet freni.
3. **AI analizi**: kalanları puanlar (alaka, ağ, çekim gücü, spam riski) ve bir
   aksiyon önerir — `ENGAGE` · `NETWORK` · `APP_SHARE` · `SKIP`.
4. **Kurallar motoru**: topluluk self-promo yasaklıyorsa `APP_SHARE` AI ne
   önerirse önersin **bloklanır**.
5. **Dashboard**: onay kuyruğu. Sen onaylayana kadar Reddit'e hiçbir şey gitmez.

## Kurulum

### 1. Supabase (yeni ve boş bir proje)

Uygulamanın Supabase projesini **kullanma** — bu ayrı bir veritabanı olmalı.

1. supabase.com → yeni proje
2. SQL Editor → `supabase/schema.sql` içeriğini yapıştır → Run
3. Settings → API → `Project URL` ve `service_role` anahtarını not al

### 2. Reddit uygulaması

1. reddit.com/prefs/apps → "create another app"
2. Tür: **script**
3. redirect uri: `http://localhost:3000` (kullanılmıyor ama zorunlu)
4. client id (başlıktaki kısa kod) ve secret'ı not al

### 3. AI anahtarı

DeepSeek (ya da OpenRouter/OpenCode üzerinden herhangi bir OpenAI-uyumlu
sağlayıcı). `AI_BASE_URL` ve `AI_MODEL` ile sağlayıcı değiştirilebilir —
kod değişmez.

### 4. Ortam değişkenleri

`.env.example`'ı `.env`e kopyala ve doldur.

```bash
cp .env.example .env
npm install
npm run dev
```

### 5. Vercel'e dağıtım

1. Repoyu Vercel'e bağla (kök dizin: `edith` ise "Root Directory" ayarını ona ver)
2. `.env`deki tüm değişkenleri Vercel → Settings → Environment Variables'a gir
3. Deploy

### 6. GitHub Actions (ayrı repoya taşıdıktan sonra)

Repo → Settings → Secrets and variables → Actions → aynı değişkenleri **Secrets**
olarak ekle. Sonra Actions sekmesinden "EDITH — fırsat taraması" workflow'unu
elle bir kez çalıştırıp doğrula.

### 7. Reddit profil hunisi (elle, ama en önemli adım)

Asıl tanıtım yorumlarda değil **profilde** yapılır: yorum değer verir, profil
satar. Reddit profilinin bio'suna uygulamayı ve kim olduğunu yaz, bir de
pinned post ekle. Bu sayede yorumlara link sıkıştırmak gerekmez — merak eden
zaten profile bakar.

## Komutlar

```bash
npm run dev        # dashboard (localhost:3000)
npm run scan       # taramayı elle çalıştır
npm run typecheck  # tsc --noEmit
npm run build      # üretim derlemesi (lint dahil)
```

## Güvenlik kararları

Bunlar bilinçli ve gevşetilmemeli:

| Katman | Ne yapar |
|--------|----------|
| **Kurallar motoru** | Topluluk self-promo yasaklıyorsa `APP_SHARE` bloklanır. Kurallar okunamadıysa da bloklanır — bilinmezlik izin değildir. |
| **Kimlik koruması** | EDITH Baha'nın sahip olmadığı rakamı/deneyimi **uyduramaz**. İhtiyaç duyarsa `missing_info` ile sana sorar, taslak yazmaz. |
| **Hız limiti** | Onaydan *sonra* çalışır. Topluluk başına haftalık `APP_SHARE`, günlük toplam yayın tavanı. Onay, limiti aşma yetkisi vermez. |
| **Uzunluk bütçesi** | Yorum 80, gönderi 150 kelime. Kodda zorlanır, prompt temennisi değil — model serbest bırakılırsa hep uzatır. |
| **Tek yanıt** | Aynı thread'e ikinci kez yazılmaz. |
| **service_role izolasyonu** | Supabase anahtarı yalnızca sunucuda. Tarayıcı Supabase'e hiç bağlanmaz; her tabloda RLS açık, politika yok. |

## Skill'ler

`skills/` altındaki markdown dosyaları EDITH'in uzmanlığı. Konuya göre prompt'a
yüklenirler (hepsi birden değil — token maliyeti).

| Dosya | Ne tanımlar |
|-------|-------------|
| `kimlik.md` | Baha kim, nasıl yazar, **neyin asla uydurulmayacağı** |
| `cekim-gucu.md` | Fark edilen yorum nasıl yazılır + uzunluk bütçesi + yasak dolgu kalıpları |
| `reddit-etiketi.md` | 9:1 dengesi, hesap gerçekleri, aksiyon seçimi |
| `zihinsel-guc-marka.md` | Ton kanonu (sert-gerçekçi, klişe motivasyon yok) |
| `urun-gercekleri.md` | Uygulamada NE VAR — olmayan özellik anlatılmasın diye |
| `dijital-pazarlama.md` | Kalıcı pazarlama kuralları ("0 ödeyen" istatistik kuralı dahil) |

Bunlar düz metin — istediğin an düzenleyebilirsin, kod değişmez.

## Sonraki fazlar

MVP (Faz 1-5) tamam. Sıradakiler:

- **Faz 6** — kurallar motoru + authenticity sertleştirme
- **Faz 7** — kalıcı bellek (`lessons`/`facts` prompt enjeksiyonu) + `/hafiza` onay ekranı
- **Faz 8** — `/sohbet`: EDITH ile konuşma, ona kural öğretme
- **Faz 9** — `monitor.yml`: yayınlananların akıbeti (upvote / yanıt / mod silmesi)
- **Faz 10** — `learn.yml`: haftalık otomatik ders önerisi + analytics + CRM

Şema, `lib/memory.ts` ve tablolar bu fazlar için hazır — eksik olan arayüz ve
zamanlanmış işler.
