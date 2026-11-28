import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryPage } from "@/components/memory/MemoryPage";
import type { MemoryImportEntry, MemoryPayload } from "@/lib/api";
import { readShellRoute, writeShellRoute } from "@/shell-route";

const apiMocks = vi.hoisted(() => ({
  fetchMemory: vi.fn(),
  fetchMemoryDocument: vi.fn(),
  fetchMemoryImport: vi.fn(),
  importMemoryFile: vi.fn(),
  deleteArchivedMemoryImports: vi.fn(),
}));
const clientMocks = vi.hoisted(() => ({
  client: { requestMutation: vi.fn() },
  getToken: vi.fn(() => "test-token"),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchMemory: apiMocks.fetchMemory,
    fetchMemoryDocument: apiMocks.fetchMemoryDocument,
    fetchMemoryImport: apiMocks.fetchMemoryImport,
    importMemoryFile: apiMocks.importMemoryFile,
    deleteArchivedMemoryImports: apiMocks.deleteArchivedMemoryImports,
  };
});

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => clientMocks,
}));

vi.mock("@/components/MarkdownText", () => ({
  MarkdownText: ({
    children,
    onOpenFilePreview,
  }: {
    children: string;
    onOpenFilePreview?: (path: string) => void;
  }) => (
    <div data-testid="memory-markdown">
      <pre>{children}</pre>
      {children.includes("[Setup]") ? (
        <button
          type="button"
          aria-label="memory/wiki/Guides/Setup.md"
          onClick={() => onOpenFilePreview?.("memory/wiki/Guides/Setup.md")}
        >
          Setup
        </button>
      ) : null}
      {children.includes("[Topic]") ? (
        <button
          type="button"
          aria-label="Encoded topic link"
          onClick={() => onOpenFilePreview?.("memory/wiki/topic#2.md")}
        >
          Topic
        </button>
      ) : null}
      {children.includes("[Missing]") ? (
        <button
          type="button"
          aria-label="Missing topic link"
          onClick={() => onOpenFilePreview?.("memory/wiki/missing.md")}
        >
          Missing
        </button>
      ) : null}
    </div>
  ),
}));

const pendingEntry: MemoryImportEntry = {
  id: "pending-1",
  name: "new-notes.md",
  bytes: 12,
  imported_at: "2026-11-28T01:00:00Z",
  status: "pending",
};

const archivedEntries: MemoryImportEntry[] = [
  {
    id: "archive-1",
    name: "old-a.md",
    bytes: 20,
    imported_at: "2026-11-27T01:00:00Z",
    archived_at: "2026-11-28T01:00:00Z",
    status: "archived",
  },
  {
    id: "archive-2",
    name: "old-b.md",
    bytes: 24,
    imported_at: "2026-11-26T01:00:00Z",
    archived_at: "2026-11-28T02:00:00Z",
    status: "archived",
  },
];

const memoryPayload: MemoryPayload = {
  documents: [
    { path: "SOUL.md" },
    { path: "memory/MEMORY.md" },
    { path: "memory/wiki/index.md" },
    { path: "memory/wiki/Guides/Setup.md" },
    { path: "memory/wiki/topic#2.md" },
    { path: "memory/wiki/topic?2.md" },
  ],
  imports: [pendingEntry, ...archivedEntries],
  max_import_bytes: 262_144,
};

const documentContents: Record<string, string> = {
  "SOUL.md": "# Agent soul\n",
  "memory/MEMORY.md": "# Memory root\n\n[Setup](wiki/Guides/Setup.md)\n[Topic](wiki/topic%232.md)\n[Missing](wiki/missing.md)\n",
  "memory/wiki/index.md": "# Wiki home\n",
  "memory/wiki/Guides/Setup.md": "# Setup guide\n",
  "memory/wiki/topic#2.md": "# Topic 2\n",
  "memory/wiki/topic?2.md": "# Topic query\n",
};

