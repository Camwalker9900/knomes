import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
}));

describe("HomePage", () => {
  it("renders the hero headline", () => {
    render(<HomePage />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Know what happened to a house before you buy it.",
      }),
    ).toBeInTheDocument();
  });

  it("lists the six property-history sections", () => {
    render(<HomePage />);
    for (const title of [
      "Permits",
      "Inspections",
      "Repairs",
      "Code violations",
      "Previous transactions",
      "Verified property findings",
    ]) {
      expect(screen.getByRole("heading", { level: 3, name: title })).toBeInTheDocument();
    }
  });
});
