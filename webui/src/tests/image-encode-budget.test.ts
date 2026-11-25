import { afterEach, describe, expect, it, vi } from "vitest";

import { encodeImageInWorker } from "@/workers/imageEncode.worker";

function pngFile(size: number): File {
  const bytes = new Uint8Array(size);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  return new File([bytes], "image.png", { type: "image/png" });
}

function stubNormalization(outputBytes: number) {
  vi.stubGlobal("createImageBitmap", vi.fn(async () => ({
    width: 4000,
    height: 3000,
    close: vi.fn(),
  })));
  vi.stubGlobal("OffscreenCanvas", class {
    getContext() {
      return { imageSmoothingQuality: "low", drawImage: vi.fn() };
    }

    async convertToBlob() {
      return new Blob([new Uint8Array(outputBytes)], { type: "image/png" });
    }
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("image encoding budget", () => {
  it("passes original bytes through when they fit the requested budget", async () => {
    const file = pngFile(8);

    await expect(encodeImageInWorker({ id: "original", file, maxBytes: 8 }))
      .resolves.toMatchObject({ id: "original", ok: true, bytes: 8, normalized: false });
  });

  it("normalizes an oversized original into the requested budget", async () => {
    stubNormalization(4);
    const file = pngFile(20);

    const result = await encodeImageInWorker({ id: "normalized", file, maxBytes: 10 });

    expect(result).toMatchObject({ id: "normalized", ok: true, bytes: 4, normalized: true });
  });

  it("rejects a normalized result that still exceeds the requested budget", async () => {
    stubNormalization(11);
    const file = pngFile(20);

    await expect(encodeImageInWorker({ id: "too-large", file, maxBytes: 10 }))
      .resolves.toEqual({ id: "too-large", ok: false, reason: "too_large_after_normalize" });
  });

  it("forwards the requested budget through the worker wrapper", async () => {
    const listeners = new Map<string, (event: { data: unknown }) => void>();
    let worker: {
      posted: { id: string; maxBytes: number } | null;
      addEventListener: (type: string, listener: (event: { data: unknown }) => void) => void;
      postMessage: (message: { id: string; maxBytes: number }) => void;
      terminate: () => void;
    };
    vi.stubGlobal("Worker", class {
      posted: { id: string; maxBytes: number } | null = null;

      constructor() {
        worker = this;
      }

      addEventListener(type: string, listener: (event: { data: unknown }) => void) {
        listeners.set(type, listener);
      }

      postMessage(message: { id: string; maxBytes: number }) {
        this.posted = message;
      }

      terminate() {}
    });
    const { encodeImage } = await import("@/lib/imageEncode");
    const file = pngFile(8);

    const pending = encodeImage(file, 12_345);

    expect(worker!.posted).toMatchObject({ id: expect.any(String), maxBytes: 12_345 });
    listeners.get("message")!({
      data: { id: worker!.posted!.id, ok: false, reason: "decode_failed" },
    });
    await expect(pending).resolves.toMatchObject({ ok: false, reason: "decode_failed" });
  });
});
