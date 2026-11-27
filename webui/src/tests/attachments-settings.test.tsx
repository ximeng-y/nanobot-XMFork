import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AttachmentsSettings } from "@/components/settings/AttachmentsSettings";
import {
  installSettingsViewTestHooks,
  jsonResponse,
  renderSettingsView,
  settingsPayload,
} from "@/tests/settings-test-utils";
import type { WebUIMutationTransport } from "@/lib/api";
import type { ManagedAttachmentsPayload } from "@/lib/types";

function attachmentPayload(overrides: Partial<ManagedAttachmentsPayload> = {}): ManagedAttachmentsPayload {
  return {
    items: [{
      id: "att-1",
      name: "report.pdf",
      kind: "file",
      source: "upload",
      bytes: 2048,
      created_at: "2026-01-02T03:04:05Z",
      unreferenced_since: null,
      references: [{ key: "websocket:chat-1", title: "Research chat" }],
      status: "available",
    }],
    usage: {
      managed_bytes: 2048,
      pending_bytes: 512,
      protected_bytes: 1024,
    },
    settings: {
      max_bytes: null,
      auto_cleanup: false,
      orphan_days: 30,
    },
    ...overrides,
  };
}

function installFetch(payloads: ManagedAttachmentsPayload[]) {
  const queue = [...payloads];
  const fallback = payloads[payloads.length - 1] ?? attachmentPayload({ items: [] });
  const fetchMock = vi.fn(async () => jsonResponse(queue.shift() ?? fallback));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("attachment management settings", () => {
  installSettingsViewTestHooks();

  it("renders the settings entry, usage, references, and a reached-limit warning", async () => {
    const payload = attachmentPayload({
      usage: { managed_bytes: 1024, pending_bytes: 512, protected_bytes: 1024 },
      settings: { max_bytes: 1024, auto_cleanup: false, orphan_days: 30 },
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/attachments/managed")) return Promise.resolve(jsonResponse(payload));
      if (url.includes("/api/settings")) return Promise.resolve(jsonResponse(settingsPayload()));
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({
      initialSection: "attachments",
      initialSettings: settingsPayload(),
    });

    expect(await screen.findByText("Managed storage")).toBeVisible();
    expect(screen.getByText("report.pdf")).toBeVisible();
    expect(screen.getByText("Research chat")).toBeVisible();
    expect(screen.getByRole("switch", { name: "Automatic cleanup" })).not.toBeChecked();
    expect(screen.getByRole("spinbutton", { name: "Cleanup after" })).toHaveValue(30);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Current usage has reached or exceeded the limit",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/attachments/managed",
      expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
    );
  });

  it("previews referenced files, confirms deletion, and reports the actual freed size", async () => {
    const user = userEvent.setup();
    const initial = attachmentPayload();
    const empty = attachmentPayload({
      items: [],
      usage: { managed_bytes: 0, pending_bytes: 0, protected_bytes: 0 },
    });
    const fetchMock = installFetch([initial, empty]);
    const requestMutation = vi.fn(async (action: string) => {
      if (action === "attachments.preview_delete") {
        return {
          items: [{
            id: "att-1",
            name: "report.pdf",
            bytes: 2048,
            files: ["media/report.pdf"],
          }],
          affected_sessions: [{ key: "websocket:chat-1", title: "Research chat" }],
          estimated_freed_bytes: 2048,
          blocked: [],
        };
      }
      if (action === "attachments.delete") {
        return {
          deleted: ["att-1"],
          failed: [],
          freed_bytes: 3072,
          affected_sessions: [{ key: "websocket:chat-1", title: "Research chat" }],
        };
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    render(
      <AttachmentsSettings
        token="tok"
        transport={{ requestMutation } as unknown as WebUIMutationTransport}
      />,
    );

    await user.click(await screen.findByRole("checkbox", { name: "Select report.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete selected" }));

    const dialog = await screen.findByRole("dialog", { name: "Delete managed attachments?" });
    expect(dialog).toHaveTextContent("Research chat");
    expect(dialog).toHaveTextContent("media/report.pdf");
    expect(dialog).toHaveTextContent("Estimated release: 2 KiB");
    expect(requestMutation).toHaveBeenCalledWith(
      "attachments.preview_delete",
      { ids: ["att-1"] },
      20_000,
    );

    await user.click(within(dialog).getByRole("button", { name: "Delete files" }));

    expect(await screen.findByText("Deleted the selected files and released 3 KiB.")).toBeVisible();
    expect(await screen.findByText("No managed attachments.")).toBeVisible();
    expect(requestMutation).toHaveBeenCalledWith(
      "attachments.delete",
      { ids: ["att-1"] },
      20_000,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("converts a GiB capacity to integer bytes and saves immediate cleanup policy", async () => {
    const user = userEvent.setup();
    installFetch([attachmentPayload()]);
    const updated = attachmentPayload({
      settings: {
        max_bytes: 2 * 1024 ** 3,
        auto_cleanup: true,
        orphan_days: 14,
      },
    });
    const requestMutation = vi.fn(async (action: string, values: Record<string, unknown>) => {
      expect(action).toBe("attachments.settings.update");
      expect(values).toEqual({
        max_bytes: 2 * 1024 ** 3,
        auto_cleanup: true,
        orphan_days: 14,
      });
      return updated;
    });

    render(
      <AttachmentsSettings
        token="tok"
        transport={{ requestMutation } as unknown as WebUIMutationTransport}
      />,
    );

    const capacity = await screen.findByRole("spinbutton", { name: "Long-term capacity" });
    await user.type(capacity, "2");
    await user.click(screen.getByRole("switch", { name: "Automatic cleanup" }));
    const days = screen.getByRole("spinbutton", { name: "Cleanup after" });
    await user.clear(days);
    await user.type(days, "14");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Attachment settings saved.")).toBeVisible();
    expect(requestMutation).toHaveBeenCalledTimes(1);
  });

  it("shows a retry state when the management list cannot load", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new Error("network down")));
    render(
      <AttachmentsSettings
        token="tok"
        transport={{ requestMutation: vi.fn() } as unknown as WebUIMutationTransport}
      />,
    );

    expect(await screen.findByText("Could not load settings")).toBeVisible();
    expect(screen.getByText("network down")).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("reports per-file deletion failures without claiming the files were deleted", async () => {
    const user = userEvent.setup();
    installFetch([attachmentPayload()]);
    const requestMutation = vi.fn(async (action: string, values: Record<string, unknown>) => {
      if (action === "attachments.preview_delete") {
        return {
          items: [{
            id: "att-1",
            name: "report.pdf",
            bytes: 2048,
            files: ["media/report.pdf"],
          }],
          affected_sessions: [{ key: "websocket:chat-1", title: "Research chat" }],
          estimated_freed_bytes: 2048,
          blocked: [],
        };
      }
      if (action === "attachments.delete") {
        expect(values).toEqual({ ids: ["att-1"] });
        return {
          deleted: [],
          failed: [{ id: "att-1", reason: "file is locked" }],
          freed_bytes: 0,
          affected_sessions: [{ key: "websocket:chat-1", title: "Research chat" }],
        };
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    render(
      <AttachmentsSettings
        token="tok"
        transport={{ requestMutation } as unknown as WebUIMutationTransport}
      />,
    );

    await user.click(await screen.findByRole("checkbox", { name: "Select report.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete managed attachments?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete files" }));

    expect(await screen.findByText("Some selected files could not be deleted.")).toBeVisible();
    expect(screen.getByText("report.pdf: file is locked")).toBeVisible();
    expect(screen.queryByText(/released .* files/i)).not.toBeInTheDocument();
  });

  it("keeps the edited policy visible when saving fails", async () => {
    const user = userEvent.setup();
    installFetch([attachmentPayload()]);
    const requestMutation = vi.fn().mockRejectedValueOnce(new Error("denied"));

    render(
      <AttachmentsSettings
        token="tok"
        transport={{ requestMutation } as unknown as WebUIMutationTransport}
      />,
    );

    const capacity = await screen.findByRole("spinbutton", { name: "Long-term capacity" });
    await user.type(capacity, "1");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save attachment settings: denied");
    expect(capacity).toHaveValue(1);
  });
});
