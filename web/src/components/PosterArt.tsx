import { useState } from "react";

function initials(title: string): string {
  const words = title.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) {
    return "?";
  }
  return words
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
}

export function PosterArt({ url, title }: { url: string | null; title: string }) {
  const [failed, setFailed] = useState(false);

  return (
    <div className="relative aspect-[2/3] overflow-hidden rounded-panel border border-border-subtle bg-panel-alt">
      {url && !failed ? (
        <img
          src={url}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center">
          <span className="text-h1 font-semibold text-fg-comment" aria-hidden>
            {initials(title)}
          </span>
        </div>
      )}
    </div>
  );
}
