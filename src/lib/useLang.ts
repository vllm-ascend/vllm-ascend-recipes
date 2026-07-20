import { useEffect, useState, useCallback } from 'react';
import { translations, type Lang, type TranslationKey } from '../lib/i18n';

export function useLang() {
  const [lang, setLang] = useState<Lang>('en');

  useEffect(() => {
    const saved = localStorage.getItem('lang') as Lang | null;
    if (saved) {
      setLang(saved);
    }

    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as Lang;
      setLang(detail);
    };
    window.addEventListener('langchange', handler);
    return () => window.removeEventListener('langchange', handler);
  }, []);

  const t = useCallback(
    (key: TranslationKey) => {
      return translations[lang][key] ?? translations.en[key] ?? key;
    },
    [lang],
  );

  return { lang, t };
}
