import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RatingsRow } from "../RatingsRow";

describe("RatingsRow", () => {
  it("renders accessible IMDb, Rotten Tomatoes and Metacritic badges", () => {
    render(
      <RatingsRow
        ratings={{ imdb_rating: 8.4, rt_rating: 92, metacritic_rating: 89 }}
      />,
    );

    expect(screen.getByLabelText("IMDb rating 8.4 out of 10")).toBeInTheDocument();
    expect(screen.getByLabelText("Rotten Tomatoes 92 percent")).toHaveTextContent("92%");
    expect(screen.getByLabelText("Metacritic 89 out of 100")).toHaveTextContent("89");
    expect(screen.getByText(/Ratings via IMDb, Rotten Tomatoes and Metacritic/)).toBeInTheDocument();
  });

  it("omits missing ratings instead of showing placeholders", () => {
    render(<RatingsRow ratings={{ imdb_rating: 8.4, rt_rating: null, metacritic_rating: null }} />);

    expect(screen.getByLabelText("IMDb rating 8.4 out of 10")).toBeInTheDocument();
    expect(screen.queryByText(/Rotten Tomatoes \d/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Metacritic \d/)).not.toBeInTheDocument();
    expect(screen.queryByText("N/A")).not.toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("renders nothing at all without ratings", () => {
    const { container } = render(
      <RatingsRow ratings={{ imdb_rating: null, rt_rating: null, metacritic_rating: null }} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/Ratings via/)).not.toBeInTheDocument();
  });

  it("uses brand tokens for IMDb, Rotten Tomatoes and Metacritic", () => {
    render(
      <RatingsRow
        ratings={{ imdb_rating: 8.4, rt_rating: 92, metacritic_rating: 89 }}
      />,
    );

    const imdb = screen.getByLabelText("IMDb rating 8.4 out of 10");
    expect(imdb).toHaveClass("bg-rating-imdb/14", "text-rating-imdb");

    const rt = screen.getByLabelText("Rotten Tomatoes 92 percent");
    expect(rt).toHaveClass("border-rating-rt/40", "bg-rating-rt/14", "text-fg");

    const metacritic = screen.getByLabelText("Metacritic 89 out of 100");
    expect(metacritic).toHaveClass("bg-rating-metacritic/14", "text-rating-metacritic");
  });

  it("shows the attribution once even when only Metacritic is present", () => {
    render(<RatingsRow ratings={{ metacritic_rating: 89 }} />);

    expect(screen.getAllByText(/Ratings via IMDb, Rotten Tomatoes and Metacritic/)).toHaveLength(1);
  });
});
