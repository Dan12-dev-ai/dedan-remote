import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CommandPalette } from "./CommandPalette";
import type { SearchResponse } from "../../types";

function searchFixture(): SearchResponse {
  return {
    query: "designer",
    total: 1,
    opportunities: [
      {
        id: "j1",
        slug: "product-designer-remote",
        title: "Product Designer (Remote)",
        company: "Northwind",
        source: "wwr",
        source_info: {
          id: "wwr",
          name: "We Work Remotely",
          monitored: true,
          homepage: null,
        },
        url: "https://example.com",
        apply_url: null,
        apply_host: null,
        salary: null,
        salary_disclosed: false,
        country: null,
        location_label: "Anywhere",
        remote: true,
        posted_date: null,
        description: null,
        tags: [],
        category: "general-remote",
        is_ai_related: false,
        experience_hint: null,
        discovered_at: null,
        freshness: {
          label: "2d ago",
          discovered_at: null,
          posted_date: null,
          age_days: 2,
          is_stale: false,
        },
        score: 72,
        score_explanation: null,
        preference_match: null,
      },
    ],
    categories: [],
    sources: [],
    suggestions: [],
    note: "Matched against title, company and tags.",
  };
}

function stubFetch(payload: unknown, status = 200) {
  const mock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    status,
    ok: status < 400,
    json: async () => payload,
  }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

function renderPalette(onClose = vi.fn()) {
  render(
    <MemoryRouter>
      <CommandPalette open onClose={onClose} />
    </MemoryRouter>,
  );
  return onClose;
}

describe("CommandPalette", () => {
  it("renders static navigation commands without calling the API", () => {
    const mock = stubFetch({});
    renderPalette();

    expect(
      screen.getByRole("dialog", { name: "Command palette" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Explore opportunities")).toBeInTheDocument();
    expect(mock).not.toHaveBeenCalled();
  });

  it("searches the live index after the debounce and lists opportunities", async () => {
    const mock = stubFetch(searchFixture());
    renderPalette();

    const input = screen.getByRole("combobox");
    await userEvent.type(input, "designer");

    expect(
      await screen.findByText("Product Designer (Remote)"),
    ).toBeInTheDocument();
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
    const url = String(mock.mock.calls[0][0]);
    expect(url).toContain("/api/search?q=designer");
    expect(url).toContain("limit=6");
    // Honest count straight from the response.
    expect(screen.getByText(/1 matching opportunity/)).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    stubFetch({});
    const onClose = renderPalette();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes before navigating when a command runs", async () => {
    stubFetch({
      query: "sources",
      total: 0,
      opportunities: [],
      categories: [],
      sources: [],
      suggestions: [],
      note: "No matches.",
    });
    const onClose = renderPalette();
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "sources");
    const item = await screen.findByRole("option", { name: /Sources/ });
    await userEvent.click(item);
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the backend message when search fails", async () => {
    stubFetch(
      { error: { code: "timeout", message: "The request timed out." } },
      408,
    );
    renderPalette();

    await userEvent.type(screen.getByRole("combobox"), "designer");
    expect(
      await screen.findByText("The request timed out."),
    ).toBeInTheDocument();
  });
});
