import { afterEach, describe, expect, it, vi } from "vitest";

import {
  consumeUrlBootstrapSecret,
  deriveWsUrl,
  fetchBootstrap,
} from "@/lib/bootstrap";

const LIMITS = {
  transport: { max_frame_bytes: 1_048_576, envelope_reserve_bytes: 65_536 },
  message: { max_text_bytes: 65_536 },
  attachments: {
    max_count: 4,
    max_file_bytes: 6_291_456,
    max_total_bytes: 25_165_824,
    max_video_count: 1,
    max_video_bytes: 20_971_520,
  },
};

describe("bootstrap helpers", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("prefers the server-provided websocket URL over the current dev host", () => {
    expect(deriveWsUrl("/", "tok en", "ws://127.0.0.1:8765/")).toBe(
      "ws://127.0.0.1:8765/?token=tok%20en",
    );
  });

  it("overrides the server-provided websocket URL when on dev server port 5173", () => {
    vi.stubGlobal("window", {
      location: {
        port: "5173",
        hostname: "192.168.1.100",
        protocol: "http:",
      },
    });
    expect(deriveWsUrl("/", "tok", "ws://127.0.0.1:8765/")).toBe(
      "ws://192.168.1.100:8765/?token=tok",
    );
  });

  it("keeps the gateway websocket port when Vite proxies a custom target", () => {
    vi.stubGlobal("window", {
      location: {
        port: "5173",
        hostname: "127.0.0.1",
        protocol: "http:",
      },
    });
    expect(deriveWsUrl("/ws", "tok", "ws://127.0.0.1:8899/ws")).toBe(
      "ws://127.0.0.1:8899/ws?token=tok",
    );
  });

  it("preserves the host socket bridge URL", () => {
    expect(deriveWsUrl("/", "tok en", "nanobot-host://engine/")).toBe(
      "nanobot-host://engine/?token=tok%20en",
    );
  });

  it("falls back to the current window host for legacy bootstrap payloads", () => {
    expect(deriveWsUrl("/", "tok")).toBe(
      "ws://localhost:3000/?token=tok",
    );
  });

  it("does not append a token for trusted-proxy websocket URLs", () => {
    expect(deriveWsUrl("/", undefined, "wss://proxy.example/")).toBe(
      "wss://proxy.example/",
    );
  });

  it("times out when the bootstrap endpoint never responds", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    const pending = expect(fetchBootstrap("", "", 25)).rejects.toThrow(
      "Request timed out after 25ms",
    );
    await vi.advanceTimersByTimeAsync(25);

    await pending;
  });

  it("accepts tokenless trusted-proxy bootstrap responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          ws_path: "/",
          ws_url: "wss://proxy.example/",
          limits: LIMITS,
        }),
      })),
    );

    await expect(fetchBootstrap()).resolves.toMatchObject({
      ws_path: "/",
      ws_url: "wss://proxy.example/",
    });
  });

  it.each([
    ["missing", undefined],
    ["invalid", { ...LIMITS, attachments: { ...LIMITS.attachments, max_video_count: 0 } }],
  ])("rejects a bootstrap response with %s attachment limits", async (_label, limits) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ ws_path: "/", limits }),
      })),
    );

    await expect(fetchBootstrap()).rejects.toThrow(
      "bootstrap response has invalid attachment limits",
    );
  });

  it("consumes bootstrap secrets from the URL fragment", () => {
    window.history.replaceState(
      null,
      "",
      "/#/settings?bootstrapSecret=s3cret&section=models",
    );

    expect(consumeUrlBootstrapSecret()).toBe("s3cret");
    expect(window.location.hash).toBe("#/settings?section=models");
  });
});
