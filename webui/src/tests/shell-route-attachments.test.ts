import { afterEach, describe, expect, it } from "vitest";

import { readShellRoute, shellRouteHash } from "@/shell-route";

describe("attachment settings route", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "#/new");
  });

  it("round-trips the attachments settings deep link", () => {
    const route = {
      view: "settings" as const,
      activeKey: null,
      settingsSection: "attachments" as const,
    };

    expect(shellRouteHash(route)).toBe("#/settings?section=attachments");
    window.history.replaceState(null, "", "#/settings?section=attachments");
    expect(readShellRoute()).toMatchObject(route);
  });
});
