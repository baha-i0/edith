# CLAUDE.md — EDITH

Reddit Growth & Networking Assistant. Zihinsel Güç'ü tanıtan, insan onaylı bir
asistan. Her oturumda oku.

## Durum (2026-08-16)

Kod: Faz 1-5 (MVP) tamamlandı. Faz 6'nın çekirdeği (kurallar motoru + kimlik
koruması) MVP içinde erken geldi. Faz 7-10 (kalıcı bellek arayüzü, /sohbet,
izleme, öğrenme) hiç başlanmadı.

Kurulum: Supabase projesi kuruldu, şema çalıştırıldı (8 tablo). Yerel .env
dolduruldu (Supabase URL + Secret key + dashboard şifresi + session secret).
Reddit app ve AI anahtarı HENÜZ YOK.

## Sıradaki adımlar (sırayla)

1. Reddit script app oluştur (reddit.com/prefs/apps) -> client id + secret
2. DeepSeek API key al (platform.deepseek.com)
3. .env'e REDDIT_* ve AI_API_KEY satirlarini doldur
4. npm run dev ile yerelde dashboard'u test et, giris yap
5. Vercel'e deploy et (env degiskenlerini Vercel'e de gir)
6. GitHub repo Secrets'a ayni degiskenleri gir, scan.yml'i elle bir kez tetikle
7. Reddit profil hunisi: bio + pinned post
8. zihinsel-guc-app reposunda claude/reddit-automation-dashboard-bcn2v8 dalini sil

## Bilinen tuzaklar (bu kurulumda yasandi)

- PowerShell 5.1'de Set-Content -Encoding utf8NoBOM CALISMIYOR.
  [System.IO.File]::WriteAllText() kullan, surumden bagimsiz calisir.
- .env.example ile .env'i karistirma. .example git'e gidiyor, gercek anahtarlar
  SADECE .env'e yazilmali (gitignore'da).
- Supabase Secret key bir ara yanlislikla .env.example'a yazilmis olabilecegi
  icin rotate edildi. Guncel anahtar simdi .env'de.

## Mimari ozeti

GitHub Actions (scan.yml) taramayi yapar, Vercel'deki dashboard onay/yayin isini
yapar, ikisi Supabase'i paylasir. AI saglayici DeepSeek V4 Flash
(lib/ai/provider.ts uzerinden degistirilebilir). Detay: README.md.

## Kararlar

- Atak seviyesi varsayilan: Dengeli. Ilk hafta Temkinli'ye cekilmesi oneriliyor
  (/ayarlar sayfasindan), hesap gecmisi oturana kadar.
- Dashboard sifresi kullanici tarafindan bilincli olarak basit tutuldu.