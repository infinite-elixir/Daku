import { useId, useState } from "react";

/** Below this length the overview is never clamped or toggled. */
export const EXPANDABLE_THRESHOLD = 320;

export interface ExpandableTextProps {
  text: string | null | undefined;
  className?: string;
  threshold?: number;
}

export function ExpandableText({
  text,
  className,
  threshold = EXPANDABLE_THRESHOLD,
}: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();

  if (!text) {
    return null;
  }

  const expandable = text.length > threshold;

  return (
    <div className={["flex max-w-[68ch] flex-col gap-grid", className ?? ""].join(" ").trim()}>
      <p
        id={id}
        className={[
          "text-body text-fg-muted",
          expandable && !expanded ? "line-clamp-3" : "",
        ]
          .join(" ")
          .trim()}
      >
        {text}
      </p>
      {expandable ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={() => setExpanded((value) => !value)}
          className="min-h-control-sm w-fit text-small font-semibold text-accent hover:text-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </div>
  );
}
