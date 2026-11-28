import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { MarkdownText } from "@/components/MarkdownText";
import { MemoryIcon } from "@/components/icons/product-icons";
import {
  deleteArchivedMemoryImports,
  fetchMemory,
  fetchMemoryDocument,
  fetchMemoryImport,
  importMemoryFile,
  type MemoryDocument,
  type MemoryImportEntry,
  type MemoryPayload,
} from "@/lib/api";
import { fmtDateTime } from "@/lib/format-time";
import { cn } from "@/lib/utils";
import { usePageVisibility } from "@/hooks/usePageVisibility";
import { useClient } from "@/providers/ClientProvider";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  buildWikiTree,
  fileContentBase64,
  formatMemoryBytes,
  isResidentMemoryPath,
  isWikiMemoryPath,
  memoryImportsByStatus,
  normalizeMemoryPath,
  RESIDENT_MEMORY_PATHS,
  wikiAncestorPaths,
  type WikiTreeNode,
} from "./memory-model";

type MemoryTab = "library" | "imports";
type DocumentState =
  | { status: "loading"; path: string }
  | { status: "ready"; path: string; content: string }
  | { status: "error"; path: string; message: string };
type UploadState = "queued" | "uploading" | "uploaded" | "failed";
type UploadRow = {
  key: string;
  name: string;
  bytes: number;
  state: UploadState;
  error?: string;
};

const REFRESH_INTERVAL_MS = 20_000;

function errorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message.trim()) return reason.message.trim();
  return String(reason);
}

function failBadgeClass() {
  return "border-destructive/25 bg-destructive/10 text-destructive";
}

function WikiNodes({
  nodes,
  depth = 0,
  expanded,
  selectedPath,
  onToggle,
  onOpen,
}: {
  nodes: WikiTreeNode[];
  depth?: number;
  expanded: Set<string>;
  selectedPath: string | null;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
}) {
  const { t } = useTranslation();
  return nodes.map((node) => {
    const indent = 8 + depth * 14;
    if (node.kind === "directory") {
      const isExpanded = expanded.has(node.path);
      return (
        <div key={node.path}>
          <button
            type="button"
            aria-expanded={isExpanded}
            aria-label={node.path}
            className="flex min-h-8 w-full items-center gap-1.5 rounded-control py-1 pr-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/45 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={{ paddingLeft: indent }}
            onClick={() => onToggle(node.path)}
          >
            {isExpanded
              ? <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
              : <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />}
            {isExpanded
              ? <FolderOpen className="h-4 w-4 shrink-0" aria-hidden />
              : <Folder className="h-4 w-4 shrink-0" aria-hidden />}
            <span className="truncate">{node.name}</span>
          </button>
          {isExpanded ? <div className="mt-0.5 space-y-0.5">
            <WikiNodes
              nodes={node.children}
              depth={depth + 1}
              expanded={expanded}
              selectedPath={selectedPath}
              onToggle={onToggle}
              onOpen={onOpen}
            />
          </div> : null}
        </div>
      );
    }
    const label = node.path === "memory/wiki/index.md"
      ? t("memory.library.wikiIndex")
      : node.name;
    return (
      <button
        key={node.path}
        type="button"
        aria-label={node.path}
        aria-current={selectedPath === node.path ? "page" : undefined}
        className={cn(
          "flex min-h-8 w-full items-center gap-1.5 rounded-control py-1 pr-2 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          selectedPath === node.path
            ? "bg-muted/70 text-foreground"
            : "text-muted-foreground hover:bg-muted/45 hover:text-foreground",
        )}
        style={{ paddingLeft: indent + 18 }}
        onClick={() => onOpen(node.path)}
      >
        <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate">{label}</span>
      </button>
    );
  });
}

function EmptyState({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-32 flex-col items-center justify-center px-4 py-8 text-center text-sm text-muted-foreground">
      {icon}
      <p className="mt-3 max-w-sm">{children}</p>
    </div>
  );
}

