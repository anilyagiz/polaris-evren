# Polaris sistem tepsisi (Polaris.exe)

Son kullanıcı hiçbir terminal penceresi görmeden Polaris'i çalıştırsın diye
Windows görev çubuğunun sağındaki bildirim alanında (sistem tepsisi) duran
küçük bir uygulama. Kod: `tray/`. Testler: `tests/tray/`.

## Ne yapar?

- Backend'i **gizli bir alt süreç** olarak başlatır:
  `.venv\Scripts\pythonw.exe -m backend --host <HOST> --port <PORT>`
  (`backend\__main__.py` yoksa `pythonw -m uvicorn backend.main:app ...`).
  Konsol penceresi açılmaz (`CREATE_NO_WINDOW`). Çıktı:
  `backend\data\logs\tray-backend.log` (5 MB'ı geçince `.1` olarak döner).
- `HOST`/`PORT` değerlerini `backend\.env` dosyasından okur (ortam değişkeni
  öncelikli, varsayılan `127.0.0.1:8765`).
- Her 5 saniyede bir `GET /health` yapar ve simgeyi günceller:

  | Simge | İpucu metni | Anlamı |
  |---|---|---|
  | Altın yıldız | `Polaris · çalışıyor · model X` | Sunucu hazır |
  | Kırmızı yıldız | `Polaris · LLM anahtarı eksik` | Sunucu açık ama `DEEPSEEK_API_KEY` yok |
  | Kırmızı yıldız | `Polaris · hata · …` / `sunucu kapandı` | Port dolu, başlatılamadı ya da çöktü |
  | Gri yıldız | `Polaris · başlıyor…` / `Polaris · durdu` | Açılıyor ya da kullanıcı durdurdu |

- İlk başarılı açılışta bildirim gösterir: **"Polaris hazır — Brave'de paneli aç"**.
- Tepsi uygulaması kapanırsa (Görev Yöneticisi'nden öldürülse bile) backend de
  kapanır (Windows Job Object). Aynı anda yalnızca **bir** tepsi çalışır
  (adlandırılmış mutex `Local\PolarisTraySingleInstance`); ikinci açılış
  "Polaris zaten çalışıyor" der ve çıkar.
- Port başka bir **Polaris** backend'i tarafından kullanılıyorsa ona bağlanır
  ve sadece izler. Başka bir program kullanıyorsa nasıl port değiştirileceğini
  anlatan bir uyarı kutusu açar.

### Menü

| Öğe | Ne yapar |
|---|---|
| Durum: … | Bilgi satırı (tıklanmaz) |
| Sunucuyu yeniden başlat | Durdurur, `.env`'i yeniden okur (yeni PORT dahil), başlatır |
| Sunucuyu durdur / başlat | Backend'i kapatır ya da açar |
| Ayarlar dosyasını aç | `backend\.env`'i Not Defteri'nde açar (yoksa `.env.example`'dan oluşturur) |
| Kayıtları aç | `backend\data\logs` klasörü (`polaris.log`, `tray-backend.log`, `tray.log`) |
| Eklenti klasörünü aç | `extension\dist` klasörünü Gezgin'de açar |
| Brave'e yükleme adımları | Adım adım yükleme talimatı gösterir |
| Windows açılışında başlat | Otomatik başlatmayı açar/kapatır (onay işareti durumu gösterir) |
| Çıkış | Backend'i durdurur (önce terminate, 5 sn sonra kill) ve tepsiden çıkar |

## Çalıştırma

```powershell
# Kaynaktan (konsollu, geliştirme için)
.\.venv\Scripts\python -m tray

# Kaynaktan, konsolsuz
.\.venv\Scripts\pythonw tray\launch.pyw      # ya da tray\Polaris.vbs'e çift tıkla

# Derlenmiş
.\Polaris.exe
```

Bağımlılıklar: `.venv\Scripts\python -m pip install -r backend\requirements-tray.txt`
(pystray + Pillow).

### Smoke testi

```powershell
.\.venv\Scripts\python -m tray --smoke            # boş bir portta başlat, /health ok bekle, durdur
.\.venv\Scripts\python -m tray --smoke --port 8010 --timeout 60
```

Çıkış kodu 0 = başarılı. `Polaris.exe --smoke` da çalışır (pencere yok; çıktı `tray.log`'a yazılır).

## Polaris.exe derleme

```powershell
powershell -ExecutionPolicy Bypass -File tray\build.ps1          # -Smoke: derleme sonrası test
```

- Çıktı: `tray\dist\Polaris.exe` (~15 MB) ve `tray\dist\polaris.ico`.
- Exe **yalnızca** tepsi kodunu + pystray + Pillow'u içerir; backend'i
  paketlemez. Açılışta kendi klasöründen yukarı doğru `backend\` klasörünü arar
  ve o kökteki `.venv` ile backend'i başlatır.
- **Sürüm paketi:** `Polaris.exe`'yi `backend\` ve `.venv\` ile aynı klasöre
  (repo kökü ya da release kökü) kopyalayın. (`POLARIS_ROOT` ortam değişkeni
  ile kök elle de verilebilir.)
- PyInstaller kullanılamıyorsa yedek: `tray\Polaris.vbs` (konsolsuz başlatır).
  Kısayol için hedef `wscript.exe "<kök>\tray\Polaris.vbs"`, simge
  `tray\dist\polaris.ico`.

## Windows açılışında başlatma

Menüdeki **Windows açılışında başlat** şu kaydı yazar/siler:

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run   Polaris = "<kök>\Polaris.exe"
```

Exe yoksa değer `"<kök>\.venv\Scripts\pythonw.exe" "<kök>\tray\launch.pyw"` olur.
Kurulum klasörü taşınırsa tepsi ilk açılışta kaydı yeni yola günceller.
Yönetici yetkisi gerekmez (yalnızca geçerli kullanıcı).

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Simge kırmızı, "port 8765 dolu" uyarısı | Başka bir program 8765'i kullanıyor. **Ayarlar dosyasını aç** → `PORT=8010` ekle → kaydet → **Sunucuyu yeniden başlat**. Brave paneli → Ayarlar → Backend adresi: `http://localhost:8010` |
| Simge kırmızı, "LLM anahtarı eksik" | `backend\.env` içine `DEEPSEEK_API_KEY=...` yaz, kaydet, yeniden başlat |
| Simge kırmızı, "sunucu kapandı" | **Kayıtları aç** → `tray-backend.log` sonundaki hatayı oku (çoğunlukla eksik paket: `scripts\setup.ps1`'i tekrar çalıştır) |
| "Python ortamı bulunamadı" / sunucu başlatılamadı | `.venv` eksik ya da exe yanlış klasörde. Exe, `backend\` ve `.venv\` ile aynı klasörde olmalı |
| Simge hiç görünmüyor | Görev çubuğundaki `^` okuna bak; Ayarlar → Kişiselleştirme → Görev çubuğu → "Görev çubuğunda hangi simgelerin görüneceğini seçin" → Polaris'i aç |
| "Polaris zaten çalışıyor" ama simge yok | Görev Yöneticisi'nde `Polaris.exe` / `pythonw.exe` (launch.pyw) sürecini sonlandır, tekrar aç |
| Bildirim gelmiyor | Windows Odak Yardımı / Bildirimler kapalı olabilir; işlev etkilenmez |
| Panel "Bağlantı yok" ama simge altın | Paneldeki Backend adresinin portu tepsi menüsündeki "Durum" satırındaki portla aynı mı? |
