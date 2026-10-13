# Polaris kurulum sihirbazı (`PolarisSetup-<sürüm>.exe`)

Windows 10/11 x64 için kullanıcı başına kurulum (yönetici izni yok). Sistemde Python veya Node.js gerekmez.

## Kullanıcı ne görür

1. **Hoş geldiniz** → **Klasör** (`%LOCALAPPDATA%\Programs\Polaris`) → **Seçenekler**:
   - Masaüstüne "Polaris" kısayolu ekle (açık)
   - Ses modelini şimdi indir (~460 MB) (açık)
   - Windows açılışında Polaris'i başlat (kapalı; tepsi menüsündeki aynı ayarla aynı kayıt: `HKCU\...\Run\Polaris`)
2. Brave yoksa: **Brave tarayıcısını da kur (önerilir)** (winget `Brave.Brave --scope user`, olmazsa resmi `BraveBrowserSetup`).
3. **Kur** → dosyalar kopyalanır → **"Polaris hazırlanıyor"** sayfası: Python 3.12 → sanal ortam → paketler →
   ayar dosyası → ses modeli → TTS. Konsol penceresi açılmaz; günlük `{app}\logs\install.log`.
   Hata olursa: *Yeniden dene* / *Ayrıntıları göster* (günlüğü açar) / *Daha sonra*.
4. **Bitiş**: "Polaris'i başlat" ve "Brave kurulum rehberini aç" (ikisi de işaretli). Rehber
   (`{app}\guide\guide.html`) Brave varsa Brave'de açılır; eklenti klasörünün tam yolunu kopyalayan düğmesi vardır.

Başlat menüsü → **Polaris**: *Polaris*, *Polaris'i onar*, *Brave'e eklentiyi yükle (rehber)*, *Polaris'i kaldır*.

## Ne nereye kurulur

| Yol | İçerik |
|---|---|
| `{app}\Polaris.exe` | tepsi uygulaması (backend'i `.venv` ile gizli başlatır; 8765 doluysa 8766+) |
| `{app}\backend`, `shared`, `scripts`, `tray`, `docs` | kaynak; `backend\.env` ilk kurulumda `.env.example`'dan |
| `{app}\extension-dist` | Brave'e "paketlenmemiş" yüklenecek eklenti |
| `{app}\tools\uv\uv.exe` | sabit sürüm uv (Astral), SHA256 doğrulanmış |
| `{app}\runtime\python` | uv'nin indirdiği CPython 3.12 (registry/PATH'e yazılmaz) |
| `{app}\runtime\uv-cache` | paket önbelleği (onarımı hızlandırır) |
| `{app}\.venv` | sanal ortam |
| `%USERPROFILE%\.cache\huggingface` | whisper / EMA modelleri (paylaşılan önbellek; kaldırınca silinmez) |

`scripts\bootstrap.ps1` idempotenttir: tekrar çalıştırmak yalnızca eksikleri tamamlar.
ASCII dışı kullanıcı adlarında (ör. `C:\Users\anıl`) uv'nin "3.12" bağlantı klasörü (junction)
oluşturulamazsa indirilen Python doğrudan kullanılır (`@@WARN pylink`, zararsız).

## Güncelleme

Yeni `PolarisSetup` aynı klasöre kurulur: çalışan Polaris kapatılır, `backend\.env`, `backend\data`
ve `.venv` korunur, yalnızca paketler eşitlenir.

## Kaldırma

Ayarlar → Uygulamalar → Polaris. Süreçler kapatılır; kurulum klasörü (`.venv`, `runtime`, `backend\.env`
dahil), kısayollar ve bu kuruluma ait `Run` kaydı silinir. Konuşma geçmişi (`backend\data`) için sorulur
(sessiz kaldırmada silinir). API anahtarı her durumda silinir.

## Sessiz kurulum / test

```powershell
PolarisSetup-0.1.1.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR="C:\Test\Polaris" /TASKS="desktopicon,voicemodel" /NOBRAVE=1 /LOG="kurulum.log"
"C:\Test\Polaris\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES
```

Görevler: `desktopicon`, `voicemodel`, `autostart`. `/NOBRAVE=1` Brave kurulumunu atlar.

## Derleme

```powershell
winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
.\scripts\package-release.ps1            # zip'ler + release\PolarisSetup-<sürüm>.exe
# yalnızca sihirbaz (hazır stage ile): installer\build-installer.ps1 -StageDir <klasör>
```

`installer\polaris.iss` (Inno Setup betiği), `installer\guide.html` (Brave rehberi),
`installer\make_assets.py` (ikon + sihirbaz görselleri, Pillow). uv sürümü/SHA256'sı
`installer\build-installer.ps1` içinde sabittir. Çıktılar: `installer\build\`, `installer\.cache\` (git dışı).
