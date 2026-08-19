import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Timeline from "@/components/Timeline";
import type { TimelineResponse } from "@/lib/types";
import timelineJson from "@/test/fixtures/timeline.json";

const fixture = timelineJson as TimelineResponse;

describe("Timeline", () => {
  it("renders every event of the synthetic 100 Test Street arc", () => {
    render(<Timeline events={fixture.events} />);

    for (const event of fixture.events) {
      expect(screen.getByText(event.title)).toBeInTheDocument();
    }
    // Year-grouped: the fixture arc all happens in 2025.
    expect(screen.getByRole("heading", { level: 3, name: "2025" })).toBeInTheDocument();
  });

  it("shows only permit events when the Permits filter tab is selected", () => {
    render(<Timeline events={fixture.events} />);

    const permitsTab = screen.getByRole("button", { name: "Permits" });
    fireEvent.click(permitsTab);
    expect(permitsTab).toHaveAttribute("aria-pressed", "true");

    expect(screen.getByText("Mechanical permit issued")).toBeInTheDocument();
    expect(screen.getByText("Mechanical permit finalized")).toBeInTheDocument();

    expect(screen.queryByText("Listed for sale")).not.toBeInTheDocument();
    expect(screen.queryByText("General home inspection performed")).not.toBeInTheDocument();
    expect(screen.queryByText("Contract terminated")).not.toBeInTheDocument();
    expect(screen.queryByText("HVAC replacement reported")).not.toBeInTheDocument();
    expect(screen.queryByText("HVAC finding resolved")).not.toBeInTheDocument();
  });

  it("returns to the full arc when All is selected again", () => {
    render(<Timeline events={fixture.events} />);

    fireEvent.click(screen.getByRole("button", { name: "Permits" }));
    fireEvent.click(screen.getByRole("button", { name: "All" }));

    expect(screen.getByText("Listed for sale")).toBeInTheDocument();
    expect(screen.getByText("Mechanical permit issued")).toBeInTheDocument();
  });
});
