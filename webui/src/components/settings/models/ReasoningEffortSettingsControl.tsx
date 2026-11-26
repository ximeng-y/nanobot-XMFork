import { useTranslation } from "react-i18next";

import { ReasoningEffortPicker } from "@/components/settings/models/ReasoningEffortPicker";
import { ToggleButton } from "@/components/ui/toggle-button";
import { useProviderModelCatalog } from "@/hooks/useProviderModelCatalog";
import { orderReasoningEfforts, reasoningEffortValues } from "@/lib/provider-model-catalog";
import type { SettingsPayload } from "@/lib/types";

const NO_PROVIDER = {};
const FIXED_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"];

export interface ReasoningEffortSettingsDraft {
  reasoningEffort: string;
  allowedReasoningEfforts: string[] | null;
}

export function ReasoningEffortSettingsControl({
  token,
  provider,
  model,
  savedValues,
  savedAllowedReasoningEfforts,
  value,
  disabled,
  onSelect,
  onChange,
}: {
  token: string;
  provider: SettingsPayload["providers"][number] | undefined;
  model: string;
  savedValues?: string[];
  savedAllowedReasoningEfforts?: string[] | null;
  value: ReasoningEffortSettingsDraft;
  disabled?: boolean;
  onSelect?: () => void;
  onChange: (value: ReasoningEffortSettingsDraft) => void;
}) {
  const { t } = useTranslation();
  const allowed = value.allowedReasoningEfforts;
  const manual = allowed !== null;
  const canLoadCatalog = manual && !!model && !!provider?.configured
    && provider.model_catalog !== "unsupported";
  const { payload } = useProviderModelCatalog({
    revision: provider ?? NO_PROVIDER,
    token,
    provider: provider?.name ?? "",
    enabled: canLoadCatalog,
  });
  const savedExtras = savedAllowedReasoningEfforts
    ?.filter((effort) => !FIXED_EFFORTS.includes(effort)) ?? [];
  const draftExtras = allowed?.filter((effort) => !FIXED_EFFORTS.includes(effort)) ?? [];
  const efforts = orderReasoningEfforts([
    ...FIXED_EFFORTS,
    ...savedExtras,
    ...draftExtras,
    ...reasoningEffortValues(payload, model, savedValues),
  ]);
  const effortLabel = (effort: string) => effort === "none"
    ? t("settings.models.reasoningEffortOff")
    : effort;

  const toggleManual = (next: boolean) => {
    onSelect?.();
    onChange({
      ...value,
      allowedReasoningEfforts: next
        ? [value.reasoningEffort || "high"]
        : null,
    });
  };
  const toggleEffort = (effort: string, checked: boolean) => {
    onSelect?.();
    const nextAllowed = checked
      ? orderReasoningEfforts([...(allowed ?? []), effort])
      : (allowed ?? []).filter((candidate) => candidate !== effort);
    onChange({
      reasoningEffort: checked || value.reasoningEffort !== effort
        ? value.reasoningEffort
        : "",
      allowedReasoningEfforts: nextAllowed,
    });
  };

  return (
    <div className="w-full space-y-3">
      <div className="flex items-center justify-end gap-2 text-[12px] text-muted-foreground">
        <span>{t("settings.runtimeConfig.manual")}</span>
        <ToggleButton
          checked={manual}
          disabled={disabled}
          label={t("settings.runtimeConfig.manual")}
          onChange={toggleManual}
        />
      </div>
      {manual ? (
        <>
          <div
            role="group"
            aria-label={t("settings.models.reasoningEffortAllowed")}
            className="grid grid-cols-2 gap-1.5"
          >
            {efforts.map((effort) => (
              <label
                key={effort}
                className="flex min-h-8 cursor-pointer items-center gap-2 rounded-control px-2 text-[13px] text-foreground settings-hover"
              >
                <input
                  type="checkbox"
                  checked={(allowed ?? []).includes(effort)}
                  disabled={disabled}
                  onChange={(event) => toggleEffort(effort, event.currentTarget.checked)}
                  className="size-4 accent-[hsl(var(--foreground))]"
                />
                <span className="min-w-0 truncate">{effortLabel(effort)}</span>
              </label>
            ))}
          </div>
          <label className="flex items-center justify-between gap-3 text-[13px] text-foreground">
            <span>{t("settings.models.reasoningEffortDefault")}</span>
            <select
              value={value.reasoningEffort || ""}
              disabled={disabled}
              onChange={(event) => {
                onSelect?.();
                onChange({ ...value, reasoningEffort: event.currentTarget.value });
              }}
              className="h-9 min-w-0 rounded-control border border-border/60 bg-background px-2 text-[13px] text-foreground"
            >
              <option value="">{t("settings.models.reasoningEffortProviderDefault")}</option>
              {(allowed ?? []).map((effort) => (
                <option key={effort} value={effort}>{effortLabel(effort)}</option>
              ))}
            </select>
          </label>
        </>
      ) : (
        <ReasoningEffortPicker
          token={token}
          provider={provider}
          model={model}
          savedValues={savedValues}
          value={value.reasoningEffort}
          onChange={(reasoningEffort) => onChange({ ...value, reasoningEffort })}
          onSelect={onSelect}
        />
      )}
    </div>
  );
}
