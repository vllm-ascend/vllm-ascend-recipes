import { useEffect, useState } from 'react';

export default function LanguageToggle() {
  const [lang, setLang] = useState<'en' | 'zh'>('en');

  useEffect(() => {
    const saved = localStorage.getItem('lang') as 'en' | 'zh' | null;
    if (saved) {
      setLang(saved);
      document.documentElement.lang = saved;
    } else {
      document.documentElement.lang = 'en';
    }
  }, []);

  const switchTo = (next: 'en' | 'zh') => {
    if (next === lang) return;
    setLang(next);
    localStorage.setItem('lang', next);
    document.documentElement.lang = next;
    window.dispatchEvent(new CustomEvent('langchange', { detail: next }));
  };

  const activeClass = 'px-2 py-1 text-[11px] font-mono rounded bg-accent-500/20 text-accent-400';
  const inactiveClass = 'px-2 py-1 text-[11px] font-mono rounded text-ink-500 hover:text-ink-300 transition-colors';

  return (
    <div className="flex items-center rounded-md border border-ink-800/60 bg-ink-900/30 p-0.5">
      <button onClick={() => switchTo('en')} className={lang === 'en' ? activeClass : inactiveClass}>EN</button>
      <button onClick={() => switchTo('zh')} className={lang === 'zh' ? activeClass : inactiveClass}>中文</button>
    </div>
  );
}
