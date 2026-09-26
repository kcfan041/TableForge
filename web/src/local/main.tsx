import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { I18nProvider } from "../i18n";
import "../styles.css";
import { LocalApp } from "./LocalApp";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <I18nProvider>
      <BrowserRouter>
        <LocalApp />
      </BrowserRouter>
    </I18nProvider>
  </StrictMode>,
);