function renderMemoryPage() {
  return render(<MemoryPage onBackToChat={() => {}} />);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe("memory page", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "#/new");
    apiMocks.fetchMemory.mockReset().mockResolvedValue(memoryPayload);
    apiMocks.fetchMemoryDocument.mockReset().mockImplementation(
      async (_token: string, path: string) => ({
        path,
        content: documentContents[path] ?? "",
      }),
    );
    apiMocks.fetchMemoryImport.mockReset();
    apiMocks.importMemoryFile.mockReset();
    apiMocks.deleteArchivedMemoryImports.mockReset();
  });

  afterEach(() => {
    window.history.replaceState(null, "", "#/new");
  });

  it("round-trips the memory route while preserving the return chat", () => {
    const route = {
      view: "memory" as const,
      activeKey: "websocket:chat-1",
      temporary: true,
      settingsSection: "overview" as const,
    };
    writeShellRoute(route, true);

    expect(window.location.hash).toBe("#/memory");
    expect(readShellRoute()).toMatchObject(route);
  });

  it("opens wiki-relative links inside the memory document list", async () => {
    const user = userEvent.setup();
    renderMemoryPage();

    await user.click(await screen.findByRole("button", { name: "memory/MEMORY.md" }));
    await waitFor(() => {
      expect(apiMocks.fetchMemoryDocument).toHaveBeenCalledWith(
        "test-token",
        "memory/MEMORY.md",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("memory-markdown")).toHaveTextContent("Memory root");
    });

    await user.click(screen.getByRole(
      "button",
      { name: "memory/wiki/Guides/Setup.md" },
    ));
    await waitFor(() => {
      expect(apiMocks.fetchMemoryDocument).toHaveBeenLastCalledWith(
        "test-token",
        "memory/wiki/Guides/Setup.md",
      );
      expect(screen.getByTestId("memory-markdown")).toHaveTextContent("Setup guide");
    });
    expect(screen.getByRole("button", { name: "memory/wiki/Guides" }))
      .toHaveAttribute("aria-expanded", "true");
  });

  it("preserves URI delimiters in listed paths and encoded document links", async () => {
    const user = userEvent.setup();
    renderMemoryPage();

    await user.click(await screen.findByRole("button", { name: "memory/wiki/topic#2.md" }));
    await waitFor(() => {
      expect(apiMocks.fetchMemoryDocument).toHaveBeenLastCalledWith(
        "test-token",
        "memory/wiki/topic#2.md",
      );
    });

    await user.click(screen.getByRole("button", { name: "memory/wiki/topic?2.md" }));
    await waitFor(() => {
      expect(apiMocks.fetchMemoryDocument).toHaveBeenLastCalledWith(
        "test-token",
        "memory/wiki/topic?2.md",
      );
    });

    await user.click(screen.getByRole("button", { name: "memory/MEMORY.md" }));
    await user.click(await screen.findByRole("button", { name: "Missing topic link" }));
    expect(await screen.findByText("That link is not part of the memory library.")).toBeVisible();
    expect(apiMocks.fetchMemoryDocument).not.toHaveBeenCalledWith(
      "test-token",
      "memory/wiki/missing.md",
    );

    await user.click(screen.getByRole("button", { name: "Encoded topic link" }));
    await waitFor(() => {
      expect(apiMocks.fetchMemoryDocument).toHaveBeenLastCalledWith(
        "test-token",
        "memory/wiki/topic#2.md",
      );
      expect(screen.getByTestId("memory-markdown")).toHaveTextContent("Topic 2");
    });
  });

  it("refreshes the selected document without leaving the page and reloads on reselection", async () => {
    const user = userEvent.setup();
    let soulContent = "first soul";
    apiMocks.fetchMemoryDocument.mockImplementation(
      async (_token: string, path: string) => ({
        path,
        content: path === "SOUL.md" ? soulContent : documentContents[path] ?? "",
      }),
    );
    renderMemoryPage();

    const soul = await screen.findByRole("button", { name: "SOUL.md" });
    await user.click(soul);
    expect(await screen.findByText("first soul")).toBeVisible();

    soulContent = "second soul";
    await user.click(soul);
    expect(await screen.findByText("second soul")).toBeVisible();
    expect(apiMocks.fetchMemoryDocument).toHaveBeenCalledTimes(2);

    soulContent = "refreshed soul";
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("refreshed soul")).toBeVisible();
    expect(apiMocks.fetchMemory).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchMemoryDocument).toHaveBeenCalledTimes(3);
    expect(apiMocks.fetchMemoryDocument).toHaveBeenLastCalledWith("test-token", "SOUL.md");
  });

  it("drops the selected document when refresh removes it from the list", async () => {
    const user = userEvent.setup();
    const withoutSoul: MemoryPayload = {
      ...memoryPayload,
      documents: memoryPayload.documents.filter((document) => document.path !== "SOUL.md"),
    };
    apiMocks.fetchMemory
      .mockResolvedValueOnce(memoryPayload)
      .mockResolvedValueOnce(withoutSoul);
    renderMemoryPage();

    await user.click(await screen.findByRole("button", { name: "SOUL.md" }));
    await waitFor(() => {
      expect(screen.getByTestId("memory-markdown")).toHaveTextContent("Agent soul");
    });

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(apiMocks.fetchMemory).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(screen.queryByTestId("memory-markdown")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Choose a file from the list to read it.")).toBeVisible();
    expect(apiMocks.fetchMemoryDocument).toHaveBeenCalledTimes(1);
  });

  it("uploads file bytes as standard Base64 and reports the result", async () => {
    const user = userEvent.setup();
    apiMocks.importMemoryFile.mockResolvedValue({
      entry: {
        ...pendingEntry,
        id: "pending-uploaded",
        name: "notes.md",
        bytes: 7,
      },
    });
    renderMemoryPage();

    await waitFor(() => expect(apiMocks.fetchMemory).toHaveBeenCalled());
    await user.click(await screen.findByRole("tab", { name: /Import & archive/ }));
    const file = new File(["你好\n"], "notes.md", { type: "text/markdown" });
    await user.upload(screen.getByTestId("memory-file-input"), file);

    await waitFor(() => {
      expect(apiMocks.importMemoryFile).toHaveBeenCalledWith(
        expect.any(Object),
        "notes.md",
        "5L2g5aW9Cg==",
      );
    });
    expect(await screen.findByText("Uploaded")).toBeVisible();
    expect(screen.getByText("1 uploaded; 0 failed.")).toBeVisible();
  });

  it("keeps the latest import preview when earlier responses arrive late", async () => {
    const user = userEvent.setup();
    const first = deferred<{
      entry: MemoryImportEntry;
      content: string;
    }>();
    const second = deferred<{
      entry: MemoryImportEntry;
      content: string;
    }>();
    apiMocks.fetchMemoryImport
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    renderMemoryPage();

    await waitFor(() => expect(apiMocks.fetchMemory).toHaveBeenCalled());
    await user.click(await screen.findByRole("tab", { name: /Import & archive/ }));
    fireEvent.click(within(screen.getByTestId("memory-import-pending-1"))
      .getByRole("button", { name: "Preview original" }));
    fireEvent.click(within(screen.getByTestId("memory-import-archive-1"))
      .getByRole("button", { name: "Preview original", hidden: true }));

    await act(async () => {
      second.resolve({
        entry: archivedEntries[0],
        content: "second original",
      });
      await second.promise;
    });
    expect(await screen.findByText("second original")).toBeVisible();

    await act(async () => {
      first.resolve({
        entry: pendingEntry,
        content: "stale original",
      });
      await first.promise;
    });
    expect(screen.queryByText("stale original")).not.toBeInTheDocument();
    expect(screen.getByText("second original")).toBeVisible();
  });

  it("confirms archived deletion and shows a partial result", async () => {
    const user = userEvent.setup();
    apiMocks.deleteArchivedMemoryImports.mockResolvedValue({
      deleted: ["archive-1"],
      failed: ["archive-2"],
    });
    apiMocks.fetchMemory
      .mockResolvedValueOnce(memoryPayload)
      .mockResolvedValueOnce({
        ...memoryPayload,
        imports: [pendingEntry, archivedEntries[1]],
      });
    renderMemoryPage();

    await waitFor(() => expect(apiMocks.fetchMemory).toHaveBeenCalledTimes(1));
    await user.click(await screen.findByRole("tab", { name: /Import & archive/ }));
    await user.click(screen.getByLabelText("Select old-a.md"));
    await user.click(screen.getByLabelText("Select old-b.md"));
    await user.click(screen.getByRole("button", { name: "Delete selected (2)" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiMocks.deleteArchivedMemoryImports).toHaveBeenCalledWith(
        expect.any(Object),
        ["archive-1", "archive-2"],
      );
    });
    expect(await screen.findByText("Deleted 1; 1 could not be deleted.")).toBeVisible();
    expect(screen.getByText("old-b.md was not deleted.")).toBeVisible();
  });
});
