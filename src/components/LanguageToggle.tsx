import { useEffect, useRef, useState } from 'react';

export default function LanguageToggle() {
  const [lang, setLang] = useState<'en' | 'zh'>('en');

  // Hydrate from localStorage on first client render. We do this during render
  // (guarded by a ref) rather than in an effect so React only commits once.
  const hydratedRef = useRef<boolean | null>(null);
  if (hydratedRef.current == null) {
    hydratedRef.current = true;
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('lang') as 'en' | 'zh' | null;
      if (saved) {
        setLang(saved);
      }
    }
  }

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const switchTo = (next: 'en' | 'zh') => {
    if (next === lang) return;
    setLang(next);
    localStorage.setItem('lang', next);
    window.dispatchEvent(new CustomEvent('langchange', { detail: next }));
  };

  const activeClass = 'px-2 py-1 text-[11px] font-mono rounded bg-accent-500/20 text-accent-400';
  const inactiveClass =
    'px-2 py-1 text-[11px] font-mono rounded text-ink-500 hover:text-ink-300 transition-colors';

  return (
    <div className="flex items-center rounded-md border border-ink-800/60 bg-ink-900/30 p-0.5">
      <button
        onClick={() => switchTo('en')}
        className={lang === 'en' ? activeClass : inactiveClass}
      >
        EN
      </button>
      <button
        onClick={() => switchTo('zh')}
        className={lang === 'zh' ? activeClass : inactiveClass}
      >
        中文
      </button>
    </div>
  );
}
