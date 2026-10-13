import { createRoot } from "react-dom/client";
import App from "./App";
import { runtimeKind } from "./runtime";
import "./styles.css";

// "desktop" when served by the backend at /app/ (Polaris window), else "extension".
document.documentElement.dataset.runtime = runtimeKind();

createRoot(document.getElementById("root")!).render(<App />);
