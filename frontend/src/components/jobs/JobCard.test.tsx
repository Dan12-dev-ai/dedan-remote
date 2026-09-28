import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { JobCard } from "./JobCard";
import type { JobSummary, PreferenceMatch, ScoreExplanation } from "../../types";

function makeJob(
  overrides: Partial<JobSummary> = {},
): JobSummary {
  return {
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
    url: "https://example.com/job",
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
    ...overrides,
  };
}

const explanation: ScoreExplanation = {
  basis: "system_ranking",
  label: "Weighted discovery ranking",
  total: 72,
  dimensions: {
    remote: { score: 100, weight: 0.3, contribution: 30 },
    salary: { score: 0, weight: 0.2, contribution: 0 },
    freshness: { score: 80, weight: 0.1, contribution: 8 },
  },
  reasons: [
    {
      dimension: "remote",
      label: "Remote-friendly",
      detail: "The listing is remote work.",
    },
  ],
};

const preferenceMatch: PreferenceMatch = {
  basis: "user_preferences",
  label: "Matches your preferences",
  score: 90,
  reasons: ["Aligned with your preferred categories"],
  note: "Based on your saved preferences.",
};

function renderCard(job: JobSummary, props: Record<string, unknown> = {}) {
  return render(
    <MemoryRouter>
      <JobCard job={job} authenticated={false} {...props} />
    </MemoryRouter>,
  );
}

describe("JobCard confidence breakdown", () => {
  it("states the system-ranking basis with real dimension readings", () => {
    renderCard(makeJob({ score_explanation: explanation }));

    expect(screen.getByText("Why this score?")).toBeInTheDocument();
    // Lead sentence for the system basis + total straight from the API.
    expect(screen.getByText(/Weighted from the dimensions below/)).toBeInTheDocument();
    expect(screen.getByText("72/100")).toBeInTheDocument();
    // Dimension label from the display map, not a guess.
    expect(screen.getByText("Remote eligibility")).toBeInTheDocument();
    // Band word derived from BAND_THRESHOLDS (72 → high).
    expect(screen.getAllByText("High").length).toBeGreaterThan(0);
    // The basis is named, never conflated with preferences.
    expect(screen.getByText("system ranking basis")).toBeInTheDocument();
  });

  it("labels preference match as its own basis, not a system score", () => {
    renderCard(
      makeJob({ score: 40, score_explanation: explanation, preference_match: preferenceMatch }),
    );

    expect(screen.getByText("Preference match")).toBeInTheDocument();
    expect(screen.getByText("90/100")).toBeInTheDocument();
    expect(screen.getByText("based on your preferences")).toBeInTheDocument();
    expect(
      screen.getByText(/not a\s+guarantee of outcomes/i),
    ).toBeInTheDocument();
  });

  it("shows no breakdown when the API returned no explanation", () => {
    renderCard(makeJob());
    expect(screen.queryByText("Why this score?")).not.toBeInTheDocument();
  });

  it("compact view omits the breakdown and keeps the score", () => {
    renderCard(makeJob({ score_explanation: explanation }), {
      view: "compact",
    });
    expect(screen.queryByText("Why this score?")).not.toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();
    expect(screen.getByTestId("job-card")).toHaveClass("job-card--compact");
  });

  it("expands the breakdown on click", async () => {
    const user = userEvent.setup();
    renderCard(makeJob({ score_explanation: explanation }));

    await user.click(screen.getByText("Why this score?"));
    expect(
      screen.getByText("The listing is remote work."),
    ).toBeInTheDocument();
  });
});
