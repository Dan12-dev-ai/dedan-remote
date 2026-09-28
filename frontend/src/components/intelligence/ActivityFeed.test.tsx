import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ActivityFeed } from "./ActivityFeed";
import type { NotificationsResponse } from "../../types";

function stubFetch(payload: unknown, status = 200) {
  const mock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    status,
    ok: status < 400,
    json: async () => payload,
  }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("ActivityFeed", () => {
  it("fetches notifications when opened and renders real timestamps", async () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    const payload: NotificationsResponse = {
      items: [
        {
          id: "n1",
          kind: "saved",
          title: "Saved “Product Designer (Remote)”",
          detail: "Northwind · We Work Remotely",
          at: twoHoursAgo,
          job: null,
        },
      ],
      note: "Activity from the last 30 days.",
    };
    const mock = stubFetch(payload);

    render(
      <MemoryRouter>
        <ActivityFeed />
      </MemoryRouter>,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Activity feed" }),
    );

    expect(
      await screen.findByText("Saved “Product Designer (Remote)”"),
    ).toBeInTheDocument();
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
    expect(String(mock.mock.calls[0][0])).toContain("/api/notifications");
    // Relative time derived from the real `at` instant.
    expect(screen.getByText("2h ago")).toBeInTheDocument();
    expect(screen.getByText(/Activity from the last 30 days/)).toBeInTheDocument();
  });

  it("shows the API error with a retry action, never a fake feed", async () => {
    stubFetch(
      { error: { code: "unauthorized", message: "Sign in to see activity." } },
      401,
    );

    render(
      <MemoryRouter>
        <ActivityFeed />
      </MemoryRouter>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Activity feed" }),
    );

    expect(
      await screen.findByText("Sign in to see activity."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("distinguishes an empty feed from a failed one", async () => {
    stubFetch({ items: [], note: "No recent activity." });

    render(
      <MemoryRouter>
        <ActivityFeed />
      </MemoryRouter>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Activity feed" }),
    );

    expect(await screen.findByText("No activity yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
