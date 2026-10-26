import { useState } from "react";

import type { Kind } from "../lib/api";
import { kindEdgeClass } from "./Pills";

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
  kind,
}: {
  url: string | null;
  title: string;
  kind: Kind;
}) {
  const [failed, setFailed] = useState(false);

  return (
    <div className="relative aspect-[2/3] overflow-hidden rounded-panel border border-border-subtle bg-panel-alt">
      <div className={`absolute inset-x-0 top-0 z-10 h-0.5 ${kindEdgeClass(kind)}`} aria-hidden />
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
          <span className="text-[28px] font-semibold text-fg-comment" aria-hidden>
            {initials(title)}
          </span>
        </div>
      )}
    </div>
  );
}
