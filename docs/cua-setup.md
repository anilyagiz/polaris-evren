# CUA (masaüstü computer use) kurulumu

Asistanın masaüstünü kullanabilmesi için izole bir **Linux masaüstü konteyneri** (CUA, [trycua/cua](https://github.com/trycua/cua)) çalıştırılır. Ekran görüntüsü OCR ile numaralı öğe listesine çevrilir; LLM ekranı bu metinden "görür".

> CUA isteğe bağlıdır. `CUA_ENABLED=false` iken backend sorunsuz açılır ve `desktop.*` tool'ları görünmez.

**Doğrulanmış ortam (13.10.2026):** Windows 10 Home 19045, Docker Desktop (Engine 28.3), repo kökündeki `.venv` (Python 3.13), `cua-sandbox 0.9.0` + `cua 0.4.1`, `rapidocr 3.10.0`. `scripts/cua_smoke.py` gerçek konteynerde baştan sona geçti: başlatma ~4-7 sn, ekran 1280×800, tıklama + `ctrl+alt+t` ile terminal + `echo Merhaba` yazma + OCR (22 öğe).

## 1. Gereksinimler

| | |
|---|---|
| İşletim sistemi | Windows 10/11 Home (Windows Sandbox / Hyper-V Manager gerekmez) |
| Sanallaştırma | BIOS'ta açık olmalı (Görev Yöneticisi → Performans → CPU → "Sanallaştırma: Etkin") |
| WSL2 + Docker Desktop | Aşağıda |
| Python | 3.11-3.13 (backend ile aynı `.venv`; `cua-sandbox` 3.14'ü desteklemiyor) |
| İmaj | `ghcr.io/trycua/linux:24.04` — **1,26 GB indirme, diskte 4,6 GB** (Ubuntu 24.04 + XFCE + Firefox/Chromium + xfce4-terminal) |
| Disk | ~6 GB boş alan (imaj + Python paketleri ~300 MB) |

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

Repo kökünde:

```powershell
.venv\Scripts\python.exe -m pip install -r backend\requirements-cua.txt
```

Kurulanlar (sabitlenmiş): `cua-sandbox==0.9.0`, `cua==0.4.1` (Windows için hazır Rust çekirdeği `cua_sdk.dll` içerir), `rapidocr==3.10.0`, `onnxruntime`, `opencv-python-headless`.

Dikkat:
- **`rapidocr-onnxruntime` kurmayın.** Python 3.13'te pip onu sessizce 2022'den kalma `1.2.3` sürümüne çözer; bu sürüm kelime kutusu vermez ve menülerde metin kaçırır (testte "Edit" hiç bulunmadı). Bakımı süren paket `rapidocr` (3.x); OCR modelleri wheel içinde gelir, ilk çalıştırmada indirme yapmaz.
- `opencv-python` ile `opencv-python-headless` aynı anda kurulu olmasın (ikisi de `cv2` sağlar). Varsa: `pip uninstall -y opencv-python` ve `pip install --force-reinstall --no-deps opencv-python-headless`.
- Eski `cua-computer` paketi ve `Computer(os_type=..., provider_type="docker")` API'si kullanılmıyor. Bu proje `cua_sandbox.Sandbox` API'sini kullanır; tüm CUA çağrıları `backend/cua/session.py` içindedir.

Gerçek API (session.py'de kullanılan, kaynak koddan doğrulandı):

```python
from cua_sandbox import Image, Sandbox
async with Sandbox.ephemeral(Image.linux(kind="container"), local=True,
                             runtime="runc", telemetry_enabled=False) as sb:
    png = await sb.screenshot(format="png")          # bytes
    w, h = await sb.screen.size()                    # (1280, 800)
    await sb.mouse.click(x, y)
    await sb.mouse.scroll(x, y, scroll_x=0, scroll_y=-5)   # + yukarı, - aşağı (tık sayısı)
    await sb.keyboard.type("metin")
    await sb.keyboard.keypress(["ctrl", "alt", "t"])
    res = await sb.shell.run("wmctrl -l", timeout=10)      # res.stdout / res.returncode
# blok bitince konteyner silinir
```

Önemli ayrıntılar:
- `Image.linux()` **`kind="container"` olmadan** QEMU VM'i seçer (Windows'ta donanım hızlandırması yok, çok yavaş, ayrıca ~100 MB "QEMU portable" indirir). session.py her zaman `kind="container"` kullanır.
- `runtime="runc"`: gVisor (`runsc`) gerektirmez, Docker Desktop'ta değişiklik yapmaz. `CUA_RUNTIME` ile değiştirilebilir (boş bırakılırsa `auto`).
- cua'nın Rust çekirdeği Docker'ı yalnızca Unix soketlerinde arar. Windows'ta `DOCKER_HOST` tanımlı değilse session.py otomatik olarak `npipe:////./pipe/docker_engine` ayarlar.
- Başlangıçtan hemen sonra klavye çağrıları "no window has keyboard focus" hatası verebilir. session.py başlatmadan sonra XFCE panelini bekler (`wmctrl -l`, en fazla 30 sn); yine de bu hata gelirse `xdotool` ile yazar/tuşlar.

## 4. Sandbox imajını çekme ve deneme

İlk başlatmada SDK imajı kendisi çeker; önceden çekmek daha iyidir (sahnede bekleme olmasın):

```powershell
docker pull ghcr.io/trycua/linux:24.04
docker images ghcr.io/trycua/linux     # DISK USAGE ~4.57GB, CONTENT SIZE ~1.26GB
```

Daha küçük alternatif: `ghcr.io/trycua/linux:24.04-slim` (0,49 GB indirme; yalnızca cua-spacesd + Chromium). Kodda kullanılmıyor/test edilmedi.

Duman testi (başlat → ekran görüntüsü → tıkla → `ctrl+alt+t` → `echo Merhaba` → ekran görüntüsü → OCR):

```powershell
.venv\Scripts\python.exe scripts\cua_smoke.py
```

Beklenen çıktı (özet):

```
[start] sandbox hazır: 6.5s, ekran 1280x800
[shot] smoke_1_raw.png (40392 bayt)
[shot] smoke_2_raw.png (84117 bayt)
[perceive] 22 öğe, ... -> /shots/smoke_2.png
[8] text "File"  [9] text "Edit" ...  [20] text "Merhaba"
OK
```

Çıktılar `backend/data/shots/` altına yazılır: `smoke_1_raw.png`, `smoke_2_raw.png` ve işaretli `smoke_2.png`. Konteyner test sonunda silinir (`docker ps -a` ile kontrol edin).

Docker olmadan algı katmanını (OCR, işaretleme, `/shots`, `desktop.*` tool'ları) denemek için:

```powershell
.venv\Scripts\python.exe scripts\cua_smoke.py --offline
```

Birim testleri (Docker ve OCR gerekmez): `.venv\Scripts\python.exe -m pytest -q tests/cua`

## 5. Backend'de açma

`backend/.env`:

```
CUA_ENABLED=true
DESKTOP_PARSER=ocr      # omniparser henüz yok, ocr'a düşer
# CUA_RUNTIME=runc      # varsayılan; gVisor kuruluysa "gvisor"
# DOCKER_HOST=npipe:////./pipe/docker_engine   # Windows'ta otomatik
```

Backend'i yeniden başlatın. İlk `desktop.snapshot` çağrısı sandbox'ı başlatır (imaj çekiliyse ~5 sn; değilse önce 1,26 GB iner).

**Kişi 3 için entegrasyon notları (`main.py`):**
- `backend/.env` yüklendikten **sonra** `import backend.tools.desktop` (CUA_ENABLED import anında okunur).
- `from backend.cua.shots_router import router as shots_router` → `app.include_router(shots_router)`.
- Kapanışta: `await backend.cua.session.get_session().close()` (konteyner silinir).
- İsteğe bağlı ısıtma: `await asyncio.to_thread(backend.cua.perceive.warm_up)` (model yükleme + bir deneme çıkarımı, ~1-2 sn; ilk gerçek snapshot'ı hızlandırır).
- Onay kapısı için tıklanacak öğenin adı: `backend.tools.desktop.element_name(ref)`.

## 6. Nasıl çalışır

1. `desktop.snapshot` → `session.screenshot()` (PNG, 1280×800; imaj zaten bu boyutta).
2. `perceive.py` → RapidOCR satır + kelime kutuları; satır içinde geniş boşluktan bölünür (menü öğeleri ayrı olsun: "Search", "Help"), yakın kutular birleşir; yukarıdan aşağı, soldan sağa sıralanır; en fazla 150 öğe.
3. `annotate.py` → her öğeye kutu + ref numarası çizip `backend/data/shots/{request_id}_{n}.png` kaydeder.
4. Snapshot'taki `shot` alanı `/shots/{request_id}_{n}.png`; panel bunu `GET /shots/{name}` ile gösterir. Ayrıca `evidence: ["shot:/shots/…"]` içeren bir trace adımı gönderilir.
5. `desktop.click {ref}` → öğenin bbox merkezine tıklar, 700 ms bekler.

LLM'e giden biçim: `[5] text "Edit"`.

Ölçülen OCR süresi (1280×800 gerçek XFCE ekranı, CPU): ilk çıkarım ~1,8-2,5 sn, sonrakiler ~0,6-1,0 sn. `warm_up()` ilk çıkarım maliyetini açılışa taşır.

## 7. Sık sorunlar

| Belirti | Çözüm |
|---|---|
| `a container engine is not available: no Docker-API socket found` | Windows'ta `DOCKER_HOST` yok. session.py bunu otomatik ayarlar; başka bir betikten çağırıyorsanız `$env:DOCKER_HOST="npipe:////./pipe/docker_engine"`. Docker Desktop açık mı? |
| `docker: command not found` / Docker bağlanamıyor | Docker Desktop açık mı? "Engine running" bekleyin; yeni terminal açın. |
| `WSL 2 installation is incomplete` | Yönetici PowerShell: `wsl --update`, sonra yeniden başlat. |
| "Virtualization not enabled" | BIOS/UEFI'de Intel VT-x / AMD-V'yi açın. |
| "downloading QEMU portable" / çok yavaş açılış | `Image.linux()` VM seçmiş; `kind="container"` olmalı (session.py zaten böyle). |
| `no window has keyboard focus` | Masaüstü henüz açılmadı. session.py bekler ve xdotool'a düşer; tekrar ederse önce bir pencereye `desktop.click` yapın. |
| `ModuleNotFoundError: cua_sandbox` / `rapidocr` | `pip install -r backend\requirements-cua.txt` (doğru `.venv` ile). Araç Türkçe hata döner, backend çökmez. |
| OCR menüde öğe kaçırıyor | `pip show rapidocr-onnxruntime` kuruluysa kaldırın, `rapidocr==3.10.0` kurun. |
| `cv2` import hatası / garip OpenCV hataları | `opencv-python` ve `-headless` çakışması; bkz. bölüm 3. |
| İlk başlatma çok uzun | İmaj iniyor; `docker pull ghcr.io/trycua/linux:24.04` ile önceden çekin. |
| Konteyner açık kaldı (süreç öldürüldüyse) | `docker ps -a --filter ancestor=ghcr.io/trycua/linux:24.04` → `docker rm -f <ad>`. |
| Tıklama yanlış yere gidiyor | Log'daki "Sandbox screen size" satırına bakın, yeni `desktop.snapshot` alın. |
| `/shots/...` 404 | Dosya `backend/data/shots/` altında mı? Ad yalnızca `[A-Za-z0-9_-].png` olabilir. |

## 8. Demo modu: DESKTOP_FAKE=1 (Docker gerekmez)

Docker kurulu değilse ya da sahnede sorun çıkarsa, Pillow ile çizilmiş sahte bir masaüstü (basit "Metin Düzenleyici") kullanılır. Tüm akış (snapshot -> numaralı öğeler -> click/type/key/scroll -> `/shots/...` -> zaman çizelgesi) gerçek gibi çalışır; OCR bile gerekmez (CUA ve rapidocr paketleri kurulu olmasa da çalışır).

`backend/.env`:

```
CUA_ENABLED=true
DESKTOP_FAKE=1
```

Doğrulama: `$env:DESKTOP_FAKE="1"; .venv\Scripts\python.exe scripts\cua_smoke.py` (sahte oturumu kullanır) ve `.venv\Scripts\python.exe -m pytest tests/cua -q`.
Sahte ekranda tıklanabilir öğeler: Dosya, Düzenle, Görünüm, Kaydet (durum çubuğunu "Kaydedildi" yapar), metin alanı (tıklayınca odaklanır, `desktop.type` metin ekler).

## 9. Doğrulama kontrol listesi

1. `docker version` ve `docker run --rm hello-world` çalışıyor.
2. `docker images ghcr.io/trycua/linux` imajı gösteriyor.
3. `.venv\Scripts\python.exe scripts\cua_smoke.py` -> `OK` ve `backend/data/shots/` altında png'ler.
4. Backend açılışında log: "Kayıtlı araçlar" içinde `desktop.click, desktop.key, desktop.scroll, desktop.snapshot, desktop.type`.
5. Tarayıcıdan `http://localhost:8000/shots/<dosya>.png` görüntü döner; geçersiz ad 404 verir.

## 10. Yedek plan

- Docker/WSL2 çalışmazsa: `DESKTOP_FAKE=1` ile demo, ya da `CUA_ENABLED=false` (backend masaüstü araçları olmadan açılır; paketler eksik olsa bile çökmez, araç Türkçe hata döner).
- Zaman kalmazsa (PROTOCOL §9, 6. saat): CUA demosunu ekran kaydı olarak çek, canlı demoda tarayıcı senaryolarına odaklan.
