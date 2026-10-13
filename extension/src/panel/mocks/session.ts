import {
  parseEvent,
  type ClientEvent,
  type ServerEvent,
  type TraceStep,
} from "../protocol";
import type { OnEvent, OnStatus, Transport } from "../ws";

const fixtures = import.meta.glob<string>(
  "../../../../shared/fixtures/session_example.jsonl",
  { query: "?raw", import: "default", eager: true },
);
const agentStep = (
  agent: TraceStep["agent"],
  decision: string,
  why: string,
  extra: Partial<TraceStep> = {},
): TraceStep => ({
  agent,
  decision,
  why,
  evidence: [],
  confidence: 0.94,
  tool: null,
  scores: null,
  verdict: null,
  latency_ms: 240,
  tokens: 128,
  ts: new Date().toISOString(),
  ...extra,
});

export function createMockTransport(
  onEvent: OnEvent,
  onStatus: OnStatus,
): Transport {
  let queue: ServerEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let waiting: Extract<ServerEvent, { type: "need_confirm" }> | undefined;
  let currentId: string | undefined;
  let closed = false;
  const tick = () => {
    if (closed || waiting) return;
    const next = queue.shift();
    if (!next) return;
    if (next.type === "need_confirm") waiting = next;
    onEvent(next);
    if (!waiting) timer = setTimeout(tick, 300);
  };
  const enqueue = (items: ServerEvent[]) => {
    queue.push(...items);
    clearTimeout(timer);
    timer = setTimeout(tick, 300);
  };
  onStatus("connected");
  return {
    send(event: ClientEvent) {
      if (closed) return false;
      if (event.type === "user_msg") {
        currentId = event.id;
        queue = [];
        waiting = undefined;
        const fixture = Object.values(fixtures)[0];
        if (fixture) {
          try {
            enqueue(
              fixture
                .split(/\r?\n/)
                .filter((line) => line.trim())
                .map((line) => {
                  const item = parseEvent(line);
                  return "request_id" in item
                    ? { ...item, request_id: event.id }
                    : item;
                }),
            );
            return true;
          } catch {
            console.warn(
              "Polaris: shared/fixtures/session_example.jsonl geçersiz; panelin yerel demo akışı kullanılıyor.",
            );
          }
        }
        const trace = (step: TraceStep): ServerEvent => ({
          type: "trace",
          request_id: event.id,
          step,
        });
        const form = /form|gönder|satın|öde|sil/i.test(event.text);
        enqueue([
          trace(
            agentStep(
              "router",
              "Tarayıcı görevi seçildi",
              "İstek açık sayfadaki bilgilerle ilgili.",
            ),
          ),
          trace(
            agentStep(
              "planner",
              form ? "Alanları doldur, onay iste" : "Sayfayı oku ve özetle",
              "Görevi kısa ve kontrol edilebilir adımlara ayırdım.",
            ),
          ),
          {
            type: "tool_call",
            request_id: event.id,
            call_id: `c_${event.id}`,
            name: "browser.read",
            args: {},
          },
          trace(
            agentStep(
              "executor",
              "Sayfa içeriği okundu",
              "Cevap için görünür sayfa metnini kullandım.",
              {
                tool: { name: "browser.read", args: {} },
                evidence: ["ref:21", "url:https://example.com"],
              },
            ),
          ),
          trace(
            agentStep(
              "judge",
              form ? "Gönderme için onay gerekli" : "Cevap kontrol edildi",
              form
                ? "Form göndermek dışarıya veri iletir; kullanıcı onayı gerekiyor."
                : "Sonuç okunabilir ve kaynakla destekleniyor.",
              {
                scores: { correct: 5, complete: 5, safe: 5 },
                verdict: form ? "ask_user" : "pass",
              },
            ),
          ),
          ...(form
            ? [
                {
                  type: "need_confirm" as const,
                  request_id: event.id,
                  call_id: `c_confirm_${event.id}`,
                  summary:
                    "Örnek form gönderilsin mi? Bu bir mock gösterimidir; gerçek form gönderilmez.",
                  tool: "browser.click",
                  args: { ref: 52 },
                },
              ]
            : []),
          trace(
            agentStep(
              "responder",
              "Kısa cevap hazırlandı",
              "Özet ve kaynakları aynı cevapta birleştirdim.",
            ),
          ),
          {
            type: "final",
            request_id: event.id,
            answer: form
              ? "**Demo tamamlandı.** Onay seçimin kaydedildi; gerçek bir form gönderilmedi."
              : "### Sayfanın kısa özeti\nBu, **Polaris’in mock gösterimi**. Gerçek sayfa henüz okunmadı.\n\n- İsteğin sınıflandırıldı.\n- Örnek araç çağrısı ve kontrol adımları gösterildi.\n- **Neden?** bölümünden gerekçeleri inceleyebilirsin.\n\n| Adım | Durum |\n| --- | --- |\n| Okuma | Örnek veri |\n| Kontrol | Tamamlandı |",
            spoken:
              "Mock gösterimi tamamlandı. Adımların gerekçelerini Neden bölümünde görebilirsin.",
            citations: [
              { title: "Örnek kaynak", url: "https://example.com", ref: 21 },
            ],
          },
        ]);
      } else if (
        event.type === "confirm" &&
        waiting?.call_id === event.call_id
      ) {
        const request_id = waiting.request_id;
        waiting = undefined;
        if (!event.approved)
          queue = [
            {
              type: "trace",
              request_id,
              step: agentStep(
                "system",
                "İşlem reddedildi",
                "Kullanıcı onay vermedi; örnek işlem durduruldu.",
              ),
            },
            {
              type: "final",
              request_id,
              answer: "İşlem reddedildi. **Form gönderilmedi.**",
              spoken: "İşlem reddedildi.",
              citations: [],
            },
          ];
        timer = setTimeout(tick, 300);
      } else if (event.type === "cancel" && currentId === event.id) {
        clearTimeout(timer);
        queue = [];
        waiting = undefined;
        currentId = undefined;
      }
      return true;
    },
    close() {
      closed = true;
      clearTimeout(timer);
      queue = [];
      waiting = undefined;
    },
  };
}
