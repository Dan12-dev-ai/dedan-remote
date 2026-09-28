/**
 * Vitest global setup for the DEDAN Remote frontend.
 *
 * Loaded via `test.setupFiles` in vite.config.ts. Keeps jsdom-based tests
 * close to a real browser: fetch is stubbed per-test, storage is cleared
 * between tests so auth state never leaks.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    /* storage unavailable in some environments */
  }
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
