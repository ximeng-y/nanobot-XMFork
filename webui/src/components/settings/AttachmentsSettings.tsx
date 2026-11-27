import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CircleAlert, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteManagedAttachments,
  fetchManagedAttachments,
  previewManagedAttachmentDeletion,
  updateManagedAttachmentSettings,
  type WebUIMutationTransport,
} from "@/lib/api";
import { fmtDateTime } from "@/lib/format-time";
import type {
  AttachmentDeleteFailure,
  ManagedAttachment,
  ManagedAttachmentDeletePreview,
  ManagedAttachmentSettings,
  ManagedAttachmentsPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ToggleButton } from "@/components/ui/toggle-button";
import {
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
  SettingsStatusMessage,
} from "@/components/settings/shared/SettingsControls";

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;
const DEFAULT_ORPHAN_DAYS = 30;

type CapacityUnit = "MiB" | "GiB";

type CapacityParse =
  | { kind: "unlimited" }
  | { kind: "bytes"; bytes: number }
  | { kind: "invalid" };

function parseCapacity(value: string, unit: CapacityUnit): CapacityParse {
  const trimmed = value.trim();
  if (!trimmed) return { kind: "unlimited" };
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) return { kind: "invalid" };
  const numeric = Number(trimmed);
  const bytes = numeric * (unit === "GiB" ? GIB : MIB);
  if (!Number.isFinite(numeric) || numeric <= 0 || !Number.isSafeInteger(bytes)) {
    return { kind: "invalid" };
  }
  return { kind: "bytes", bytes };
}

