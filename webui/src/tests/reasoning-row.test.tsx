import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { ReasoningRow } from "@/components/thread/activity/ReasoningRow";

it("keeps the compact preview bounded without exposing hidden reasoning in a tooltip", async () => {
  const user = userEvent.setup();
  const text = 'The user is saying "hi" again. I should respond in Chinese, concisely and in a friendly manner.';
  const { container } = render(<ReasoningRow text={text} streaming={false} />);
  const line = screen.getByTestId("activity-line");

  expect(container.querySelector("[title]")).toBeNull();
  await user.hover(line);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  await user.unhover(line);
  await user.tab();
  expect(line).toHaveFocus();
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});

it("bounds the preview and renders the current text as a multiline body when expanded", () => {
  const text = "A long reasoning paragraph. ".repeat(2_000) + "\noriginal tail";
  const { container, rerender } = render(<ReasoningRow text={text} streaming />);
  const line = screen.getByTestId("activity-line");

  expect(line.textContent).toBe(text.slice(0, 512) + "…");
  expect(container.querySelector("[data-sheen-text]")).toBeNull();
  expect(screen.getByTestId("activity-reasoning-marker")).toHaveAttribute("data-state", "thinking");
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

  rerender(<ReasoningRow text={text} streaming expanded />);
  const body = screen.getByTestId("reasoning-body");
  expect(body.textContent).toBe(text);
  expect(body).toHaveClass("whitespace-pre-wrap", "break-words");
  expect(screen.queryByTestId("activity-line")).not.toBeInTheDocument();
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  rerender(<ReasoningRow text={text + " latest tail"} streaming expanded />);
  expect(body.textContent).toBe(text + " latest tail");
});

it("keeps the existing text animation for short active reasoning", () => {
  const { container } = render(<ReasoningRow text="Short active reasoning" streaming />);
  expect(container.querySelector("[data-sheen-text]")).toHaveAttribute("data-sheen-text", "Short active reasoning");
});

it("does not split a surrogate pair at the reasoning preview boundary", () => {
  render(<ReasoningRow text={"x".repeat(511) + "🚀 tail"} streaming />);
  expect(screen.getByTestId("activity-line").textContent).toBe("x".repeat(511) + "…");
});
