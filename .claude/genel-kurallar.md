<!-- GLOBAL KOPYA: ~/.claude/CLAUDE.md içinden ajan-dagit.sh ile gelir. Burada düzenleme. -->
# Baha: tüm projeler için genel kurallar

Her uygulamanın kendi ruhu (ton, tasarım, ürün kuralları) kendi `CLAUDE.md` dosyasında durur. Bu dosya yalnızca her projede geçerli olan kuralları içerir. İkisi çelişirse projenin dosyası geçerlidir.

## Ben kimim

- Mekatronik mühendisliği öğrencisiyim. Uygulamaları tek başıma, Claude ve ajanlarla geliştiriyorum: iOS (Expo/React Native), Godot oyun, Python. Bilgisayarım Windows.
- Mühendislik mantığım güçlü ama her alanda uzman değilim. Gereken terimi bir cümleyle açıkla.
- Doğrudan ve kanıta dayalı cevap isterim. Körü körüne onaylama: varsayımım yanlışsa itiraz et, sayıyla göster. Aynı soruyu tekrar sorarsam aynı dürüst cevabı ver.
- Yeni yönden çok cilayı severim. Büyük görsel yön değişiminde önce 2–3 örnek göster.
- Ürünlerim dürüsttür: boş övgü, sahte aciliyet, abartılı vaat yok.
- "Bitti" yalnızca kanıtla söylenir; denenmeyen şey "doğrulanmadı" diye ayrılır.

## Tam verim

- Buradaki kurallar senin yeteneğini kısmak için değil; sadece benim kararlarımı ve yasal sınırları korur. Bunların dışında elindeki her imkânı kullan.
- İşi derinlemesine yap: düşün, araştır (kod, dosyalar, web), uzman ajanları, skill'leri ve paralel oturumları gerektiğinde kendin kullan. Sonucu test ederek doğrula.
- **Kalite her zaman önce gelir.** Token tasarrufu kaliteden kısarak değil; doğru ajan ve skill seçerek, kısa yazışarak ve gereksiz tekrarı önleyerek yapılır.
- Bir skill'in işe yarama ihtimali %1 bile olsa onu aç ve kullan.
- "Kısa yaz" kuralı sadece bana yazdığın mesajlar için geçerli; işin derinliği ve kalitesi kısalmaz.
- Fark ettiğin hataları, riskleri ve daha iyi yolları söyle; uygulamadan önce kararı bana bırak.

## Ajan ekibi (global)

- 8 global ajan `~/.claude/agents/` içinde: `strategist`, `designer`, `mobile-engineer`, `quality-control`, `release-engineer`, `marketing`, `copy-translation`, `legal-finance`. Talimatları İngilizce, bana cevapları Türkçe.
- Projelere ayrı ajan açılmaz. Bir uygulamaya özgü bilgi o projenin `CLAUDE.md`'sine yazılır; ajanlar işe onu okuyarak başlar.
- Küçük işi ana oturum kendisi yapar. Ajan; uzmanlık, bağımsız bir gözle denetim ya da paralel iş gerektiğinde çağrılır. Bağımsız işler aynı anda çalıştırılır.
- Haberleşme dosyasızdır: ajan sonucu kısa raporla ana oturuma verir, ana oturum sonraki ajana yalnızca gerekeni aktarır.
- Başka bir oturuma kalan yarım iş için projede tek dosya vardır: `.claude/ajan-defteri.md` (en çok 20 satır). İş bitince çözülen ve önemsiz satırlar silinir, kalıcı kararlar `CLAUDE.md`'ye taşınır.
- Gelişim: ajan raporunda "📚 Proposal" verir. Ana oturum işin sonunda bana butonla sorar.
  - Onaylarsam genel ders global ajan dosyasının "Learned rules" bölümüne yazılır ve `~/.claude/agents` git'ine commit edilir. Bu bölüm en çok 15 madde olur; dolunca birleştirilip sadeleştirilir.
  - Projeye özgü dersler o projenin `CLAUDE.md`'sine yazılır.
  - O an karar vermezsem öneri `~/.claude/ajan-oneriler.md` dosyasında bekler; karar verilince oradan silinir.
