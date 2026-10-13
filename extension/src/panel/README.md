# Polaris yan paneli — geliştirici notları

Kurulum, Brave'e yükleme ve sorun giderme için: [`extension/README.md`](../../README.md).

## Dosyalar

| Dosya | Görev |
|---|---|
| `App.tsx` | Sohbet, görev durumu, onay akışı, klavye kısayolları, sağlık kontrolü |
| `components.tsx` | Markdown, ayarlar, onay penceresi, sağlık kartları, durum açılır paneli, "Neden?" zaman çizelgesi |
| `health.ts` | `GET {backend}/health` (ek alanlara toleranslı: `stt_ready`, `brave_configured`, `version`…) |
| `errors.ts` | Sunucu/LLM hata metinlerini kısa Türkçe başlık + öneriye çevirir |
| `settings.ts` | `chrome.storage.local` ayarları (sunucu adresi, sesli yanıt), URL doğrulama, sürüm |
| `protocol.ts` / `ws.ts` | Donmuş WS protokolü (zod) ve yeniden bağlanan WebSocket istemcisi |
| `voice.ts` | Bas-konuş kaydı (`/stt`) ve sesli yanıt (`/tts`, yedek: speechSynthesis) |
| `mocks/session.ts` | **Yalnızca testler** için senaryolu oturum. Üretim arayüzü bunu içe aktarmaz |

## Davranış

- Panel her zaman gerçek sunucuya bağlanır (demo modu yok). Varsayılan adres `http://localhost:8765`.
- Açılışta, her yeniden bağlanmada ve sunucu kapalıyken 5 sn'de bir `/health` kontrol edilir.
- Testler sahte sunucuyu `<App createTransport={...} />` ile enjekte eder.
- Kısayollar: Enter gönder, Shift+Enter yeni satır, Esc görevi iptal et, Space (odak metin kutusunda değilken) bas-konuş.

## Test

```powershell
Set-Location extension
npx tsc --noEmit
npx vitest run
npx vite build
```
# Etkileşimli Polaris peti

Güncel main arayüzüne eklenen `PolarisPet.tsx` yerel SVG karakteridir. Mouse üzerine
gelince parıldar, gözleri pointer'ı takip eder; tıklanınca kısa bir selam ve yıldız
animasyonu gösterir. Klavye odağı ve Enter/Space ile de selamlaşılabilir.

Panelin gerçek durumuna göre dinlenme, kuyruklu yıldızla çalışma, mikrofon dinleme, konuşma
ve onay bekleme pozları kullanılır. Pet etkileşimleri araç çağrısı veya ağ isteği
başlatmaz. Hareket azaltma tercihi açık olduğunda animasyonlar kapatılır.

Derlenen `src/panel/pet-preview.html` sayfası beş durumu bağımsız denemeye yarar.
`npm run preview` ile servis edildiğinde `/src/panel/pet-preview.html` yolundan açılır.
