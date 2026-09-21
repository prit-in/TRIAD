import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { ToastHost } from "./shared";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html is missing #root");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ToastHost>
          <App />
        </ToastHost>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
