import "./styles.css";

const button = document.getElementById("grant") as HTMLButtonElement;
const status = document.getElementById("status")!;
button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
    status.textContent =
      "Mikrofon izni verildi. Yan panele dönüp bas-konuşu kullanabilirsin.";
    if (typeof chrome !== "undefined" && chrome.tabs) {
      const tab = await chrome.tabs.getCurrent();
      if (tab?.id !== undefined) await chrome.tabs.remove(tab.id);
    }
  } catch {
    status.textContent =
      "İzin alınamadı. Tarayıcının site izinlerini kontrol edip tekrar dene.";
    button.disabled = false;
  }
});