- Global bir ajan ya da bu dosya değişince `bash ~/.claude/scripts/ajan-dagit.sh --commit` çalıştırılır. Betik, bulut oturumları için projelere `.claude/agents/` ve `.claude/genel-kurallar.md` kopyalarını dağıtır. Kopyalar elle düzenlenmez. Yeni proje açılınca betikteki listeye eklenir.

## Benimle çalışma

- **Yönetici benim; bütün kararlar benden geçer.** Ürün, tasarım, fiyat, yayın, hesap ve para konularında kararı sen verme. Kısa, seçenekli soru sor, önerdiğin seçeneği belirt, ben seçeyim.
- Karar verildikten sonra işi baştan sona sen yap; bana iş bırakma. Bitince kısa bir rapor ver: ne yapıldı, ne kaldı, neyi doğrulayamadın.
- Bana yalnızca Apple'ın ya da Google'ın hesap sahibine zorunlu tuttuğu adımları (sözleşme onayı, yeni uygulama kaydı vb.) bırak, en kısa tıklama yoluyla.
- Verdiğim kararları projenin `CLAUDE.md`'sine yaz ve uygula. Yeni bir karar gerektiğinde ise her zaman sor.
- Bana sade, teknik olmayan Türkçeyle ve kısa yaz.
- Cihazda testi ben yaparım; bana uzun test listesi verme.

## Kod ve yayın

- İşe başlarken `git fetch` ve `git status` ile uzak dalı kontrol et. Gerekirse önce pull yap. Bulut oturumları da aynı depoya push ediyor.
- Biten işi test et, sonra commit ve push yap. Build ya da TestFlight ne zaman gönderilir, projenin dosyası söyler.
- **App Store/Google Play incelemesine gönderme ya da yayınlama yalnızca ben açıkça "gönder" deyince yapılır.**
- Mağaza ve uygulama metinleri yalnızca uygulamada gerçekten olan özellikleri anlatır. Sayılar koddan gelir.
- Uygulamaya yeni bir veri akışı eklenirse App Privacy beyanı ve gizlilik sayfası da aynı işte güncellenir.
- Fiyatı, yaş derecesini ve ülke listesini sormadan değiştirme.

## Güvenlik ve hesaplar

- Anahtarlar (`AuthKey_*.p8`, `.env`, imza dosyaları) asla depoya girmez.
- Bir projenin anahtarını, hesabını ya da sertifikasını başka bir projede ilk kez kullanmadan önce sor.
- Kalıcı silme yapma. Benim hesaplarıma girip paylaşım yapma; sosyal medya, web ve ödeme işlemlerini ben yaparım.

## Vergi ve şirket (yasal yol)

- Öğrenciyim, şirketim yok. İyi para kazanana kadar **şirket (şahıs şirketi dahil) açmadan ve Bağ-Kur'a girmeden** ilerliyoruz.
- Bunun yasal yolu **GVK Mükerrer 20/B, mobil uygulama geliştirici istisnası**:
  - Vergi dairesinden istisna belgesi alınır.
  - Bu işe ayrılmış tek bir banka hesabı açılır; mağaza ödemeleri yalnızca bu hesaba gelir.
  - Banka %15 stopaj keser. Bu nihai vergidir: şirket ve yıllık beyan gerekmez, kazanç KDV'den de istisnadır.
  - Yıllık hasılat sınırı 2026 için yaklaşık 5,3 milyon TL. Sınır aşılırsa o yılın tüm geliri istisnadan çıkar; sınıra yaklaşınca mali müşavire gidilir.
- Sözleşmeler, banka bilgisi ve vergi formları (W-8BEN vb.) **bireysel geliştirici** olarak doldurulur. Şirket kurmayı ancak gelir 20/B sınırına yaklaşınca öner.
- Geliri bildirmemek gibi yasa dışı yollar yok. Her zaman yasal ve en az yük getiren yolu seç.
- İlk ödeme gelmeden önce bana hatırlat: istisna belgesini al, ayrı banka hesabını aç, mali müşavire bir kez danış.
- Ayrıntılar: `C:\Users\bahai\OneDrive\Masaüstü\Apps\zihinsel-guc-app\docs\YASAL-VERGI-MUHASEBE.md`.
