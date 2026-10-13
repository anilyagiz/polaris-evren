export interface FriendlyError {
  title: string;
  hint: string;
  /** The original server text, kept for troubleshooting. */
  detail: string;
}

const rules: Array<[RegExp, string, string]> = [
  [
    /429|istek sınırı|rate.?limit/i,
    "Yapay zekâ servisi şu an yoğun",
    "Birkaç saniye bekleyip tekrar dene.",
  ],
  [
    /deepseek_api_key|kimlik doğrulama|unauthori[sz]ed|\b40[13]\b|api.?key/i,
    "Yapay zekâ anahtarı kabul edilmedi",
    "backend/.env içindeki DEEPSEEK_API_KEY değerini kontrol et ve sunucuyu yeniden başlat.",
  ],
  [
    /llm_model|deepseek_base_url|bulunamadı.*model|model.*bulunamadı|\b404\b/i,
    "Model ayarı hatalı",
    "backend/.env içinde LLM_MODEL ve DEEPSEEK_BASE_URL değerlerini kontrol et, sunucuyu yeniden başlat.",
  ],
  [
    /zaman aşımı|timeout|timed out/i,
    "Yanıt zamanında gelmedi",
    "Tekrar dene. Uzun görevleri daha küçük isteklere bölmek yardımcı olur.",
  ],
  [
    /geçici hata|\b5\d\d\b/i,
    "Yapay zekâ servisi geçici olarak yanıt vermiyor",
    "Biraz sonra tekrar dene.",
  ],
  [
    /bağlanılamadı|connection|internet/i,
    "Yapay zekâ servisine ulaşılamadı",
    "İnternet bağlantını ve backend/.env içindeki DEEPSEEK_BASE_URL değerini kontrol et.",
  ],
  [
    /geçerli json döndürmedi|boş\/bozuk/i,
    "Model anlaşılır bir yanıt üretemedi",
    "İsteği biraz farklı ifade ederek tekrar dene.",
  ],
  [
    /bütçe|sınırı doldu/i,
    "Görev beklenenden uzun sürdü",
    "İsteği daha küçük adımlara bölerek tekrar dene.",
  ],
  [
    /orchestrator|sunucu hazırlanamadı/i,
    "Sunucu hazırlanamadı",
    "Backend terminalindeki hatayı kontrol edip sunucuyu yeniden başlat.",
  ],
  [
    /geçersiz json|geçersiz frame|protokole uymuyor/i,
    "Panel ile sunucu anlaşamadı",
    "Uzantıyı ve sunucuyu aynı sürüme güncelleyip paneli yeniden aç.",
  ],
];

/** Maps raw server/LLM error text to a short Turkish explanation + next step. */
export function friendlyError(message: string): FriendlyError {
  const detail = message.trim();
  for (const [pattern, title, hint] of rules)
    if (pattern.test(detail)) return { title, hint, detail };
  return {
    title: "Bir sorun oluştu",
    hint: "Tekrar dene. Sorun sürerse backend terminalindeki kayıtlara bak.",
    detail,
  };
}

/** Trace steps the orchestrator emits for failed tool calls. */
export function isToolFailure(decision: string): boolean {
  return /^araç (hatası|zaman aşımı)|başarısız/i.test(decision.trim());
}
