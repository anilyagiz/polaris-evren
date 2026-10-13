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

- Panel her zaman gerçek sunucuya bağlanır (demo modu yok). Varsayılan adres `http://localhost:8000`.
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
