import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  filtersFromParams,
  parseView,
  paramsFromFilters,
} from "./query";

describe("view mode URL round-trip", () => {
  it("defaults to list and omits the param", () => {
    expect(parseView(null)).toBe("list");
    expect(parseView("bogus")).toBe("list");
    const params = paramsFromFilters(DEFAULT_FILTERS);
    expect(params.get("view")).toBeNull();
  });

  it("serializes compact and visual modes", () => {
    expect(paramsFromFilters(DEFAULT_FILTERS, "compact").get("view"))
      .toBe("compact");
    expect(paramsFromFilters(DEFAULT_FILTERS, "visual").get("view")).toBe(
      "visual",
    );
  });

  it("parses every supported mode and round-trips", () => {
    for (const mode of ["list", "compact", "visual"] as const) {
      const params = paramsFromFilters(DEFAULT_FILTERS, mode);
      expect(parseView(params.get("view"))).toBe(mode);
    }
  });

  it("filters survive alongside the view param", () => {
    const params = paramsFromFilters(
      { ...DEFAULT_FILTERS, q: "designer", remote: true },
      "compact",
    );
    expect(params.get("view")).toBe("compact");
    const back = filtersFromParams(params);
    expect(back.q).toBe("designer");
    expect(back.remote).toBe(true);
  });
});
