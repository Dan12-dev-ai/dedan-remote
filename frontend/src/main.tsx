import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthProvider } from "./stores/AuthContext";
// Design tokens + base primitives — must load before any component CSS so
// `var(--*)` custom properties resolve everywhere. `index.css` declares the
// cascade order for the whole system; nothing else imports tokens directly.
import "./design/index.css";

const container = document.getElementById("root");
if (!container) throw new Error("Root element not found");

createRoot(container).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>
);
