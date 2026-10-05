"use client";

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

const isDarkNow = () => document.documentElement.classList.contains("dark");

export function Header() {
  const dark = useSyncExternalStore(subscribe, isDarkNow, () => false);

  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      // Storage unavailable (private mode); the toggle still works for this page.
    }
  };

  return (
    <header className="no-print border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/80">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-teal-600 text-sm font-bold text-white">
            M
          </span>
          <div>
            <h1 className="text-lg font-semibold leading-tight">MedOrchestra</h1>
            <p className="text-xs text-zinc-500">Multi-agent clinical decision support</p>
          </div>
        </div>
        <button
          onClick={toggle}
          className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          aria-label="Toggle dark mode"
        >
          {dark ? "☀ Light" : "☾ Dark"}
        </button>
      </div>
    </header>
  );
}
