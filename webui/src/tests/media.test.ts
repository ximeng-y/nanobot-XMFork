import { describe, expect, it } from "vitest";

import { toMediaAttachment } from "@/lib/media";

describe("media normalization", () => {
  it("preserves managed identity and deleted state", () => {
    expect(toMediaAttachment({
      kind: "image",
      name: "deleted.png",
      url: "",
      managed_id: "att-1",
      deleted: true,
    })).toEqual({
      kind: "image",
      name: "deleted.png",
      url: "",
      managed_id: "att-1",
      deleted: true,
    });
  });
});