function capacityInputForBytes(bytes: number | null): { value: string; unit: CapacityUnit } {
  if (bytes === null) return { value: "", unit: "GiB" };
  if (bytes % GIB === 0) return { value: String(bytes / GIB), unit: "GiB" };
  return { value: String(bytes / MIB), unit: "MiB" };
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const formatted = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: unit === 0 ? 0 : value < 10 ? 2 : 1,
  }).format(value);
  return `${formatted} ${units[unit]}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message.trim();
  return String(error);
}

function samePolicy(
  left: ManagedAttachmentSettings,
  right: ManagedAttachmentSettings,
): boolean {
  return left.max_bytes === right.max_bytes
    && left.auto_cleanup === right.auto_cleanup
    && left.orphan_days === right.orphan_days;
}

export function AttachmentsSettings({
  token,
  transport,
}: {
  token: string;
  transport: WebUIMutationTransport;
}) {
  const { t } = useTranslation();
  const mounted = useRef(true);
  const [payload, setPayload] = useState<ManagedAttachmentsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [capacityValue, setCapacityValue] = useState("");
  const [capacityUnit, setCapacityUnit] = useState<CapacityUnit>("GiB");
  const [autoCleanup, setAutoCleanup] = useState(false);
  const [orphanDays, setOrphanDays] = useState(String(DEFAULT_ORPHAN_DAYS));
  const [saving, setSaving] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ManagedAttachmentDeletePreview | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteFailures, setDeleteFailures] = useState<AttachmentDeleteFailure[]>([]);

  const hydrateSettings = useCallback((policy: ManagedAttachmentSettings) => {
    const capacity = capacityInputForBytes(policy.max_bytes);
    setCapacityValue(capacity.value);
    setCapacityUnit(capacity.unit);
    setAutoCleanup(policy.auto_cleanup);
    setOrphanDays(String(policy.orphan_days));
  }, []);

  const applyPayload = useCallback((
    next: ManagedAttachmentsPayload,
    hydratePolicy: boolean,
  ) => {
    setPayload(next);
    if (hydratePolicy) hydrateSettings(next.settings);
    const available = new Set(next.items.filter((item) => item.status !== "deleted").map((item) => item.id));
    setSelected((current) => new Set([...current].filter((id) => available.has(id))));
  }, [hydrateSettings]);

  const loadAttachments = useCallback(async (hydratePolicy: boolean) => {
    setLoading(true);
    setLoadError(null);
    try {
      const next = await fetchManagedAttachments(token);
      if (!mounted.current) return;
      applyPayload(next, hydratePolicy);
    } catch (error) {
      if (!mounted.current) return;
      setLoadError(errorMessage(error));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [applyPayload, token]);

  useEffect(() => {
    mounted.current = true;
    void loadAttachments(true);
    return () => {
      mounted.current = false;
    };
  }, [loadAttachments]);

  const capacity = parseCapacity(capacityValue, capacityUnit);
  const orphanDaysNumber = Number(orphanDays);
  const orphanDaysValid = /^\d+$/.test(orphanDays.trim())
    && Number.isSafeInteger(orphanDaysNumber)
    && orphanDaysNumber > 0;
  const draftPolicy = useMemo<ManagedAttachmentSettings | null>(() => {
    if (capacity.kind === "invalid" || !orphanDaysValid) return null;
    return {
      max_bytes: capacity.kind === "unlimited" ? null : capacity.bytes,
      auto_cleanup: autoCleanup,
      orphan_days: orphanDaysNumber,
    };
  }, [autoCleanup, capacity, orphanDaysNumber, orphanDaysValid]);
  const settingsDirty = Boolean(
    payload && draftPolicy && !samePolicy(payload.settings, draftPolicy),
  );
  const limitReached = Boolean(
    payload
    && payload.settings.max_bytes !== null
    && payload.usage.managed_bytes >= payload.settings.max_bytes,
  );
  const selectableItems = useMemo(
    () => (payload?.items ?? []).filter((item) => item.status !== "deleted"),
    [payload],
  );
  const selectedIds = useMemo(
    () => selectableItems.filter((item) => selected.has(item.id)).map((item) => item.id),
    [selectableItems, selected],
  );
  const allSelected = selectableItems.length > 0
    && selectableItems.every((item) => selected.has(item.id));

  const toggleSelected = (id: string, checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const toggleAll = (checked: boolean) => {
    setSelected(checked ? new Set(selectableItems.map((item) => item.id)) : new Set());
  };

  const saveSettings = async () => {
    if (!draftPolicy || !payload || !settingsDirty) return;
    setSaving(true);
    setSettingsNotice(null);
    setSettingsError(null);
    try {
      const next = await updateManagedAttachmentSettings(transport, draftPolicy);
      if (!mounted.current) return;
      applyPayload(next, true);
      setSettingsNotice(t("settings.attachments.saved"));
    } catch (error) {
      if (!mounted.current) return;
      setSettingsError(t("settings.attachments.saveFailed", {
        error: errorMessage(error),
      }));
    } finally {
      if (mounted.current) setSaving(false);
    }
  };

  const requestDeletePreview = async () => {
    if (!selectedIds.length) return;
    setPreviewLoading(true);
    setPreviewOpen(false);
    setPreview(null);
    setDeleteNotice(null);
    setDeleteError(null);
    setDeleteFailures([]);
    try {
      const next = await previewManagedAttachmentDeletion(transport, selectedIds);
      if (!mounted.current) return;
      setPreview(next);
      setPreviewOpen(true);
    } catch (error) {
      if (!mounted.current) return;
      setDeleteError(t("settings.attachments.previewFailed", {
        error: errorMessage(error),
      }));
    } finally {
      if (mounted.current) setPreviewLoading(false);
    }
  };

  const confirmDelete = async () => {
    if (!preview?.items.length) return;
    setDeleting(true);
    setDeleteError(null);
    setDeleteFailures([]);
    try {
      const result = await deleteManagedAttachments(
        transport,
        preview.items.map((item) => item.id),
      );
      if (!mounted.current) return;
      setPreviewOpen(false);
      setPreview(null);
      setDeleteFailures(result.failed);
      if (result.deleted.length) {
        setDeleteNotice(t("settings.attachments.deleteSuccess", {
          size: formatBytes(result.freed_bytes),
        }));
      }
      if (result.failed.length) {
        setDeleteError(t("settings.attachments.deleteFailed"));
      }
      await loadAttachments(false);
    } catch (error) {
      if (!mounted.current) return;
      setDeleteError(t("settings.attachments.deleteFailedWithReason", {
        error: errorMessage(error),
      }));
    } finally {
      if (mounted.current) setDeleting(false);
    }
  };

  const itemName = (id: string) => payload?.items.find((item) => item.id === id)?.name ?? id;
  const failureName = (failure: AttachmentDeleteFailure) => itemName(failure.id);

  return (
    <section className="settings-stack">
      <div>
        <SettingsSectionTitle>{t("settings.nav.attachments")}</SettingsSectionTitle>
        <p className="settings-list-inset mt-2 max-w-3xl text-ui leading-6 text-muted-foreground">
          {t("settings.attachments.description")}
        </p>
      </div>

      {loading && !payload ? (
        <SettingsGroup>
          <SettingsRow title={t("settings.attachments.loading")}>
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
          </SettingsRow>
        </SettingsGroup>
      ) : !payload ? (
        <SettingsGroup>
          <SettingsRow title={t("settings.status.loadError")}>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="text-sm text-muted-foreground">{loadError}</span>
              <Button type="button" size="sm" variant="outline" onClick={() => void loadAttachments(true)}>
                {t("common.retry")}
              </Button>
            </div>
          </SettingsRow>
        </SettingsGroup>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <UsageCard label={t("settings.attachments.managed")} value={formatBytes(payload.usage.managed_bytes)} />
            <UsageCard label={t("settings.attachments.pending")} value={formatBytes(payload.usage.pending_bytes)} />
            <UsageCard label={t("settings.attachments.protected")} value={formatBytes(payload.usage.protected_bytes)} />
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 settings-list-inset text-xs text-muted-foreground">
            <span>{t("settings.attachments.limit", {
              limit: payload.settings.max_bytes === null
                ? t("settings.attachments.unlimited")
                : formatBytes(payload.settings.max_bytes),
            })}</span>
            {limitReached ? (
              <span role="alert" className="inline-flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-300">
                <CircleAlert className="h-3.5 w-3.5" aria-hidden />
                {t("settings.attachments.overLimit")}
              </span>
            ) : null}
          </div>

          <section className="settings-stack">
            <SettingsSectionTitle>{t("settings.attachments.policyTitle")}</SettingsSectionTitle>
            <SettingsGroup>
              <SettingsRow
                title={t("settings.attachments.capacityTitle")}
                description={t("settings.attachments.capacityDescription")}
              >
                <div className="flex w-full min-w-0 items-center gap-2 sm:w-[19rem]">
                  <Input
                    type="number"
                    min={0}
                    step="any"
                    inputMode="decimal"
                    value={capacityValue}
                    aria-label={t("settings.attachments.capacityTitle")}
                    aria-invalid={capacity.kind === "invalid"}
                    placeholder={t("settings.attachments.unlimited")}
                    onChange={(event) => {
                      setCapacityValue(event.target.value);
                      setSettingsNotice(null);
                    }}
                    className="min-w-0 flex-1"
                  />
                  <select
                    value={capacityUnit}
                    aria-label="MiB / GiB"
                    onChange={(event) => {
                      setCapacityUnit(event.target.value as CapacityUnit);
                      setSettingsNotice(null);
                    }}
                    className="h-9 shrink-0 rounded-control border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    <option value="MiB">MiB</option>
                    <option value="GiB">GiB</option>
                  </select>
                </div>
              </SettingsRow>
              <SettingsRow
                title={t("settings.attachments.autoCleanupTitle")}
                description={t("settings.attachments.autoCleanupDescription")}
              >
                <ToggleButton
                  checked={autoCleanup}
                  label={t("settings.attachments.autoCleanupTitle")}
                  onChange={(checked) => {
                    setAutoCleanup(checked);
                    setSettingsNotice(null);
                  }}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.attachments.orphanDaysTitle")}
                description={t("settings.attachments.orphanDaysDescription")}
              >
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    min={1}
                    step={1}
                    value={orphanDays}
                    aria-label={t("settings.attachments.orphanDaysTitle")}
                    aria-invalid={!orphanDaysValid}
                    onChange={(event) => {
                      setOrphanDays(event.target.value);
                      setSettingsNotice(null);
                    }}
                    className="w-24 text-right tabular-nums"
                  />
                  <span className="text-sm text-muted-foreground">{t("settings.attachments.days")}</span>
                </div>
              </SettingsRow>
            </SettingsGroup>
            {(capacity.kind === "invalid" || !orphanDaysValid) ? (
              <p role="alert" className="settings-list-inset text-xs text-destructive">
                {capacity.kind === "invalid"
                  ? t("settings.attachments.capacityInvalid")
                  : t("settings.attachments.orphanDaysInvalid")}
              </p>
            ) : null}
            <div className="flex min-h-10 flex-wrap items-center justify-between gap-3">
              <div role={settingsError ? "alert" : "status"} className="min-w-0 text-xs leading-5 text-muted-foreground">
                <SettingsStatusMessage tone={settingsError ? "danger" : settingsNotice ? "accent" : undefined}>
                  {settingsError ?? settingsNotice}
                </SettingsStatusMessage>
              </div>
              <Button
                type="button"
                size="sm"
                onClick={() => void saveSettings()}
                disabled={!settingsDirty || !draftPolicy || saving || deleting}
              >
                {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {saving ? t("settings.actions.saving") : t("settings.actions.save")}
              </Button>
            </div>
          </section>

          <section className="settings-stack">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <SettingsSectionTitle>{t("settings.attachments.listTitle")}</SettingsSectionTitle>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={loading || deleting || previewLoading}
                onClick={() => void loadAttachments(false)}
              >
                <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
                {t("settings.attachments.refresh")}
              </Button>
            </div>
            {loadError ? (
              <div role="alert" className="settings-list-inset text-xs text-destructive">
                {t("settings.attachments.refreshFailed", { error: loadError })}
              </div>
            ) : null}
            {selectableItems.length ? (
              <div className="settings-list-inset flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <label className="inline-flex min-h-9 items-center gap-2">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={(event) => toggleAll(event.target.checked)}
                    className="size-4 rounded border-border accent-foreground"
                  />
                  {t("settings.attachments.selectAll")}
                </label>
                {selectedIds.length ? (
                  <span>{t("settings.attachments.selectedCount", { count: selectedIds.length })}</span>
                ) : null}
              </div>
            ) : null}
            <SettingsGroup>
              {payload.items.length ? payload.items.map((item) => (
                <AttachmentRow
                  key={item.id}
                  item={item}
                  checked={selected.has(item.id)}
                  disabled={item.status === "deleted"}
                  onCheckedChange={(checked) => toggleSelected(item.id, checked)}
                />
              )) : (
                <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                  {t("settings.attachments.empty")}
                </div>
              )}
            </SettingsGroup>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0 text-xs leading-5 text-muted-foreground">
                {deleteNotice ? (
                  <div role="status">
                    <SettingsStatusMessage tone="accent">{deleteNotice}</SettingsStatusMessage>
                  </div>
                ) : null}
                {deleteError ? (
                  <div role="alert">
                    <SettingsStatusMessage tone="danger">{deleteError}</SettingsStatusMessage>
                  </div>
                ) : null}
                {deleteFailures.length ? (
                  <ul className="mt-1 space-y-1 text-destructive">
                    {deleteFailures.map((failure) => (
                      <li key={failure.id}>
                        {t("settings.attachments.failureItem", {
                          name: failureName(failure),
                          reason: failure.reason,
                        })}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => void requestDeletePreview()}
                disabled={!selectedIds.length || previewLoading || deleting}
              >
                {previewLoading ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                )}
                {previewLoading ? t("settings.attachments.preparing") : t("settings.attachments.deleteSelected")}
              </Button>
            </div>
          </section>
        </>
      )}

      <Dialog
        open={previewOpen}
        onOpenChange={(open) => {
          if (!deleting) setPreviewOpen(open);
        }}
      >
        {preview ? (
          <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-[36rem] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{t("settings.attachments.previewTitle")}</DialogTitle>
              <DialogDescription>{t("settings.attachments.previewWarning")}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 text-sm">
              <p className="font-medium text-foreground">
                {t("settings.attachments.previewEstimated", {
                  size: formatBytes(preview.estimated_freed_bytes),
                })}
              </p>
              {preview.affected_sessions.length ? (
                <PreviewSection title={t("settings.attachments.previewSessions")}>
                  <div className="flex flex-wrap gap-1.5">
                    {preview.affected_sessions.map((session) => (
                      <span key={session.key} className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">
                        {session.title}
                      </span>
                    ))}
                  </div>
                </PreviewSection>
              ) : null}
              <PreviewSection title={t("settings.attachments.previewFiles")}>
                <ul className="max-h-40 space-y-1 overflow-y-auto break-all text-xs text-muted-foreground">
                  {preview.items.flatMap((item) => item.files.map((file) => (
                    <li key={`${item.id}:${file}`}>{file}</li>
                  )))}
                </ul>
              </PreviewSection>
              {preview.blocked.length ? (
                <PreviewSection title={t("settings.attachments.previewBlocked")}>
                  <ul className="space-y-1 text-xs text-destructive">
                    {preview.blocked.map((blocked) => (
                      <li key={blocked.id}>
                        {t("settings.attachments.failureItem", {
                          name: failureName(blocked),
                          reason: blocked.reason,
                        })}
                      </li>
                    ))}
                  </ul>
                </PreviewSection>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                disabled={deleting}
                onClick={() => setPreviewOpen(false)}
              >
                {t("settings.actions.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={deleting || !preview.items.length}
                onClick={() => void confirmDelete()}
              >
                {deleting ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
                {deleting ? t("settings.actions.deleting") : t("settings.attachments.confirmDelete")}
              </Button>
            </DialogFooter>
          </DialogContent>
        ) : null}
      </Dialog>
    </section>
  );
}

function UsageCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-panel bg-settings-surface px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-medium tabular-nums text-foreground">{value}</div>
    </div>
  );
}

function AttachmentRow({
  item,
  checked,
  disabled,
  onCheckedChange,
}: {
  item: ManagedAttachment;
  checked: boolean;
  disabled: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <div className="flex items-start gap-3 border-b border-border/40 px-4 py-3 last:border-b-0">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={t("settings.attachments.selectItem", { name: item.name })}
        onChange={(event) => onCheckedChange(event.target.checked)}
        className="mt-1 size-4 shrink-0 rounded border-border accent-foreground disabled:opacity-50"
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="min-w-0 break-all font-medium text-foreground">{item.name}</span>
          <span className="text-xs tabular-nums text-muted-foreground">{formatBytes(item.bytes)}</span>
          {item.status === "missing" ? (
            <span className="text-xs font-medium text-amber-700 dark:text-amber-300">
              {t("settings.attachments.missing")}
            </span>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{t("settings.attachments.createdAt", { date: fmtDateTime(item.created_at, i18n.language) })}</span>
          {item.unreferenced_since ? (
            <span>
              {t("settings.attachments.unreferencedSince", {
                date: fmtDateTime(item.unreferenced_since, i18n.language),
              })}
            </span>
          ) : null}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">{t("settings.attachments.references")}</span>
          {item.references.length ? item.references.map((reference) => (
            <span key={reference.key} title={reference.key} className="max-w-full rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
              <span className="block max-w-60 truncate">{reference.title}</span>
            </span>
          )) : (
            <span className="text-muted-foreground">{t("settings.attachments.noReferences")}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function PreviewSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-medium text-foreground">{title}</h3>
      <div className="rounded-control bg-muted/35 p-3">{children}</div>
    </section>
  );
}
