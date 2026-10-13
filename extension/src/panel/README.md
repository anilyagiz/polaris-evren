# Polaris yan paneli — Kişi 1 teslimi

Brave Manifest V3 eklentisi. React + TypeScript strict, CRXJS, Vite.
Kullanıcı metinleri Türkçe; protokol ve tanımlayıcılar ekip sözleşmesiyle aynı.

## PowerShell ile kurulum

Node.js 22 veya daha yeni bir sürüm ve npm kurulu olmalı.
Repo kökünden:

```powershell
Set-Location extension
npm install
npm run build
```

Bu çalışma ortamında pnpm kullanıldı ve `pnpm-lock.yaml` repoya eklendi.
pnpm kullananlar için:

```powershell
Set-Location extension
pnpm install --frozen-lockfile
pnpm run build
pnpm test
```

## Brave'e yükleme

1. `brave://extensions` adresini aç.
2. Geliştirici modunu aç.
3. “Paketlenmemiş yükle” seçeneğiyle `extension/dist` klasörünü seç.
4. Brave'in yan panel menüsünden “Polaris · EVREN Asistan”ı seç.
5. İlk açılışta Mock açık, otomatik sesli yanıt kapalıdır. Dişli simgesinden değiştir.

**K2 entegrasyonu:** `src/background.ts` ve `src/content/index.ts` yalnızca başlangıç placeholder'larıdır.
Toolbar ikonuna tıklayınca panel açılması için K2 background dosyasında
`chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })` davranışını eklemeli.
K1 bu dosyalara gerçek tarayıcı kontrolü eklemez.

## Geliştirme ve önizleme

```powershell
npm run dev
```

Vite'in terminalde bildirdiği yerel adresin `/src/panel/index.html` yolunu aç.
Örnek: `http://127.0.0.1:5173/src/panel/index.html`.
Web önizlemesinde `chrome.*` bulunmaz; ayarlar localStorage'da saklanır ve aktif sekme null olur.
Gerçek sekme kontrolü için eklenti olarak yükle.
CRXJS geliştirme derlemesi de `dist` üretir; üretim demosu için dev sunucusunu kapatıp
`npm run build` çalıştır, ardından Brave eklentisini yenile.

## Panelde bulunan özellikler

- Sohbet ve güvenli Markdown (GFM tabloları, HTML sanitize, HTTP(S) kaynak bağlantıları).
- İlk açılışta dört örnek komut.
- WebSocket hello; 1/2/4/8/10 saniye ile yeniden bağlantı. Görevler otomatik yeniden gönderilmez.
- Aktif sekmeyle user_msg; bağlantı başına tek görev; cancel.
- TraceStep zaman çizelgesi: ajan, karar, kısa gerekçe, güven, araç, puan, verdict, kanıtlar ve süre/token.
- ref highlight, URL kaynakları, /shots/*.png önizleme ve büyütme.
- 60 saniyelik onay modalı. Reddet, kapat, Escape ve timeout `approved:false` gönderir.
- Bas-konuş: fare/dokunmatik veya Space; input içindeyken Space normal yazı yazar.
- WebM/Opus → POST /stt (multipart `audio`); transkript `source:voice` ile gönderilir.
- POST /tts, MP3 oynatma; hata durumunda Türkçe speechSynthesis; manuel “Sesli oku” ve durdur.
- Backend adresi, Mock ve ses ayarları chrome.storage.local'a kaydedilir.
- notify bandı, hata mesajları, açık/koyu tema, 360 px genişlik.

## Mock akışı

`shared/fixtures/session_example.jsonl` mevcutsa derlemede içeri alınır.
Yoksa veya sunucu olaylarına uymuyorsa panelin yerel fixture'ı kullanılır.
Olaylar 300 ms aralıkla ilerler. Onay olayında oynatma durur; yalnızca doğru call_id ile devam eder.
“Bu formu doldur” onay modalını gösterir. Red halinde “Form gönderilmedi” cevabı gelir.
Mock gerçek sayfa okumaz, form göndermez, hatırlatıcı oluşturmaz.
Mikrofonun STT testi mock modunda bile çalışan /stt backend'i gerektirir.
Mock sesli cevaplar yerel speechSynthesis üzerinden okunur.

## K2 araç köprüsü

K2 şu dosyayı teslim eder:

```typescript
// extension/src/bridge/executeTool.ts
export async function executeBrowserTool(
  name: string,
  args: Record<string, unknown>,
  tabId?: number,
): Promise<{ ok: boolean; data: unknown | null; error: string | null }>;
```

Vite alias'ı bu dosya varsa gerçek köprüyü, yoksa panel içindeki hata dönen fallback'i seçer.
K2 dosyayı ekleyince dev sunucusunu yeniden başlat veya yeniden build et.
Gerçek modda eksik köprü başarılı sahte işlem döndürmez.
tool_call için görevin başladığı sekme kimliği kullanılır; highlight da cevabın ait olduğu sekmeye gider.
Panel yalnızca browser.* tool çağrılarını işler. Diğer araçlar backend'indir.

## K3 ve K5 bağlantısı

- `GET /health`, `WS /ws`: K3; panel `/ws` kullanır.
- `POST /stt`: K5; alan `audio`; cevap `{text, language, duration_s}`.
- `POST /tts`: K5; JSON `{text}`; cevap MP3.
- Backend adresi varsayılan `http://localhost:8000`. Gerçek mod için Ayarlar → Mock kapat.
- K3, K5'in router'larını main.py'de include etmeli ve extension origin'i için CORS açmalı.
- DeepSeek/EVREN API anahtarı panelde tutulmaz; LLM bağlantısı backend'e aittir.
- PatientDesk entegrasyonu eklenmedi; ekip mevcut STT/TTS sözleşmesiyle devam ediyor.

## Doğrulama ve kalan entegrasyon kontrolleri

```powershell
npm test
npm run build
```

Testler: protokol doğrulama, URL/screenshot güvenliği, hello/reconnect,
onay/timeout/red/iptal, Markdown güvenliği, WebM multipart aktarımı, izin reddi,
izin beklerken bırakılan mikrofon ve TTS fallback.
Tarayıcı önizlemesinde 360 px görünüm ve mock onay/red akışı kontrol edilir.

Canlı ekip backend'i ve K2 köprüsüyle ayrıca doğrulanmalı:

- Aktif sekmenin gerçekten highlight edilmesi.
- Brave yan panelinde gerçek mikrofon izni, Türkçe transkripsiyon ve MP3 ses çıkışı.
- Gerçek görevde onay/red, bağlantı kopması ve iptal.
- Masaüstü ekran görüntüsü endpoint'i.

Mikrofon izni panelde çıkmazsa “Mikrofon izni ver” bağlantısı extension origin'indeki
`src/panel/permission.html` sayfasını açar. İzin verildikten sonra sekme kapanır; panelde bas-konuşu tekrar kullan.
Mikrofon için bir kayıt en fazla 60 saniye sürer.

## Dosya sahipliği

K1: manifest.json, vite.config.ts, package.json, TS/test/kurulum iskeleti ve src/panel/**.
K2: background.ts, content/**, bridge/**. Yalnızca boş ilk dosyalar oluşturuldu.
K3/K4/K5 backend, shared fixture ve ekip dokümanlarına müdahale edilmedi.
