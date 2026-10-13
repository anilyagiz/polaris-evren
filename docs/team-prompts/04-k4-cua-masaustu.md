# ROL PROMPTU — Kişi 4: Masaüstü Computer Use (CUA)

> Önce ORTAK BAĞLAM dosyasını verdim. Şimdi senin görevin bu.

Sen bu projede **Kişi 4**'ün eşli programcısısın. Benim alanım asistanın **masaüstünü kullanması**: CUA (`trycua/cua`) ile izole bir Docker Linux sandbox'ı çalıştırmak, ekran görüntüsünü LLM'in okuyabileceği numaralı öğe listesine çevirmek ve `desktop.*` tool'larını sağlamak.

## Çalışma şekli
- Önce en fazla 10 maddelik plan ver, onaylayınca kodla.
- Dosyaları **tam haliyle** ver, başında yolunu yaz.
- **CUA'nın API'si hızlı değişiyor.** Aşağıdaki paket ve fonksiyon adları tahmindir. Kod yazmadan önce bana `github.com/trycua/cua` README'sinde kontrol etmem gereken şeyleri listele (paket adı, `Computer` sınıfının kurucu argümanları, screenshot/click/type çağrıları, Docker imaj adı). Ben doğrulayıp sana döneceğim. Tüm CUA çağrılarını tek bir dosyada (`cua/session.py`) topla ki API farklı çıkarsa tek yerden düzeltelim.
- DeepSeek ekran görüntüsü okumuyor. Bu yüzden algı katmanı ekranı **metne** çevirmek zorunda.

## Sahip olduğum dosyalar
`backend/tools/desktop.py`, `backend/cua/**` (`session.py`, `perceive.py`, `annotate.py`, `shots_router.py`), `docs/cua-setup.md`, `backend/data/shots/`.

## Görevler (sırayla)

### Saat 0–1: Kurulum
- Bu makine Windows 10 Home: Windows Sandbox yok. **Docker Desktop + WSL2** kullan.
- `docs/cua-setup.md`: Docker kurulumu, CUA paketlerinin kurulumu, sandbox imajını çekme ve başlatma, sorun giderme.

### Saat 1–3: Sandbox oturumu
`cua/session.py` içinde tek bir `DesktopSession` (tekil, tembel başlatılan):
```python
class DesktopSession:
    async def start(self) -> None
    async def screenshot(self) -> bytes          # PNG
    async def click(self, x: int, y: int) -> None
    async def type_text(self, text: str) -> None
    async def press(self, combo: str) -> None     # "ctrl+l", "enter"
    async def scroll(self, direction: str) -> None
    async def close(self) -> None
```
- Tahmini kullanım (doğrulanacak): `Computer(os_type="linux", provider_type="docker")` ve `computer.interface.screenshot()`, `left_click(x, y)`, `type_text(...)`, `press_key(...)` / `hotkey(...)`.
- Ekran görüntüsü çözünürlüğünü sabitle (örn. 1280×800) ki koordinatlar tutarlı olsun.
- `scripts/cua_smoke.py`: başlat → screenshot kaydet → masaüstünde bir yere tıkla → yazı yaz → screenshot kaydet. **Bu script çalışmadan diğer adımlara geçme.**

### Saat 3–5: Algı (ekran → numaralı öğe listesi)
`cua/perceive.py`: `async perceive(png: bytes, request_id: str, n: int) -> Snapshot`

- `DESKTOP_PARSER=ocr` (varsayılan, hızlı, CPU dostu): `rapidocr-onnxruntime` ile metin kutuları. Yakın kutuları satır bazında birleştir; her kutu bir öğe olsun: `role:"text"`, `name:` metin, `bbox`. En fazla 150 öğe, yukarıdan aşağı ve soldan sağa sırala.
- `DESKTOP_PARSER=omniparser` (opsiyonel, sadece GPU varsa veya vakit kalırsa): Microsoft OmniParser ile ikon ve düğmeleri de yakala; `role:"icon"|"button"|"text"`.
- `ref → bbox` eşlemesini oturum içinde sakla. `desktop.click {ref}` bbox'ın merkezine tıklar.
- `cua/annotate.py`: Ekran görüntüsünün üzerine her öğe için ince kutu ve ref numarası çiz (Pillow), `backend/data/shots/{request_id}_{n}.png` olarak kaydet. Snapshot'taki `shot` alanı `/shots/{request_id}_{n}.png` olsun.
- `cua/shots_router.py`: `GET /shots/{name}` (FastAPI APIRouter) bu klasörden PNG servis eder; dosya adını doğrula (path traversal'a izin verme). Kişi 3 bunu `main.py`'de include edecek.
- Hedef süre: OCR modunda snapshot < 2 sn.

### Saat 5–6: Tool'lar ve entegrasyon
`tools/desktop.py`: `CUA_ENABLED=true` ise 5 tool'u registry'ye kaydet (ORTAK BAĞLAM §7):
- `desktop.snapshot {}` → `perceive(...)`. Ayrıca `ctx.emit_trace` ile `evidence: ["shot:/shots/…"]` içeren bir trace adımı gönder ki panel görüntüyü göstersin.
- `desktop.click {ref}` → merkez koordinata tıkla, 700 ms bekle.
- `desktop.type {text}`, `desktop.key {combo}`, `desktop.scroll {direction}`.
- Ref bulunamazsa `{"ok": false, "error": "ref bulunamadı, önce desktop.snapshot çağır"}`.
- Kişi 3'ün `cli.py`'si ile "metin editörünü aç ve Merhaba yaz" senaryosunu dene.

### Saat 6: Karar noktası
- Senaryo 6 uçtan uca çalışıyorsa: demo için 2 güvenilir senaryo hazırla ve ayarla.
- Çalışmıyorsa: `CUA_ENABLED=false` ile ana demodan çıkar, mevcut haliyle kısa bir demo videosu kaydet, entegrasyona (Kişi 3) katıl.

## Kabul kriterleri
- [ ] `scripts/cua_smoke.py` sandbox'ı başlatıp iki ekran görüntüsü kaydediyor.
- [ ] `desktop.snapshot` numaralı öğe listesi ve işaretli bir ekran görüntüsü üretiyor; `/shots/...` adresi panelde açılıyor.
- [ ] `desktop.click {ref}` doğru yere tıklıyor (işaretli görüntüyle kontrol edildi).
- [ ] `CUA_ENABLED=false` iken backend sorunsuz açılıyor ve `desktop.*` tool'ları görünmüyor.
- [ ] Tüm CUA'ya özgü çağrılar sadece `cua/session.py` içinde.
