import { act, render, screen, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Toaster } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDeferredCommit } from "@/components/painel/useDeferredCommit";
import { descartarAtrativo } from "@/lib/atrativos-api";
import { setOperatorToken } from "@/lib/api-client";
import { server } from "@/mocks/server";

const DESCARTE_URL = "http://localhost:3000/api/api/v1/atrativos/:id/descarte";

let calls = 0;

function DiscardButton() {
  const commit = useDeferredCommit();
  return (
    <button
      type="button"
      onClick={() =>
        commit({ message: "1 descartado.", run: () => descartarAtrativo("abc") })
      }
    >
      Descartar
    </button>
  );
}

// fireEvent (not userEvent): userEvent's internal delays fight fake timers.
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  calls = 0;
  setOperatorToken("test-operator-token");
  server.use(
    http.patch(DESCARTE_URL, () => {
      calls += 1;
      return HttpResponse.json({ status: "ok", routing: "descarte" });
    }),
  );
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDeferredCommit", () => {
  it("sends nothing when undone within the window", async () => {
    render(
      <>
        <Toaster />
        <DiscardButton />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
    await advance(100);
    expect(screen.getByText("1 descartado.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Desfazer" }));
    await advance(10_000);

    expect(calls).toBe(0);
  });

  it("sends exactly one request when the window expires, even after the caller unmounts", async () => {
    const { rerender } = render(
      <>
        <Toaster />
        <DiscardButton />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
    await advance(3_000);
    expect(calls).toBe(0);

    // The operator switches screen: the caller unmounts, the commit must survive.
    rerender(<Toaster />);
    await advance(3_000);
    await vi.waitFor(() => expect(calls).toBe(1));

    await advance(30_000);
    expect(calls).toBe(1);
  });

  it("asks before leaving the page while a commit waits in its undo window", async () => {
    render(
      <>
        <Toaster />
        <DiscardButton />
      </>,
    );
    const leave = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };

    expect(leave()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
    expect(leave()).toBe(true);

    await advance(100);
    fireEvent.click(screen.getByRole("button", { name: "Desfazer" }));
    expect(leave()).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
    await advance(6_000);
    await vi.waitFor(() => expect(calls).toBe(1));
    expect(leave()).toBe(false);
  });
});
