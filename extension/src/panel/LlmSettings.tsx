import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ClipboardPaste,
  CloudOff,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  PlugZap,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import {
  getLlmSettings,
  LlmApiError,
  listModels,
  prettyModel,
  putLlmSettings,
  testLlm,
  type LlmModel,
  type LlmPreset,
  type LlmSettings,
  type LlmUpdate,
  type ProviderId,
} from "./llm";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; saved: LlmSettings }
  | { kind: "missing" }
  | { kind: "error"; message: string };
type TestState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "ok"; text: string }
  | { kind: "fail"; text: string };

const providerOrder: ProviderId[] = ["evren", "deepseek", "custom"];
const providerName = (preset: LlmPreset) =>
  preset.id === "custom" ? "Diğer" : preset.label || preset.id;
const isHttpUrl = (value: string) => {
  try {
    return /^https?:$/.test(new URL(value.trim()).protocol);
  } catch {
    return false;
  }
};
const withModel = (models: LlmModel[], id: string | null, note: string) =>
  !id || models.some((model) => model.id === id)
    ? models
    : [...models, { id, label: prettyModel(id), note }];

/**
 * Draft state for the "Yapay zeka" settings section. The API key only lives in
 * this component's memory and is sent to the backend via PUT — never stored.
 */
export function useLlmDraft(backend: string) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [provider, setProvider] = useState<ProviderId>("evren");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [fallback, setFallback] = useState<string | null>(null);
  const [thinking, setThinking] = useState<"on" | "off">("on");
  const [models, setModels] = useState<LlmModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [editingKey, setEditingKey] = useState(false);
  const [braveKey, setBraveKey] = useState("");
  const [test, setTest] = useState<TestState>({ kind: "idle" });
  const [saveError, setSaveError] = useState("");
  const modelsSeq = useRef(0);

  const presets: LlmPreset[] =
    load.kind === "ready"
      ? providerOrder.map(
          (id) =>
            load.saved.presets.find((preset) => preset.id === id) ?? {
              id,
              label: id,
              base_url: "",
              key_hint: "",
              models: [],
              recommended: null,
            },
        ).filter(
          (preset) =>
            preset.id === "custom" ||
            load.saved.presets.some((item) => item.id === preset.id),
        )
      : [];
  const preset = presets.find((item) => item.id === provider);

  const adopt = useCallback((saved: LlmSettings) => {
    const current = saved.presets.find((item) => item.id === saved.provider);
    setProvider(saved.provider);
    setBaseUrl(saved.base_url || current?.base_url || "");
    setModel(saved.model);
    setFallback(saved.fallback_model);
    setThinking(saved.thinking);
    setModels(
      withModel(
        withModel(current?.models ?? [], saved.model, "Kayıtlı model"),
        saved.fallback_model,
        "Kayıtlı yedek",
      ),
    );
    setNewKey("");
    setEditingKey(false);
    setBraveKey("");
    setLoad({ kind: "ready", saved });
  }, []);

  const reload = useCallback(async () => {
    setLoad({ kind: "loading" });
    try {
      adopt(await getLlmSettings(backend));
    } catch (cause) {
      if (cause instanceof LlmApiError && cause.status === 404)
        setLoad({ kind: "missing" });
      else
        setLoad({
          kind: "error",
          message:
            cause instanceof Error ? cause.message : "Ayarlar yüklenemedi.",
        });
    }
  }, [backend, adopt]);
  useEffect(() => {
    void reload();
  }, [reload]);

  const fetchModels = async (id: ProviderId, url: string, keep: string[]) => {
    if (!isHttpUrl(url)) return;
    const sequence = ++modelsSeq.current;
    setModelsLoading(true);
    try {
      const list = await listModels(backend, url, id);
      if (sequence !== modelsSeq.current || !list.length) return;
      let next = list;
      for (const extra of keep) next = withModel(next, extra, "Seçili model");
      setModels(next);
    } catch {
      /* Keep the preset list; the picker stays usable offline. */
    } finally {
      if (sequence === modelsSeq.current) setModelsLoading(false);
    }
  };

  const chooseProvider = (id: ProviderId) => {
    if (load.kind !== "ready" || id === provider) return;
    const saved = load.saved;
    const next = presets.find((item) => item.id === id);
    setTest({ kind: "idle" });
    setSaveError("");
    setProvider(id);
    if (id === saved.provider) {
      adopt(saved);
      void fetchModels(id, saved.base_url, [saved.model]);
      return;
    }
    const url = id === "custom" ? "" : (next?.base_url ?? "");
    const list = next?.models ?? [];
    const rec = next?.recommended;
    const chosen = rec?.model ?? list[0]?.id ?? "";
    setBaseUrl(url);
    setModels(withModel(list, chosen, ""));
    setModel(chosen);
    setFallback(rec?.fallback_model ?? null);
    setThinking(rec?.thinking ?? "on");
    setEditingKey(true);
    if (url) void fetchModels(id, url, chosen ? [chosen] : []);
  };

  const saved = load.kind === "ready" ? load.saved : null;
  const keyEditing =
    !saved || !saved.api_key_set || editingKey || provider !== saved.provider;
  const invalid =
    !saved
      ? null
      : !isHttpUrl(baseUrl)
        ? "Geçerli bir http:// veya https:// sağlayıcı adresi yaz."
        : !model.trim()
          ? "Bir model seç ya da model adını yaz."
          : null;
  const changes: LlmUpdate = {};
  if (saved) {
    if (baseUrl.trim() !== saved.base_url) changes.base_url = baseUrl.trim();
    if (model.trim() !== saved.model) changes.model = model.trim();
    if (fallback !== saved.fallback_model) changes.fallback_model = fallback;
    if (thinking !== saved.thinking) changes.thinking = thinking;
    if (newKey.trim()) changes.api_key = newKey.trim();
    if (braveKey.trim()) changes.brave_api_key = braveKey.trim();
  }
  const dirty = Object.keys(changes).length > 0;

  const runTest = async () => {
    if (!saved || invalid || test.kind === "testing") return;
    setTest({ kind: "testing" });
    try {
      const result = await testLlm(backend, {
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(newKey.trim() ? { api_key: newKey.trim() } : {}),
      });
      // The backend already writes the duration into its message ("· 2,8 sn").
      const latency =
        result.ok && result.latency_ms != null && !/sn|ms/.test(result.message)
          ? ` · ${Math.round(result.latency_ms)} ms`
          : "";
      setTest({
        kind: result.ok ? "ok" : "fail",
        text: `${result.message}${latency}`,
      });
    } catch (cause) {
      setTest({
        kind: "fail",
        text: cause instanceof Error ? cause.message : "Test yapılamadı.",
      });
    }
  };

  /** PUTs the changed fields. Returns the new settings, or null if nothing changed. */
  const save = async (): Promise<LlmSettings | null> => {
    if (!saved || !dirty) return null;
    if (invalid) {
      setSaveError(invalid);
      throw new Error(invalid);
    }
    setSaveError("");
    try {
      const next = await putLlmSettings(backend, changes);
      adopt(next);
      return next;
    } catch (cause) {
      const message =
        cause instanceof Error ? cause.message : "Yapay zekâ ayarları kaydedilemedi.";
      setSaveError(message);
      throw new Error(message);
    }
  };

  return {
    load,
    reload,
    presets,
    preset,
    provider,
    chooseProvider,
    baseUrl,
    setBaseUrl: (value: string) => {
      setBaseUrl(value);
      setTest({ kind: "idle" });
    },
    refreshModels: () => void fetchModels(provider, baseUrl, [model]),
    model,
    setModel: (value: string) => {
      setModel(value);
      if (fallback === value) setFallback(null);
      setTest({ kind: "idle" });
    },
    fallback,
    setFallback,
    thinking,
    setThinking,
    models,
    modelsLoading,
    newKey,
    setNewKey: (value: string) => {
      setNewKey(value.trim());
      setTest({ kind: "idle" });
    },
    keyEditing,
    setEditingKey: (value: boolean) => {
      setEditingKey(value);
      if (!value) setNewKey("");
    },
    braveKey,
    setBraveKey,
    test,
    runTest,
    invalid,
    dirty,
    save,
    saveError,
  };
}
export type LlmDraft = ReturnType<typeof useLlmDraft>;