function StatusBadge({
  entry,
}: {
  entry: MemoryImportEntry;
}) {
  const { t } = useTranslation();
  return (
    <span className={cn(
      "inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] leading-4",
      entry.status === "failed"
        ? failBadgeClass()
        : entry.status === "archived"
          ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-border/60 bg-muted/45 text-muted-foreground",
    )}>
      {t(`memory.imports.status.${entry.status}`)}
    </span>
  );
}

function ImportRow({
  entry,
  selectable,
  selected,
  deleting,
  onSelect,
  onPreview,
  onDelete,
}: {
  entry: MemoryImportEntry;
  selectable: boolean;
  selected: boolean;
  deleting: boolean;
  onSelect: (checked: boolean) => void;
  onPreview: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const icon = entry.status === "failed"
    ? <XCircle className="h-4 w-4 text-destructive" aria-hidden />
    : entry.status === "archived"
      ? <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" aria-hidden />
      : <AlertCircle className="h-4 w-4 text-muted-foreground" aria-hidden />;
  return (
    <div
      data-testid={`memory-import-${entry.id}`}
      className="flex min-w-0 items-start gap-2.5 border-t border-border/40 px-3 py-3 first:border-t-0 sm:px-4"
    >
      {selectable ? (
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 shrink-0 accent-primary"
          aria-label={t("memory.imports.selectFile", { name: entry.name })}
          checked={selected}
          disabled={deleting}
          onChange={(event) => onSelect(event.currentTarget.checked)}
        />
      ) : null}
      {icon}
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <p className="min-w-0 break-all text-[13px] font-medium leading-5 text-foreground">
            {entry.name}
          </p>
          <StatusBadge entry={entry} />
        </div>
        <p className="mt-0.5 text-[11px] leading-5 text-muted-foreground">
          {formatMemoryBytes(entry.bytes)}
          <span aria-hidden> · </span>
          {entry.status === "archived" && entry.archived_at
            ? t("memory.imports.archivedAt", { date: fmtDateTime(entry.archived_at) })
            : t("memory.imports.importedAt", { date: fmtDateTime(entry.imported_at) })}
        </p>
        {entry.error ? (
          <p className="mt-1 break-words text-[11px] leading-5 text-destructive">{entry.error}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          aria-label={t("memory.imports.preview")}
          title={t("memory.imports.preview")}
          onClick={onPreview}
        >
          <Eye className="h-4 w-4" aria-hidden />
        </Button>
        {entry.status === "archived" ? (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-8 w-8 text-muted-foreground hover:text-destructive"
            aria-label={t("memory.imports.delete")}
            title={t("memory.imports.delete")}
            disabled={deleting}
            onClick={onDelete}
          >
            <Trash2 className="h-4 w-4" aria-hidden />
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function MemoryPage({
  mainNavigationExpanded = false,
  hostChromeInset = false,
  onBackToChat,
}: {
  mainNavigationExpanded?: boolean;
  hostChromeInset?: boolean;
  onBackToChat: () => void;
}) {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const pageVisible = usePageVisibility();
  const mountedRef = useRef(true);
  const refreshingRef = useRef(false);
  const documentRequestRef = useRef(0);
  const previewRequestRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<MemoryTab>("library");
  const [payload, setPayload] = useState<MemoryPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expandedWiki, setExpandedWiki] = useState<Set<string>>(() => new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [documentState, setDocumentState] = useState<DocumentState | null>(null);
  const [mobileReading, setMobileReading] = useState(false);
  const [libraryNotice, setLibraryNotice] = useState<string | null>(null);
  const [uploadRows, setUploadRows] = useState<UploadRow[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState<{
    entry: MemoryImportEntry;
    content: string | null;
    loading: boolean;
    error: string | null;
  } | null>(null);
  const [deleteRequest, setDeleteRequest] = useState<MemoryImportEntry[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteFailures, setDeleteFailures] = useState<string[]>([]);

  const loadMemory = useCallback(async ({
    initial = false,
    silent = false,
  }: { initial?: boolean; silent?: boolean } = {}) => {
    if (refreshingRef.current) return;
    refreshingRef.current = true;
    if (initial) setLoading(true);
    if (!silent) setRefreshing(true);
    try {
      const next = await fetchMemory(getToken());
      if (!mountedRef.current) return;
      setPayload(next);
      setLoadError(null);
    } catch (reason) {
      if (!mountedRef.current) return;
      setLoadError(errorMessage(reason));
    } finally {
      refreshingRef.current = false;
      if (mountedRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [getToken]);

  useEffect(() => {
    mountedRef.current = true;
    void loadMemory({ initial: true });
    return () => {
      mountedRef.current = false;
      documentRequestRef.current += 1;
      previewRequestRef.current += 1;
    };
  }, [loadMemory]);

  const documents = payload?.documents ?? [];
  const documentPaths = useMemo(
    () => new Set(documents.map((document) => document.path)),
    [documents],
  );
  const residentDocuments = useMemo(() => {
    const order = new Map<string, number>(
      RESIDENT_MEMORY_PATHS.map((path, index) => [path, index]),
    );
    return documents
      .filter((document) => isResidentMemoryPath(document.path))
      .sort((left, right) => (order.get(left.path) ?? 0) - (order.get(right.path) ?? 0));
  }, [documents]);
  const wikiDocuments = useMemo(
    () => documents.filter((document) => isWikiMemoryPath(document.path)),
    [documents],
  );
  const wikiTree = useMemo(() => buildWikiTree(wikiDocuments), [wikiDocuments]);
  const importGroups = useMemo(
    () => memoryImportsByStatus(payload?.imports ?? []),
    [payload],
  );
  const archivedEntries = importGroups.archived;
  const pendingEntries = importGroups.pending;
  const selectedArchivedEntries = archivedEntries.filter((entry) => selectedIds.has(entry.id));
  const maxImportBytes = payload?.max_import_bytes ?? null;

  useEffect(() => {
    const archivedIds = new Set(archivedEntries.map((entry) => entry.id));
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => archivedIds.has(id)));
      return next.size === current.size && [...next].every((id) => current.has(id))
        ? current
        : next;
    });
  }, [archivedEntries]);

  useEffect(() => {
    if (!pageVisible || !pendingEntries.length) return;
    const timer = window.setInterval(() => {
      void loadMemory({ silent: true });
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadMemory, pageVisible, pendingEntries.length]);

  const openDocument = useCallback((rawPath: string) => {
    const path = normalizeMemoryPath(rawPath);
    if (!path || !documentPaths.has(path)) {
      setLibraryNotice(t("memory.library.linkUnavailable"));
      return;
    }
    setLibraryNotice(null);
    setSelectedPath(path);
    setMobileReading(true);
    setExpandedWiki((current) => {
      const next = new Set(current);
      wikiAncestorPaths(path).forEach((ancestor) => next.add(ancestor));
      return next;
    });
    if (documentState?.status === "ready" && documentState.path === path) return;

    const request = ++documentRequestRef.current;
    setDocumentState({ status: "loading", path });
    void fetchMemoryDocument(getToken(), path)
      .then((document) => {
        if (!mountedRef.current || request !== documentRequestRef.current) return;
        setDocumentState({ status: "ready", path, content: document.content });
      })
      .catch((reason) => {
        if (!mountedRef.current || request !== documentRequestRef.current) return;
        setDocumentState({
          status: "error",
          path,
          message: errorMessage(reason),
        });
      });
  }, [documentPaths, documentState, getToken, t]);

  const toggleWikiFolder = (path: string) => {
    setExpandedWiki((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const openPreview = async (entry: MemoryImportEntry) => {
    const request = ++previewRequestRef.current;
    setPreview({ entry, content: null, loading: true, error: null });
    try {
      const result = await fetchMemoryImport(getToken(), entry.id);
      if (!mountedRef.current || request !== previewRequestRef.current) return;
      setPreview({
        entry: result.entry,
        content: result.content,
        loading: false,
        error: null,
      });
    } catch (reason) {
      if (!mountedRef.current || request !== previewRequestRef.current) return;
      setPreview({
        entry,
        content: null,
        loading: false,
        error: errorMessage(reason),
      });
    }
  };

  const closePreview = () => {
    previewRequestRef.current += 1;
    setPreview(null);
  };

  const updateUploadRow = (key: string, patch: Partial<UploadRow>) => {
    setUploadRows((current) => current.map((row) => (
      row.key === key ? { ...row, ...patch } : row
    )));
  };

  const uploadFiles = async (files: File[]) => {
    if (!files.length || maxImportBytes === null || uploading) return;
    const rows = files.map((file, index): UploadRow => ({
      key: `${index}:${file.name}:${file.size}:${file.lastModified}`,
      name: file.name,
      bytes: file.size,
      state: "queued",
    }));
    setUploadRows(rows);
    setUploading(true);
    setLoadError(null);
    let imported = 0;
    try {
      for (const [index, file] of files.entries()) {
        const row = rows[index];
        if (!file.name.toLowerCase().endsWith(".md")) {
          updateUploadRow(row.key, {
            state: "failed",
            error: t("memory.imports.onlyMarkdown"),
          });
          continue;
        }
        if (file.size > maxImportBytes) {
          updateUploadRow(row.key, {
            state: "failed",
            error: t("memory.imports.tooLarge", { size: formatMemoryBytes(maxImportBytes) }),
          });
          continue;
        }
        updateUploadRow(row.key, { state: "uploading" });
        try {
          const contentBase64 = await fileContentBase64(file);
          const result = await importMemoryFile(client, file.name, contentBase64);
          if (!mountedRef.current) return;
          imported += 1;
          updateUploadRow(row.key, { state: "uploaded" });
          setPayload((current) => current ? {
            ...current,
            imports: [
              result.entry,
              ...current.imports.filter((entry) => entry.id !== result.entry.id),
            ],
          } : current);
        } catch (reason) {
          if (!mountedRef.current) return;
          updateUploadRow(row.key, {
            state: "failed",
            error: errorMessage(reason),
          });
        }
      }
    } finally {
      if (mountedRef.current) setUploading(false);
    }
    if (imported > 0 && mountedRef.current) {
      await loadMemory({ silent: true });
    }
  };

  const requestDelete = (entries: MemoryImportEntry[]) => {
    const archivedIds = new Set(archivedEntries.map((entry) => entry.id));
    const ids = entries.filter((entry) => archivedIds.has(entry.id));
    if (!ids.length) return;
    setDeleteNotice(null);
    setDeleteError(null);
    setDeleteFailures([]);
    setDeleteRequest(ids);
  };

  const confirmDelete = async () => {
    if (!deleteRequest?.length) return;
    setDeleting(true);
    setDeleteError(null);
    setDeleteFailures([]);
    try {
      const result = await deleteArchivedMemoryImports(
        client,
        deleteRequest.map((entry) => entry.id),
      );
      if (!mountedRef.current) return;
      setPayload((current) => current ? {
        ...current,
        imports: current.imports.filter((entry) => !result.deleted.includes(entry.id)),
      } : current);
      setSelectedIds(new Set(result.failed));
      setDeleteFailures(result.failed);
      if (result.deleted.length && result.failed.length) {
        setDeleteNotice(t("memory.imports.deletePartial", {
          deleted: result.deleted.length,
          failed: result.failed.length,
        }));
      } else if (result.deleted.length) {
        setDeleteNotice(t("memory.imports.deleteSuccess", { count: result.deleted.length }));
      } else {
        setDeleteError(t("memory.imports.deleteError"));
      }
      setDeleteRequest(null);
      await loadMemory({ silent: true });
    } catch (reason) {
      if (!mountedRef.current) return;
      setDeleteError(errorMessage(reason));
    } finally {
      if (mountedRef.current) setDeleting(false);
    }
  };

  const tabOptions = [
    { value: "library" as const, label: t("memory.tabs.library") },
    {
      value: "imports" as const,
      label: (
        <span className="inline-flex items-center gap-1.5">
          {t("memory.tabs.imports")}
          {pendingEntries.length ? (
            <span className="rounded-full bg-muted px-1.5 text-[10px] leading-4 text-muted-foreground">
              {pendingEntries.length}
            </span>
          ) : null}
        </span>
      ),
    },
  ];
  const uploadFinished = !uploading && uploadRows.length > 0
    && uploadRows.every((row) => row.state === "uploaded" || row.state === "failed");

  const content = loading && !payload
    ? <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {t("memory.library.loading")}
    </div>
    : !payload && loadError
      ? <EmptyState icon={<AlertCircle className="h-6 w-6" aria-hidden />}>
        <span className="block">{t("memory.library.loadError")}</span>
        <span className="mt-1 block break-words text-xs">{loadError}</span>
        <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => void loadMemory()}>
          {t("memory.refresh")}
        </Button>
      </EmptyState>
      : tab === "library"
        ? <div className="min-h-0 md:flex md:flex-1">
          <aside className={cn(
            "min-h-0 shrink-0 overflow-y-auto border-b border-border/45 p-3 md:w-60 md:border-b-0 md:border-r lg:w-64",
            mobileReading && "hidden md:block",
          )}>
            <div className="mb-3 flex items-center gap-2 px-1 text-[11px] text-muted-foreground">
              <MemoryIcon className="h-3.5 w-3.5" />
              <span>{t("memory.scope")}</span>
            </div>
            {!residentDocuments.length && !wikiDocuments.length ? (
              <EmptyState icon={<MemoryIcon className="h-6 w-6" aria-hidden />}>
                {t("memory.library.empty")}
              </EmptyState>
            ) : <>
              {residentDocuments.length ? (
                <section aria-labelledby="memory-resident-heading">
                  <h2 id="memory-resident-heading" className="px-1 pb-1.5 text-[11px] font-medium text-muted-foreground">
                    {t("memory.library.resident")}
                  </h2>
                  <div className="space-y-0.5">
                    {residentDocuments.map((document: MemoryDocument) => (
                      <button
                        key={document.path}
                        type="button"
                        aria-label={document.path}
                        aria-current={selectedPath === document.path ? "page" : undefined}
                        className={cn(
                          "flex min-h-9 w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          selectedPath === document.path
                            ? "bg-muted/70 text-foreground"
                            : "text-muted-foreground hover:bg-muted/45 hover:text-foreground",
                        )}
                        onClick={() => openDocument(document.path)}
                      >
                        <FileText className="h-4 w-4 shrink-0" aria-hidden />
                        <span className="min-w-0 truncate">{document.path === "memory/MEMORY.md" ? "MEMORY.md" : document.path}</span>
                      </button>
                    ))}
                  </div>
                </section>
              ) : null}
              {wikiDocuments.length ? (
                <section className={residentDocuments.length ? "mt-4" : undefined} aria-labelledby="memory-wiki-heading">
                  <h2 id="memory-wiki-heading" className="px-1 pb-1.5 text-[11px] font-medium text-muted-foreground">
                    {t("memory.library.wiki")}
                  </h2>
                  <div className="space-y-0.5">
                    <WikiNodes
                      nodes={wikiTree.children}
                      expanded={expandedWiki}
                      selectedPath={selectedPath}
                      onToggle={toggleWikiFolder}
                      onOpen={openDocument}
                    />
                  </div>
                </section>
              ) : null}
            </>}
          </aside>
          <section className={cn(
            "flex min-h-0 min-w-0 flex-1 flex-col",
            !mobileReading && "hidden md:flex",
          )}>
            <div className="flex min-h-12 items-center gap-2 border-b border-border/45 px-3 sm:px-4">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 shrink-0 px-2 md:hidden"
                onClick={() => setMobileReading(false)}
              >
                <ChevronLeft className="mr-1 h-4 w-4" aria-hidden />
                {t("memory.library.backToFiles")}
              </Button>
              {selectedPath ? (
                <p className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground" title={selectedPath}>
                  {selectedPath}
                </p>
              ) : null}
            </div>
            {libraryNotice ? <div role="alert" className="mx-3 mt-3 rounded-control border border-destructive/25 bg-destructive/10 px-3 py-2 text-xs text-destructive sm:mx-4">
              {libraryNotice}
            </div> : null}
            <div className="min-h-0 flex-1 overflow-y-auto">
              {!selectedPath || !documentState ? (
                <EmptyState icon={<FileText className="h-6 w-6" aria-hidden />}>
                  {t("memory.library.selectFile")}
                </EmptyState>
              ) : documentState.status === "loading" ? (
                <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  {t("memory.library.documentLoading")}
                </div>
              ) : documentState.status === "error" ? (
                <EmptyState icon={<AlertCircle className="h-6 w-6" aria-hidden />}>
                  <span className="block">{t("memory.library.documentError")}</span>
                  <span className="mt-1 block break-words text-xs">{documentState.message}</span>
                </EmptyState>
              ) : (
                <MarkdownText
                  documentPath={documentState.path}
                  onOpenFilePreview={openDocument}
                  className="px-4 pb-8 pt-2 text-reading sm:px-6 [&>:first-child]:mt-0"
                >
                  {documentState.content}
                </MarkdownText>
              )}
            </div>
          </section>
        </div>
        : <div className="p-4 sm:p-6">
          <div className="rounded-control border border-border/55 bg-muted/25 px-3.5 py-3 text-[12px] leading-5 text-muted-foreground">
            {t("memory.imports.dreamHint")}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={uploading || maxImportBytes === null}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading
                ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                : <Upload className="mr-1.5 h-3.5 w-3.5" aria-hidden />}
              {uploading ? t("memory.imports.uploading") : t("memory.imports.chooseFiles")}
            </Button>
            {maxImportBytes !== null ? (
              <span className="text-[11px] text-muted-foreground">
                {t("memory.imports.maxSize", { size: formatMemoryBytes(maxImportBytes) })}
              </span>
            ) : null}
            <input
              ref={fileInputRef}
              data-testid="memory-file-input"
              type="file"
              accept=".md,text/markdown"
              multiple
              hidden
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = "";
                void uploadFiles(files);
              }}
            />
          </div>

          {uploadRows.length ? (
            <div className="mt-3 rounded-control border border-border/50 bg-settings-surface px-3 py-2">
              {uploadFinished ? <p className="text-[11px] text-muted-foreground">
                {t("memory.imports.uploadSummary", {
                  succeeded: uploadRows.filter((row) => row.state === "uploaded").length,
                  failed: uploadRows.filter((row) => row.state === "failed").length,
                })}
              </p> : null}
              <div className="mt-1.5 space-y-1.5">
                {uploadRows.map((row) => {
                  const statusLabel = row.state === "uploaded"
                    ? t("memory.imports.uploaded")
                    : row.state === "failed"
                      ? t("memory.imports.uploadFailed")
                      : t("memory.imports.uploading");
                  return (
                    <div key={row.key} className="flex min-w-0 items-start gap-2 text-[11px]">
                      {row.state === "uploaded"
                        ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                        : row.state === "failed"
                          ? <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden />
                          : <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />}
                      <span className="min-w-0 flex-1">
                        <span className="break-all text-foreground">{row.name}</span>
                        <span className={cn(
                          "ml-1.5",
                          row.state === "failed" ? "text-destructive" : "text-muted-foreground",
                        )}>{statusLabel}</span>
                        {row.error ? <span className="mt-0.5 block break-words text-destructive">{row.error}</span> : null}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {deleteNotice ? <div role="status" className="mt-3 rounded-control border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300">{deleteNotice}</div> : null}
          {deleteError ? <div role="alert" className="mt-3 rounded-control border border-destructive/25 bg-destructive/10 px-3 py-2 text-xs text-destructive">{deleteError}</div> : null}
          {deleteFailures.length ? <ul className="mt-2 space-y-1 px-1 text-xs text-destructive">
            {deleteFailures.map((id) => {
              const entry = payload?.imports.find((item) => item.id === id);
              return <li key={id}>{t("memory.imports.deleteFailedItem", { name: entry?.name ?? id })}</li>;
            })}
          </ul> : null}

          <section className="mt-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <h2 className="text-ui-heading text-foreground/85">{t("memory.imports.pending")}</h2>
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {pendingEntries.length}
                </span>
              </div>
              {refreshing ? <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                {t("memory.refreshing")}
              </span> : null}
            </div>
            <div className="mt-2 overflow-hidden rounded-panel border border-border/45 bg-settings-surface">
              {pendingEntries.length ? pendingEntries.map((entry) => (
                <ImportRow
                  key={entry.id}
                  entry={entry}
                  selectable={false}
                  selected={false}
                  deleting={deleting}
                  onSelect={() => {}}
                  onPreview={() => void openPreview(entry)}
                  onDelete={() => {}}
                />
              )) : <EmptyState icon={<Archive className="h-6 w-6" aria-hidden />}>
                {t("memory.imports.emptyPending")}
              </EmptyState>}
            </div>
          </section>

          <section className="mt-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <h2 className="text-ui-heading text-foreground/85">{t("memory.imports.archived")}</h2>
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {archivedEntries.length}
                </span>
              </div>
              {selectedArchivedEntries.length ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] text-muted-foreground">
                    {t("memory.imports.selectedCount", { count: selectedArchivedEntries.length })}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={deleting}
                    onClick={() => setSelectedIds(new Set())}
                  >
                    {t("memory.imports.clearSelection")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    disabled={deleting}
                    onClick={() => requestDelete(selectedArchivedEntries)}
                  >
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    {t("memory.imports.deleteSelected", { count: selectedArchivedEntries.length })}
                  </Button>
                </div>
              ) : null}
            </div>
            <div className="mt-2 overflow-hidden rounded-panel border border-border/45 bg-settings-surface">
              {archivedEntries.length ? archivedEntries.map((entry) => (
                <ImportRow
                  key={entry.id}
                  entry={entry}
                  selectable
                  selected={selectedIds.has(entry.id)}
                  deleting={deleting}
                  onSelect={(checked) => {
                    setSelectedIds((current) => {
                      const next = new Set(current);
                      if (checked) next.add(entry.id);
                      else next.delete(entry.id);
                      return next;
                    });
                  }}
                  onPreview={() => void openPreview(entry)}
                  onDelete={() => requestDelete([entry])}
                />
              )) : <EmptyState icon={<Archive className="h-5 w-5" aria-hidden />}>
                {t("memory.imports.emptyArchived")}
              </EmptyState>}
            </div>
          </section>
        </div>;

  return (
    <div role="region" aria-label={t("memory.title")} className="flex min-h-0 flex-1 flex-col overflow-hidden bg-settings-canvas">
      <div className="min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
        <div
          data-settings-section="memory"
          data-main-navigation-expanded={mainNavigationExpanded}
          className={cn(
            "settings-grid settings-feature-page mx-auto w-full animate-in fade-in-0 slide-in-from-bottom-1 py-6 duration-200 ease-out motion-reduce:animate-none sm:py-8 lg:py-12",
            hostChromeInset && "pt-[4.25rem] sm:pt-[4.25rem] lg:pt-[4.75rem]",
          )}
        >
          <div className="settings-feature-header mb-5">
            <Button
              variant="ghost"
              size="sm"
              className="touch-target mb-4 gap-1 lg:hidden"
              onClick={onBackToChat}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden />
              {t("settings.backToChat")}
            </Button>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h1 className="text-[24px] font-normal leading-tight tracking-normal text-foreground sm:text-[28px]">
                  {t("memory.title")}
                </h1>
                <p className="mt-1.5 max-w-2xl text-[13px] leading-6 text-muted-foreground">
                  {t("memory.description")}
                </p>
              </div>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-9 w-9 shrink-0"
                aria-label={t("memory.refresh")}
                title={t("memory.refresh")}
                disabled={loading || refreshing}
                onClick={() => void loadMemory()}
              >
                <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} aria-hidden />
              </Button>
            </div>
            <SegmentedControl
              mode="tabs"
              value={tab}
              options={tabOptions}
              ariaLabel={t("memory.tabs.ariaLabel")}
              className="mt-4 w-full max-w-md"
              itemClassName="flex-1"
              onChange={setTab}
            />
          </div>

          {loadError && payload ? <div role="alert" className="mb-3 rounded-control border border-destructive/25 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {loadError}
          </div> : null}

          <div className="overflow-hidden rounded-panel border border-border/45 bg-settings-surface">
            {content}
          </div>
        </div>
      </div>

      <Dialog open={Boolean(preview)} onOpenChange={(open) => {
        if (!open) closePreview();
      }}>
        {preview ? (
          <DialogContent className="max-h-[calc(100dvh-2rem)] w-[min(calc(100vw-2rem),48rem)] gap-0 overflow-hidden p-0">
            <DialogHeader className="border-b border-border/45 px-5 py-4">
              <DialogTitle className="break-all pr-6 text-left">
                {t("memory.imports.previewTitle")}
              </DialogTitle>
              <DialogDescription className="break-all text-left">
                {preview.entry.name} · {formatMemoryBytes(preview.entry.bytes)}
              </DialogDescription>
            </DialogHeader>
            <div className="min-h-32 overflow-y-auto p-5">
              {preview.loading ? (
                <div className="flex min-h-32 items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  {t("memory.imports.previewLoading")}
                </div>
              ) : preview.error ? (
                <div className="flex min-h-32 flex-col items-center justify-center text-center text-sm">
                  <AlertCircle className="mb-2 h-5 w-5 text-destructive" aria-hidden />
                  <p className="text-destructive">{t("memory.imports.previewError")}</p>
                  <p className="mt-1 max-w-xl break-words text-xs text-muted-foreground">{preview.error}</p>
                </div>
              ) : (
                <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-6 text-foreground/90">
                  {preview.content}
                </pre>
              )}
            </div>
          </DialogContent>
        ) : null}
      </Dialog>

      <Dialog open={Boolean(deleteRequest)} onOpenChange={(open) => {
        if (!open && !deleting) setDeleteRequest(null);
      }}>
        <DialogContent className="w-[min(calc(100vw-2rem),28rem)]">
          <DialogHeader>
            <DialogTitle>{t("memory.imports.deleteConfirmTitle")}</DialogTitle>
            <DialogDescription>{t("memory.imports.deleteConfirmDescription")}</DialogDescription>
          </DialogHeader>
          {deleteRequest?.length ? <ul className="max-h-36 overflow-y-auto break-all text-xs text-muted-foreground">
            {deleteRequest.map((entry) => <li key={entry.id}>{entry.name}</li>)}
          </ul> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" disabled={deleting} onClick={() => setDeleteRequest(null)}>
              {t("settings.actions.cancel")}
            </Button>
            <Button type="button" variant="destructive" disabled={deleting} onClick={() => void confirmDelete()}>
              {deleting
                ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                : <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />}
              {t("memory.imports.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
