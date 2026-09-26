import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { en } from "./en";
import { zhHant, type Messages } from "./zh-Hant";

export type Locale = "zh-Hant" | "en";

const catalogs: Record<Locale, Partial<Messages>> = { "zh-Hant": zhHant, en };
const STORAGE_KEY = "tableforge.locale";

export type MessageKey = keyof Messages;
type Translate = (key: MessageKey, values?: Record<string, string | number>) => string;

interface I18n {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
  formatDate: (iso: string) => string;
  formatNumber: (value: number, digits?: number) => string;
}

const I18nContext = createContext<I18n | null>(null);

function initialLocale(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "zh-Hant" || saved === "en") return saved;
  return "zh-Hant";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.lang = next;
    setLocaleState(next);
  }, []);

  const value = useMemo<I18n>(() => {
    const catalog = catalogs[locale];
    const t: Translate = (key, values) => {
      const template = catalog[key] ?? zhHant[key];
      if (!values) return template;
      return template.replace(/\{(\w+)\}/g, (match, name: string) =>
        name in values ? String(values[name]) : match,
      );
    };
    const dates = new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "medium" });
    return {
      locale,
      setLocale,
      t,
      formatDate: (iso) => dates.format(new Date(iso)),
      formatNumber: (value, digits = 0) =>
        new Intl.NumberFormat(locale, {
          maximumFractionDigits: digits,
          minimumFractionDigits: digits,
        }).format(value),
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside I18nProvider");
  return context;
}
