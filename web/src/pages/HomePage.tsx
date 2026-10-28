import { Link } from "react-router-dom";

import { HealthCard } from "../components/HealthCard";

export function HomePage() {
  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-bold text-fg">Daku</h1>
        <p className="text-small text-fg-muted">
          Your LAN-only media librarian. Jellyfin plays; Daku acquires.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <Link
          to="/library"
          className="rounded-panel border border-border-subtle bg-panel p-5 transition-colors hover:border-border-strong"
        >
          <h2 className="text-title font-semibold text-fg">Library</h2>
          <p className="mt-1 text-small text-fg-muted">
            Browse saved titles. An entry with no file is Saved, not an error.
          </p>
        </Link>
        <Link
          to="/search"
          className="rounded-panel border border-border-subtle bg-panel p-5 transition-colors hover:border-border-strong"
        >
          <h2 className="text-title font-semibold text-fg">Search</h2>
          <p className="mt-1 text-small text-fg-muted">
            Find a movie, series or anime and add it without downloading anything.
          </p>
        </Link>
      </div>

      <HealthCard />
    </section>
  );
}
