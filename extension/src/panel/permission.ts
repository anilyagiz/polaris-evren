import "./styles.css";

const button = document.getElementById("grant") as HTMLButtonElement;
const status = document.getElementById("status")!;

function explain(error: unknown): string {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotFoundError")
    return "Mikrofon bulunamadı. Bir mikrofon bağlı olduğundan emin ol ve tekrar dene.";
  if (name === "NotReadableError")
    return "Mikrofon başka bir uygulama tarafından kullanılıyor ya da Windows engelliyor. Ayarlar > Gizlilik > Mikrofon bölümünden masaüstü uygulamalarına izin ver.";
  if (name === "NotAllowedError")
    return "İzin reddedildi. Adres çubuğundaki kilit/mikrofon simgesinden bu eklentiye izin ver; Brave Shields veya brave://settings/content/microphone engelliyor olabilir. Sonra tekrar dene.";
  return "İzin alınamadı. Tarayıcının site izinlerini kontrol edip tekrar dene.";
}

async function store(value: "granted" | "denied") {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.local)
      await chrome.storage.local.set({ micPermission: value });
  } catch {
    /* storage is best-effort */
  }
}

button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
    await store("granted");
    status.textContent =
      "Mikrofon izni verildi. Bu sekme kapanıyor; yan panele dönüp bas-konuş'u tekrar dene.";
    if (typeof chrome !== "undefined" && chrome.tabs) {
      const tab = await chrome.tabs.getCurrent();
      if (tab?.id !== undefined) await chrome.tabs.remove(tab.id);
    }
  } catch (error) {
    await store("denied");
    status.textContent = explain(error);
    button.disabled = false;
  }
});
