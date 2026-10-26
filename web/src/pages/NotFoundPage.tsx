import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="flex flex-col items-center gap-3 py-16 text-center">
      <p className="text-[40px] font-bold leading-none tracking-tight text-border-strong" aria-hidden>
        404
      </p>
      <h1 className="text-[20px] font-semibold text-fg">Page not found</h1>
      <p className="max-w-[48ch] text-[13px] text-fg-muted">
        That route does not exist. The library is a better place to be.
      </p>
      <Link
        to="/library"
        className="mt-2 inline-flex min-h-9 items-center rounded-control border border-border-strong px-3 text-[13px] font-semibold text-fg hover:bg-hover"
      >
        Go to library
      </Link>
    </section>
  );
}
