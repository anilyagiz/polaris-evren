# Polaris Eklentisi (MV3)

## Derleme
```
cd extension
pnpm install
pnpm build        # tsc --noEmit + vite build -> dist/
pnpm typecheck    # yalnızca tip kontrolü
pnpm test         # vitest (panel + content + bridge)
pnpm dev          # geliştirme (crxjs HMR)
pnpm icons        # public/icons/*.png yeniden üretir
```

## Brave'e yükleme
1. `brave://extensions` adresini aç.
2. Sağ üstten **Geliştirici modu**nu aç.
3. **Paketlenmemiş öğe yükle** -> `extension/dist` klasörünü seç.
4. Eklenti simgesine tıkla; yan panel açılır.
5. Backend `http://localhost:8000` üzerinde çalışmalıdır (ws://localhost:8000 bağlantısı CSP'de izinlidir).

## Mikrofon izni
Yan paneller mikrofon izni isteyemez. İlk kullanımda panel, `permission.html` sekmesini açar; "Mikrofon izni ver" düğmesine bas ve Brave'in izin penceresinde **İzin ver**'i seç. Sonra yan panele dön. İzin reddedildiyse adres çubuğundaki kilit simgesinden sıfırla.

## Notlar
- Dev test sayfası (`src/dev/tester.html`) yalnızca `pnpm dev` ile erişilir, üretim paketine girmez.
- Kod değişince `pnpm build` sonrası `brave://extensions` içinde eklentiyi yenile.
