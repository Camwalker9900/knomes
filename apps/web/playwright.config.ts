import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright smoke tests (spec §69, plan D3.3).
 *
 * Hermetic: the webServer runs `next start` against a prior `npm run build`;
 * no API is started, and the e2e specs only cover statically rendered UI.
 */
export default defineConfig({
  testDir: "e2e",
  forbidOnly: !!process.env.CI,
  use: {
    baseURL: "http://localhost:3000",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run start",
    port: 3000,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
