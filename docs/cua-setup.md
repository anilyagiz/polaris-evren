# CUA (masaüstü computer use) kurulumu

Asistanın masaüstünü kullanabilmesi için izole bir **Linux masaüstü konteyneri** (CUA, [trycua/cua](https://github.com/trycua/cua)) çalıştırılır. Ekran görüntüsü OCR ile numaralı öğe listesine çevrilir; LLM ekranı bu metinden "görür".

> CUA isteğe bağlıdır. `CUA_ENABLED=false` iken backend sorunsuz açılır ve `desktop.*` tool'ları görünmez.

## 1. Gereksinimler

| | |
|---|---|
| İşletim sistemi | Windows 10/11 Home (Windows Sandbox gerekmez) |
| Sanallaştırma | BIOS'ta açık olmalı (Görev Yöneticisi → Performans → CPU → "Sanallaştırma: Etkin") |
| WSL2 + Docker Desktop | Aşağıda |
| Python | 3.11 (backend ile aynı venv) |
| Disk | ~5 GB (konteyner imajı) |

## 2. WSL2 ve Docker Desktop

**Yönetici** PowerShell'de:

```powershell
wsl --install            # WSL2 + Ubuntu; bitince BİLGİSAYARI YENİDEN BAŞLAT
winget install -e --id Docker.DockerDesktop
```

Yeniden başlattıktan sonra Docker Desktop'ı açın:
- Settings → General → **Use the WSL 2 based engine** işaretli olsun.
- Sağ alttaki durum "Engine running" olana kadar bekleyin.

Kontrol:

```powershell
docker version
docker run --rm hello-world
```

## 3. CUA Python paketleri

Repo kökünde, backend'in venv'i aktifken:

```powershell
.venv\Scripts\activate
pip install "cua[sandbox]" rapidocr-onnxruntime pillow
```

> Not: Eski `cua-computer` paketi ve `Computer(os_type=..., provider_type="docker")` API'si **deprecated**. Bu projede `cua-sandbox` (0.9.x) kullanılır. Tüm CUA çağrıları `backend/cua/session.py` içindedir; API değişirse yalnızca orası düzeltilir.

## 4. Sandbox imajını çekme ve deneme

Linux masaüstü imajı `ghcr.io/trycua/linux` (24.04). İlk başlatmada SDK imajı kendisi çeker; önceden çekmek isterseniz:

```powershell
docker pull ghcr.io/trycua/linux:24.04
```

Duman testi (başlat → ekran görüntüsü → tıkla → yaz → ekran görüntüsü):

```powershell
python scripts/cua_smoke.py
```

Çıktılar `backend/data/shots/` altına yazılır: `smoke_1_raw.png`, `smoke_2_raw.png` ve işaretli `smoke_2.png`.

Docker olmadan algı katmanını (OCR, işaretleme, `/shots`, `desktop.*` tool'ları) denemek için:

```powershell
python scripts/cua_smoke.py --offline
```

## 5. Backend'de açma

`backend/.env`:

```
CUA_ENABLED=true
DESKTOP_PARSER=ocr      # omniparser henüz yok, ocr'a düşer
```

Backend'i yeniden başlatın. İlk `desktop.snapshot` çağrısı sandbox'ı başlatır (ilk seferde imaj indirme nedeniyle birkaç dakika sürebilir).

**Kişi 3 için entegrasyon notları (`main.py`):**
- `backend/.env` yüklendikten **sonra** `import backend.tools.desktop` (CUA_ENABLED import anında okunur).
- `from backend.cua.shots_router import router as shots_router` → `app.include_router(shots_router)`.
- Kapanışta: `await backend.cua.session.get_session().close()` (konteyner silinir).
- İsteğe bağlı ısıtma: `await asyncio.to_thread(backend.cua.perceive.warm_up)` (OCR modeli ~1 sn).
- Onay kapısı için tıklanacak öğenin adı: `backend.tools.desktop.element_name(ref)`.

## 6. Nasıl çalışır

1. `desktop.snapshot` → `session.screenshot()` (PNG, sabit 1280×800 hedeflenir).
2. `perceive.py` → RapidOCR metin kutuları; geniş boşluklardan bölünür (menü öğeleri ayrı olsun), yakın kutular satır bazında birleşir; yukarıdan aşağı, soldan sağa sıralanır; en fazla 150 öğe.
3. `annotate.py` → her öğeye kutu + ref numarası çizip `backend/data/shots/{request_id}_{n}.png` kaydeder.
4. Snapshot'taki `shot` alanı `/shots/{request_id}_{n}.png`; panel bunu `GET /shots/{name}` ile gösterir. Ayrıca `evidence: ["shot:/shots/…"]` içeren bir trace adımı gönderilir.
5. `desktop.click {ref}` → öğenin bbox merkezine tıklar, 700 ms bekler.

LLM'e giden biçim: `[5] text "Edit"`.

## 7. Sık sorunlar

| Belirti | Çözüm |
|---|---|
| `docker: command not found` / Docker bağlanamıyor | Docker Desktop açık mı? "Engine running" bekleyin; yeni terminal açın. |
| `WSL 2 installation is incomplete` | Yönetici PowerShell: `wsl --update`, sonra yeniden başlat. |
| "Virtualization not enabled" | BIOS/UEFI'de Intel VT-x / AMD-V'yi açın. |
| `Image.linux() is a VM and needs QEMU` | `Image.linux(kind="container")` kullanılmalı (session.py zaten böyle). |
| İlk başlatma çok uzun | İmaj iniyor; `docker pull ghcr.io/trycua/linux:24.04` ile önceden çekin. |
| Tıklama yanlış yere gidiyor | Ekran boyutu değişmiş olabilir; log'daki "Sandbox screen size" satırına bakın, yeni `desktop.snapshot` alın. |
| OCR öğe bulmuyor / yavaş | `rapidocr-onnxruntime` kurulu mu? Hedef: snapshot < 2 sn (ölçülen: ~0,8 sn, 1280×800, CPU). |
| `/shots/...` 404 | Dosya `backend/data/shots/` altında mı? Ad yalnızca `[A-Za-z0-9_-].png` olabilir. |
