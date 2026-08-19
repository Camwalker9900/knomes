import { expect, test } from "@playwright/test";

// Homepage smoke tests. The homepage is static — these must pass with no API running.

test("homepage shows the headline", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Know what happened to a house before you buy it.",
    }),
  ).toBeVisible();
});

test("search input is visible and focusable", async ({ page }) => {
  await page.goto("/");
  const input = page.getByRole("combobox", {
    name: "Search by street address or HCAD account number",
  });
  await expect(input).toBeVisible();
  await expect(input).toHaveAttribute(
    "placeholder",
    "Search a Houston address or HCAD account number",
  );
  await input.focus();
  await expect(input).toBeFocused();
});
