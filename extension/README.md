# Polaris eklentisi (Brave, Manifest V3)

Polaris'in Brave yan panelinde çalışan arayüzü: sohbet, bas-konuş, sesli yanıt,
onay penceresi ve her adımın gerekçesini gösteren **"Neden?"** zaman çizelgesi.
Eklenti tek başına çalışmaz; her zaman yerel **Polaris sunucusuna** (FastAPI,
varsayılan `http://localhost:8765`) bağlanır.

## 1. Derleme

Gereksinim: Node.js 18+ ve npm. (Bu makinede `pnpm` EPERM hatası veriyor; npm kullan.)

```powershell
Set-Location extension
npm install
npm run build      # tsc --noEmit + vite build  ->  extension\dist
```

Diğer komutlar:

| Komut | İş |
|---|---|
| `npm test` | Vitest (panel + content + bridge) |
| `npm run typecheck` | Yalnızca tip kontrolü |
| `npm run dev` | Geliştirme sunucusu (CRXJS HMR) |
| `npm run icons` | `public/icons/*.png` dosyalarını yeniden üretir |

Repo kökünden tek adımda: `.\scripts\build-extension.ps1`.

## 2. Brave'e yükleme

1. `brave://extensions` adresini aç.
2. Sağ üstten **Geliştirici modu**nu aç.
3. **Paketlenmemiş uzantıyı yükle** → `extension\dist` klasörünü seç.
4. Araç çubuğundaki Polaris simgesine tıkla; yan panel açılır.
   (Simge görünmüyorsa yapboz simgesinden Polaris'i sabitle.)

Kod değiştirdikten sonra yeniden derle ve `brave://extensions` içinde uzantının
**yenile** düğmesine bas; açık sekmeleri de yenile.

## 3. İlk çalıştırma

1. Sunucuyu başlat:
   - **Polaris tepsi uygulaması** ile ya da repo kökünde `start-polaris.cmd` çalıştırarak,
   - geliştirici kurulumunda: `.\scripts\run-backend.ps1`.
2. Paneli aç. Panel açılışta `GET /health` ile sunucuyu kontrol eder:
   - **"Polaris sunucusu çalışmıyor"** kartı → sunucuyu başlat (komutu karttan kopyalayabilirsin)
     ve **Tekrar dene**'ye bas. Sunucu açıldığında kart birkaç saniye içinde kendiliğinden kaybolur.
   - **"Yapay zekâ anahtarı ayarlı değil"** kartı → `backend\.env` içine `DEEPSEEK_API_KEY`
     ve `LLM_MODEL` yaz, sunucuyu yeniden başlat.
3. Sağ üstteki durum rozetine (Bağlı / Kopuk / Sunucu kapalı) tıklayınca sunucu adresi,
   model adı, masaüstü (CUA) durumu ve sürüm bilgileri görünür.
4. İlk **bas-konuş**ta mikrofon izni için ayrı bir sekme açılır (yan paneller izin isteyemez);
   **Mikrofon izni ver** → Brave'de **İzin ver**, sonra panele dön.

### Ayarlar (dişli simgesi)

- **Sunucu adresi:** yalnızca `http://` veya `https://`. **Bağlantıyı test et** düğmesi `/health`'i dener.
- **Sesli yanıt:** cevapları Türkçe sesli okur.
- Alt kısımda uzantı sürümü (manifest'ten) görünür.

**Port notu:** Uzantının güvenlik politikası (CSP) `localhost` ve `127.0.0.1` üzerindeki
**her porta** (`http://` ve `ws://`) izin verir. Sunucuyu başka bir portta çalıştırıyorsan
Ayarlar'dan adresi değiştirmen yeterli. Uzak (ağdaki) bir sunucu CSP tarafından engellenir.

### Kısayollar

| Tuş | İş |
|---|---|
| Enter | Gönder |
| Shift+Enter | Yeni satır |
| Esc | Çalışan görevi iptal et (sesli okumayı / kaydı durdurur) |
| Space (metin kutusu dışında, basılı tut) | Bas-konuş |

Cevapların altında **Kopyala** ve **Sesli oku**; başlıkta **Sohbeti temizle** düğmesi vardır.

## 4. Sorun giderme

| Belirti | Çözüm |
|---|---|
| "Polaris sunucusu çalışmıyor" | Sunucu açık mı? Tarayıcıda <http://127.0.0.1:8765/health> → `{"ok": true, ...}` dönmeli. Ayarlar'daki adresi kontrol et |
| Rozet sürekli "Bağlanıyor/Kopuk" ama `/health` açılıyor | Ayarlar'daki adres ile sunucu adresi aynı mı (`localhost` / `127.0.0.1`, port)? Uzantıyı yenile |
| "Yapay zekâ anahtarı kabul edilmedi" / "Model ayarı hatalı" | `backend\.env`: `DEEPSEEK_API_KEY`, `LLM_MODEL`, `DEEPSEEK_BASE_URL`; değiştirince sunucuyu yeniden başlat |
| "Yapay zekâ servisi şu an yoğun" (429) | Birkaç saniye bekleyip tekrar dene |
| Zaman çizelgesinde "Araç başarısız" | Adımın altındaki açıklamayı oku; çoğunlukla sayfa okunamadı ya da zaman aşımı. Normal bir `https://` sekmesinde tekrar dene |
| Uzantı sayfayı okuyamıyor | `brave://` ve mağaza sayfalarında içerik betiği çalışmaz. Normal bir sayfa aç ve yenile |
| Mikrofon çalışmıyor | `brave://settings/content/microphone` ve Windows Ayarlar → Gizlilik → Mikrofon. Metin kutusu her zaman kullanılabilir |
| Sesli yanıt yok | Ayarlar → Sesli yanıt açık mı? `/tts` internet ister; yoksa tarayıcı sesine düşer |
| Derleme hatası | `extension` klasöründe `npm install`, sonra `npm run build`. `pnpm` kullanma |
| Eski arayüz görünüyor | `brave://extensions` → Polaris → yenile; yan paneli kapatıp aç |

## Notlar

- İzinler: `sidePanel`, `storage`, `scripting`, `tabs` + tüm sitelerde içerik betiği
  (sayfa okuma/tıklama için). API anahtarları eklentide **bulunmaz**; yalnızca `backend\.env` içindedir.
- Gönder / satın al / öde / sil gibi işlemler onay penceresi olmadan yapılmaz; 60 sn içinde
  cevap verilmezse işlem reddedilir.
- Geliştirici test sayfası (`src/dev/tester.html`) yalnızca `npm run dev` ile erişilir, pakete girmez.
