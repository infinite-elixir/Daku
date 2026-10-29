import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

type NavItem = {
  to: string;
  label: string;
  end?: boolean;
  icon: ReactNode;
};

function LibraryIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <rect x="3" y="4" width="5" height="16" rx="1" />
      <rect x="10" y="4" width="5" height="16" rx="1" />
      <path d="m17.5 5.5 3 14.5" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </svg>
  );
}

function SystemIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l2.5 2" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" />
      <path d="m19 13.5 1.2 1-.9 1.6-1.5-.4a7.5 7.5 0 0 1-1.4 1.1l-.2 1.6h-1.9l-.5-1.5a7.5 7.5 0 0 1-1.8 0l-.5 1.5h-1.9l-.2-1.6a7.5 7.5 0 0 1-1.4-1.1l-1.5.4-.9-1.6 1.2-1a7 7 0 0 1 0-2.1l-1.2-1 .9-1.6 1.5.4A7.5 7.5 0 0 1 9.1 8l.2-1.6h1.9l.5 1.5a7.5 7.5 0 0 1 1.8 0l.5-1.5h1.9l.2 1.6a7.5 7.5 0 0 1 1.4 1.1l1.5-.4.9 1.6-1.2 1a7 7 0 0 1 .3 1.1 7 7 0 0 1-.1 1.1Z" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <path d="M12 4v10" />
      <path d="m7 10 5 5 5-5" />
      <path d="M4 20h16" />
    </svg>
  );
}

const NAV_ITEMS: NavItem[] = [
  { to: "/library", label: "Library", icon: <LibraryIcon /> },
  { to: "/search", label: "Search", icon: <SearchIcon /> },
  { to: "/downloads", label: "Downloads", icon: <DownloadIcon /> },
  { to: "/", label: "System", end: true, icon: <SystemIcon /> },
  { to: "/settings", label: "Settings", icon: <SettingsIcon /> },
];

function navClass({ isActive }: { isActive: boolean }, mobile: boolean): string {
  if (mobile) {
    return [
      "flex min-h-12 flex-col items-center justify-center gap-1 text-micro font-medium",
      isActive ? "text-accent" : "text-fg-muted hover:text-fg",
    ].join(" ");
  }
  return [
    "rounded-control px-3 py-1.5 text-body transition-colors",
    isActive ? "bg-hover text-fg" : "text-fg-muted hover:bg-hover hover:text-fg",
  ].join(" ");
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-canvas text-fg">
      <header className="sticky top-0 z-20 border-b border-border-subtle bg-canvas/95">
        <div className="mx-auto flex h-14 w-full max-w-[1440px] items-center justify-between px-gutter">
          <Link
            to="/library"
            className="text-h2 font-bold tracking-tight text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span className="mr-1 text-accent" aria-hidden>
              ▸
            </span>
            Daku
          </Link>
          <nav aria-label="Primary" className="hidden items-center gap-1 md:flex">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={(state) => navClass(state, false)}>
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1440px] px-gutter pb-24 pt-section md:pb-10">{children}</main>

      <nav
        aria-label="Primary mobile"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-border-subtle bg-panel md:hidden"
      >
        <ul className="grid grid-cols-5">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} end={item.end} className={(state) => navClass(state, true)}>
                {item.icon}
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
