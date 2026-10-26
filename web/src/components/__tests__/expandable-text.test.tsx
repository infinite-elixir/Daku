import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExpandableText, EXPANDABLE_THRESHOLD } from "../ExpandableText";

const LONG_TEXT = `${"A long description of the desert planet and its people. ".repeat(12)}`.trim();
const SHORT_TEXT = "A short overview.";

describe("ExpandableText", () => {
  it("clamps long text and toggles with a keyboard-operable button", () => {
    render(<ExpandableText text={LONG_TEXT} />);

    const paragraph = screen.getByText(LONG_TEXT);
    expect(LONG_TEXT.length).toBeGreaterThan(EXPANDABLE_THRESHOLD);
    expect(paragraph).toHaveClass("line-clamp-3");

    const button = screen.getByRole("button", { name: "Show more" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", paragraph.id);
    expect(paragraph.id).toBeTruthy();

    fireEvent.click(button);

    expect(paragraph).not.toHaveClass("line-clamp-3");
    expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "Show less" }));

    expect(paragraph).toHaveClass("line-clamp-3");
    expect(screen.getByRole("button", { name: "Show more" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("does not render a toggle for short text", () => {
    render(<ExpandableText text={SHORT_TEXT} />);

    expect(screen.getByText(SHORT_TEXT)).not.toHaveClass("line-clamp-3");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders nothing for a missing overview", () => {
    const { container } = render(<ExpandableText text={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});