export function LlmSection({
  llm,
  focusKey,
}: {
  llm: LlmDraft;
  focusKey: boolean;
}) {
  const keyInput = useRef<HTMLInputElement>(null);
  const keyBlock = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  const [showKey, setShowKey] = useState(false);
  const [pasteNote, setPasteNote] = useState("");
  const { load } = llm;
  const ready = load.kind === "ready";
  const editing = llm.keyEditing;
  const setEditing = llm.setEditingKey;

  // "Anahtarı gir": open straight at the key field once the settings arrive.
  useEffect(() => {
    if (!focusKey || focused.current || !ready) return;
    if (!editing) {
      setEditing(true);
      return;
    }
    focused.current = true;
    keyBlock.current?.scrollIntoView?.({ block: "center" });
    keyInput.current?.focus();
  }, [focusKey, ready, editing, setEditing]);

  // "Değiştir" swaps the chip for the field; move focus into it.
  const focusOnEdit = useRef(false);
  useEffect(() => {
    if (editing && focusOnEdit.current) {
      focusOnEdit.current = false;
      keyInput.current?.focus();
    }
  }, [editing]);

  const paste = async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (!text) setPasteNote("Pano boş görünüyor.");
      else {
        llm.setNewKey(text);
        setPasteNote("");
      }
    } catch {
      setPasteNote("Panoya erişilemedi; alana tıklayıp Ctrl+V ile yapıştır.");
    }
    keyInput.current?.focus();
  };

  if (load.kind === "loading")
    return (
      <section className="llm-section" aria-busy="true" aria-label="Yapay zeka">
        <SectionHead />
        <div className="llm-skeleton" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <p className="field-help">Yapay zekâ ayarları yükleniyor…</p>
      </section>
    );
  if (load.kind === "missing" || load.kind === "error")
    return (
      <section className="llm-section" aria-label="Yapay zeka">
        <SectionHead />
        <div className="llm-unavailable" role="note">
          <CloudOff size={16} />
          <div>
            <strong>
              {load.kind === "missing"
                ? "Sunucuyu güncelle"
                : "Yapay zekâ ayarları yüklenemedi"}
            </strong>
            <p>
              {load.kind === "missing"
                ? "Bu sunucu sürümü panelden anahtar ve model seçmeyi desteklemiyor. Polaris’i güncelledikten sonra burada görünecek."
                : `${load.message} Sunucu adresini aşağıdaki Gelişmiş bölümünden kontrol edebilirsin.`}
            </p>
            {load.kind === "error" && (
              <button
                type="button"
                className="text-button"
                onClick={() => void llm.reload()}
              >
                <RefreshCw size={13} /> Tekrar dene
              </button>
            )}
          </div>
        </div>
      </section>
    );

  const saved = load.saved;
  const preset = llm.preset;
  const recommended = preset?.recommended?.model ?? null;
  const fallbackOptions = llm.models.filter((item) => item.id !== llm.model);
  const custom = llm.provider === "custom";
  const providerChanged = llm.provider !== saved.provider;

  return (
    <section className="llm-section" aria-labelledby="llm-title">
      <SectionHead configured={saved.llm_configured && !providerChanged} />

      <fieldset className="llm-field">
        <legend className="field-label">Sağlayıcı</legend>
        <div className="segmented">
          {llm.presets.map((item) => (
            <label
              key={item.id}
              className={item.id === llm.provider ? "selected" : ""}
            >
              <input
                type="radio"
                name="llm-provider"
                value={item.id}
                checked={item.id === llm.provider}
                onChange={() => llm.chooseProvider(item.id)}
              />
              <span>{providerName(item)}</span>
            </label>
          ))}
        </div>
        {custom ? (
          <div className="llm-subfield">
            <label className="field-label" htmlFor="llm-base-url">
              Sağlayıcı adresi
            </label>
            <div className="field-with-action">
              <input
                id="llm-base-url"
                type="url"
                inputMode="url"
                spellCheck={false}
                autoComplete="off"
                placeholder="https://api.ornek.com/v1"
                value={llm.baseUrl}
                aria-describedby="llm-base-help"
                onChange={(event) => llm.setBaseUrl(event.target.value)}
                onBlur={() => llm.refreshModels()}
              />
              <button
                type="button"
                className="icon-button bordered"
                aria-label="Modelleri getir"
                title="Modelleri getir"
                disabled={llm.modelsLoading}
                onClick={() => llm.refreshModels()}
              >
                <RefreshCw
                  size={14}
                  className={llm.modelsLoading ? "spin" : ""}
                />
              </button>
            </div>
            <p id="llm-base-help" className="field-help">
              OpenAI uyumlu bir uç nokta (…/v1). Modeller adresten otomatik
              okunur.
            </p>
          </div>
        ) : (
          <p className="llm-base-line" title={llm.baseUrl}>
            {llm.baseUrl}
          </p>
        )}
      </fieldset>

      <div className="llm-field" ref={keyBlock}>
        {editing ? (
          <>
            <label className="field-label" htmlFor="llm-api-key">
              API anahtarı
            </label>
            <div className="key-row">
              <div className="key-input">
                <KeyRound size={14} aria-hidden="true" />
                <input
                  ref={keyInput}
                  id="llm-api-key"
                  type={showKey ? "text" : "password"}
                  spellCheck={false}
                  autoComplete="off"
                  autoCapitalize="off"
                  placeholder={preset?.key_hint || "Anahtarını yapıştır"}
                  value={llm.newKey}
                  aria-describedby="llm-key-help"
                  onChange={(event) => llm.setNewKey(event.target.value)}
                  onPaste={(event) => {
                    const text = event.clipboardData.getData("text");
                    if (!text) return;
                    event.preventDefault();
                    llm.setNewKey(text);
                    setPasteNote("");
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                      event.preventDefault();
                      void llm.runTest();
                    }
                  }}
                />
                <button
                  type="button"
                  className="key-eye"
                  aria-label={showKey ? "Anahtarı gizle" : "Anahtarı göster"}
                  aria-pressed={showKey}
                  onClick={() => setShowKey((value) => !value)}
                >
                  {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
              <button
                type="button"
                className="secondary compact"
                onClick={() => void paste()}
              >
                <ClipboardPaste size={14} /> Yapıştır
              </button>
            </div>
            <p id="llm-key-help" className="field-help">
              {pasteNote ||
                (saved.api_key_set
                  ? providerChanged
                    ? "Yeni sağlayıcının anahtarını gir. Boş bırakırsan kayıtlı anahtar korunur."
                    : "Boş bırakırsan kayıtlı anahtar korunur."
                  : "Anahtar yalnızca kendi Polaris sunucuna gönderilir; tarayıcıda saklanmaz.")}
            </p>
            {saved.api_key_set && !providerChanged && (
              <button
                type="button"
                className="text-button flush"
                onClick={() => {
                  setEditing(false);
                  setPasteNote("");
                }}
              >
                Vazgeç, kayıtlı anahtarı kullan
              </button>
            )}
          </>
        ) : (
          <>
            <span className="field-label">API anahtarı</span>
            <div className="key-chip">
              <span className="key-chip-icon">
                <Check size={13} />
              </span>
              <span>
                <span>Anahtar kayıtlı</span>
                {saved.api_key_hint && (
                  <>
                    {" · "}
                    <code>{saved.api_key_hint}</code>
                  </>
                )}
              </span>
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  focusOnEdit.current = true;
                  setEditing(true);
                }}
              >
                Değiştir
              </button>
            </div>
          </>
        )}
        <div className="test-row">
          <button
            type="button"
            className="secondary compact"
            disabled={llm.test.kind === "testing" || Boolean(llm.invalid)}
            onClick={() => void llm.runTest()}
          >
            {llm.test.kind === "testing" ? (
              <LoaderCircle size={14} className="spin" />
            ) : (
              <PlugZap size={14} />
            )}
            {llm.test.kind === "testing" ? "Deneniyor…" : "Bağlantıyı test et"}
          </button>
        </div>
        <div aria-live="polite" className="test-live">
          {(llm.test.kind === "ok" || llm.test.kind === "fail") && (
            <p className={`test-result ${llm.test.kind}`}>
              {llm.test.kind === "ok" ? (
                <Check size={13} aria-label="Başarılı" />
              ) : (
                <TriangleAlert size={13} aria-label="Hata" />
              )}
              <span>{llm.test.text}</span>
            </p>
          )}
        </div>
      </div>

      <fieldset className="llm-field">
        <legend className="field-label">
          Model
          {llm.modelsLoading && (
            <LoaderCircle size={12} className="spin legend-spin" aria-label="Modeller yükleniyor" />
          )}
        </legend>
        {llm.models.length > 0 && (
          <div className="model-cards">
            {llm.models.map((item) => (
              <label
                key={item.id}
                className={`model-card ${item.id === llm.model ? "selected" : ""}`}
              >
                <input
                  type="radio"
                  name="llm-model"
                  value={item.id}
                  checked={item.id === llm.model}
                  onChange={() => llm.setModel(item.id)}
                />
                <span className="model-radio" aria-hidden="true" />
                <span className="model-text">
                  <span className="model-title">
                    <strong>{item.label}</strong>
                    {item.id === recommended && (
                      <span className="badge-recommended">Önerilen</span>
                    )}
                  </span>
                  {item.note && <small>{item.note}</small>}
                  <code>{item.id}</code>
                </span>
              </label>
            ))}
          </div>
        )}
        {(custom || llm.models.length === 0) && (
          <div className="llm-subfield">
            <label className="field-label subtle" htmlFor="llm-model-id">
              Model adı
            </label>
            <input
              id="llm-model-id"
              className="text-input mono"
              spellCheck={false}
              autoComplete="off"
              placeholder="örn. gpt-4o-mini"
              value={llm.model}
              onChange={(event) => llm.setModel(event.target.value)}
            />
          </div>
        )}
      </fieldset>

      <div className="llm-field">
        <label className="field-label" htmlFor="llm-fallback">
          Yedek model
        </label>
        <div className="select-wrap">
          <select
            id="llm-fallback"
            value={llm.fallback ?? ""}
            aria-describedby="llm-fallback-help"
            onChange={(event) => llm.setFallback(event.target.value || null)}
          >
            <option value="">Yok</option>
            {fallbackOptions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <p id="llm-fallback-help" className="field-help">
          Ana model yanıt vermez ya da hata verirse istek otomatik olarak buna
          geçer.
        </p>
      </div>

      <label className="setting-row llm-fast">
        <span>
          <strong>Hızlı mod</strong>
          <small>
            Gizli düşünme adımını kapatır; Gemma’da yanıtlar ~5 kat hızlı.
          </small>
        </span>
        <input
          type="checkbox"
          role="switch"
          className="switch"
          checked={llm.thinking === "off"}
          onChange={(event) =>
            llm.setThinking(event.target.checked ? "off" : "on")
          }
        />
      </label>

      {llm.saveError ? (
        <p className="field-error" role="alert">
          {llm.saveError}
        </p>
      ) : (
        llm.invalid &&
        llm.dirty && <p className="field-help warn">{llm.invalid}</p>
      )}
    </section>
  );
}

function SectionHead({ configured }: { configured?: boolean }) {
  return (
    <div className="section-head">
      <div>
        <span className="eyebrow">Beyin</span>
        <h3 id="llm-title">Yapay zeka</h3>
      </div>
      {configured !== undefined && (
        <span className={`status-chip ${configured ? "ok" : "warn"}`}>
          <i aria-hidden="true" />
          {configured ? "Hazır" : "Kurulum gerekli"}
        </span>
      )}
    </div>
  );
}
