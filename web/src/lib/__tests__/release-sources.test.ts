import { describe, expect, it } from "vitest";

import {
  describeReleaseSources,
  releaseSourceChoices,
  releaseSourceLabel,
  releaseSourceNumber,
} from "../releaseSources";

describe("release source labels", () => {
  it("numbers the known sources in registry order", () => {
    expect(releaseSourceNumber("torrentio")).toBe(1);
    expect(releaseSourceNumber("animetosho")).toBe(2);
    expect(releaseSourceLabel("animetosho")).toBe("AnimeTosho");
  });

  it("describes one or more selected sources without hardcoding Torrentio", () => {
    expect(describeReleaseSources(["animetosho"])).toBe(
      "Searching source 2 AnimeTosho…",
    );
    expect(describeReleaseSources(["torrentio", "animetosho"])).toBe(
      "Searching sources 1 Torrentio, 2 AnimeTosho…",
    );
  });

  it("keeps room for a future source after the registered ones", () => {
    const choices = releaseSourceChoices(["torrentio", "animetosho", "example"]);

    expect(choices.map((choice) => [choice.number, choice.label])).toEqual([
      [1, "Torrentio"],
      [2, "AnimeTosho"],
      [3, "Torrents-CSV"],
      [4, "Example"],
    ]);
  });
});
