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

export function PosterArt({
  url,
  title,
  bleed = false,
}: {
  url: string | null;
  title: string;
  /** Fill the card's outer frame; the fixed aspect keeps every poster the same height. */
  bleed?: boolean;
}) {
  const [failed, setFailed] = useState(false);

  return (
    <div
      className={[
        "pointer-events-none relative aspect-[2/3] overflow-hidden bg-panel-alt",
        bleed ? "rounded-t-panel" : "rounded-panel border border-border-subtle",
      ].join(" ")}
    >
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
