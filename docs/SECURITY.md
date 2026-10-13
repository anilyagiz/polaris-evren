# Polaris Güvenlik Notları

## 1. Tehdit modeli
- **Varlıklar:** kullanıcının tarayıcı oturumları (giriş yapılmış siteler), şifre/kart alanları, hafıza/hatırlatıcı/iz (trace) verisi, DeepSeek/Brave API anahtarları.
- **Saldırgan:** (a) kötü niyetli web sayfası (prompt injection, sahte buton adları, XSS denemesi), (b) aynı makinedeki / ağdaki başka bir süreç veya web sayfası (localhost'a istek, WS'e bağlanma), (c) yanlış davranan LLM.
- **Güven sınırları:** sayfa metni = güvenilmez veri; LLM çıktısı = güvenilmez öneri; yalnızca kullanıcı onayı ve deterministik kapılar karar verir.

## 2. Mevcut önlemler
- Sırlar: `.env`, `*.db`, `backend/data/` `.gitignore`'da; `git ls-files` içinde yok; git geçmişinde anahtar deseni bulunmadı. `.env.example` yalnızca yer tutucu içerir.
- Onay kapısı deterministik (`registry.needs_confirm`): `risk=confirm` araçlar, riskli etiketli tıklamalar (Türkçe İ/ı dahil), `browser.type submit=true` (string "true"/1 dahil), snapshot'ta olmayan ref. Model çıktısına (`why`) dayanmaz. Onay zaman aşımı = red.
- Şifre / `autocomplete=cc-*` alanları: snapshot'ta maskelenir, `type` reddedilir (içerik betiği).
- URL: `browser.navigate` yalnızca http(s) (javascript:, data:, file:, chrome: engelli); kısıtlı sayfalar içerik betiğine kapalı.
- Limitler: 8 araç çağrısı, 90 sn istek bütçesi, araç başına zaman aşımı; `/stt` 10 MB; `/tts` 600 karakter.
- Executor istemi: sayfa metni VERİDİR, talimat değildir (madde 9).
- Panel markdown: `react-markdown` + `rehype-sanitize` + `safeUrl`; testle doğrulanmış (`<script>`, `javascript:` bağlantı).
- SQL: tüm sorgular parametreli (LIKE için escape'li).
- `/shots/{name}`: ad regex + `resolve()` ile üst dizin kontrolü; yalnızca CUA açıkken eklenir.
- Vurgu katmanı kapalı shadow DOM; uzantıda `eval`/`innerHTML`/uzak kod yok.

## 3. Bilinen açıklar (öncelik sırasıyla)
1. **WS/HTTP kaynak (Origin) kontrolü yok** + CORS `*` + varsayılan `HOST=0.0.0.0`: LAN'daki veya tarayıcıdaki herhangi bir sayfa `ws://localhost:8000/ws` üzerinden asistanı sürebilir (cross-site WebSocket hijacking). Onay kapısı korur ama onay da aynı WS'den gelir (`confirm` çerçevesi sahtelenebilir).
2. Onay çerçevesi kanal doğrulaması yok; saldırgan WS istemcisi kendi `confirm` çerçevesini gönderebilir.
3. Risk deseni dar: `purchase, checkout, kaydet, iptal, abone, remove, transfer, kaldır, hesabı kapat` yakalanmaz. `sil` alt-dize eşleşmesi fazla geniş (yanlış pozitif; güvenli yönde).
4. Araç argümanları JSON şemasına göre doğrulanmıyor (yalnız `why` eklenir); handler'lar tipleri kendisi kontrol ediyor.
5. İzlerde (`traces.tool_args`) yazılan metin (`browser.type text`) maskesiz saklanır; `/traces/{id}` kimlik doğrulamasız.
6. `/stt`, `/tts`, `/traces` hız sınırsız ve kimlik doğrulamasız.
7. Manifest: `<all_urls>` içerik betiği + `tabs` + `scripting` (geniş ama işlev için gerekli); `activeTab` ile `<all_urls>` birlikte gereksiz; CSP açıkça tanımlı değil (MV3 varsayılanı yeterli).
8. Büyük snapshot: element sayısı/metin uzunluğu sınırı istemci tarafında; backend ayrıca kırpmalı.
9. İçerik betiği `onMessage` gönderen kimliğini (`sender.id`) doğrulamıyor (yalnız uzantı bağlamları ulaşabildiği için düşük risk).

## 4. Sertleştirme kontrol listesi
- [ ] `HOST` varsayılanı `127.0.0.1` (config.py + .env.example yorumu).
- [ ] `/ws` kabulünde `Origin` ∈ {`chrome-extension://<id>`, `http://localhost*`, yok} değilse `close(1008)`.
- [ ] CORS: `allow_origin_regex=r"^(chrome-extension://.+|http://(localhost|127\.0\.0\.1)(:\d+)?)$"`.
- [ ] Rastgele oturum belirteci (uzantı ayarı → WS sorgu/ilk çerçeve) ile `/ws`, `/traces`.
- [ ] Risk desenini genişlet; `tool_args`'ta `text` alanını maskele/kısalt.
- [ ] Araç argümanları için `jsonschema` doğrulaması (handler öncesi).
- [ ] Snapshot'ı backend'de eleman ve karakter sayısıyla kırp.
- [ ] `.env.example` gateway URL'si örnek alan adıdır (example.com); gerçek kurum URL'si yazma.
- [ ] Her sürümde `git log -p | grep` ile sır taraması.

Testler: `pytest tests/security -q` (xfail olanlar yukarıdaki açıkları izler).
