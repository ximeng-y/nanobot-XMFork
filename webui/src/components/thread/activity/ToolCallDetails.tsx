import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type { ToolProgressEvent } from "@/lib/types";

import { redactActivityText } from "./activity-text";

interface ToolCallDetailsProps {
  event?: ToolProgressEvent;
  callIndex?: number;
  callCount?: number;
  className?: string;
}

interface DetailSection {
  key: "arguments" | "result" | "error";
  label: string;
  value: string;
  error?: boolean;
}

export function ToolCallDetails({
  event,
  callIndex = 1,
  callCount = 1,
  className,
}: ToolCallDetailsProps) {
  const { t } = useTranslation();
  const detailsId = useId();
  const [expanded, setExpanded] = useState(false);
  const sections = useMemo(
    () => (expanded && event ? toolDetailSections(event, t) : []),
    [event, expanded, t],
  );
  if (!event || !hasToolDetail(event)) return null;

  const label = callCount > 1
    ? t("message.toolCallDetails", { index: callIndex, defaultValue: "Call {{index}} details" })
    : t("message.toolDetails", { defaultValue: "Details" });

  return (
    <div className={cn("ms-[1.625rem] min-w-0", className)} data-tool-call-details>
      <button
        type="button"
        data-tool-call-details-toggle
        data-testid="tool-call-details-toggle"
        className="touch-target inline-flex min-h-7 items-center gap-1 rounded-sm text-[12px] text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-expanded={expanded}
        aria-controls={detailsId}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded
          ? <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
          : <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />}
        <span>{label}</span>
      </button>
      {expanded ? (
        <div
          id={detailsId}
          data-testid="tool-call-details"
          className="mt-1 max-h-[min(16rem,50vh)] overflow-y-auto overscroll-contain rounded-md border border-border/70 bg-muted/20 px-2.5 py-2"
        >
          <div className="flex min-w-0 flex-col gap-2">
            {sections.map((section) => (
              <section key={section.key} data-tool-detail-section={section.key}>
                <div className={cn(
                  "mb-0.5 text-[11px] font-medium text-muted-foreground",
                  section.error && "text-destructive",
                )}>
                  {section.label}
                </div>
                <pre className={cn(
                  "m-0 min-w-0 whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-foreground/85",
                  section.error && "text-destructive",
                )}>
                  {section.value}
                </pre>
              </section>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function hasToolDetail(event: ToolProgressEvent): boolean {
  return hasOwn(event, "arguments")
    || hasOwn(event, "result")
    || hasOwn(event, "error")
    || (typeof event.error_code === "string" && event.error_code.length > 0)
    || hasFunctionArguments(event);
}

function toolDetailSections(
  event: ToolProgressEvent,
  t: ReturnType<typeof useTranslation>["t"],
): DetailSection[] {
  const sections: DetailSection[] = [];
  if (hasOwn(event, "arguments") || hasFunctionArguments(event)) {
    sections.push({
      key: "arguments",
      label: t("message.toolArguments", { defaultValue: "Arguments" }),
      value: formatToolDetailValue(toolEventArguments(event)),
    });
  }
  if (hasOwn(event, "result")) {
    sections.push({
      key: "result",
      label: t("message.toolResult", { defaultValue: "Result" }),
      value: formatToolDetailValue(event.result),
    });
  }
  if (hasOwn(event, "error") || event.error_code) {
    const error = hasOwn(event, "error") ? event.error : undefined;
    const value = error !== undefined && event.error_code
      ? { error, error_code: event.error_code }
      : error !== undefined
        ? error
        : event.error_code;
    sections.push({
      key: "error",
      label: t("message.toolError", { defaultValue: "Error" }),
      value: formatToolDetailValue(value),
      error: true,
    });
  }
  return sections;
}

function toolEventArguments(event: ToolProgressEvent): unknown {
  const functionArguments = (event as { function?: { arguments?: unknown } }).function?.arguments;
  return functionArguments ?? event.arguments;
}

function hasFunctionArguments(event: ToolProgressEvent): boolean {
  const functionRecord = (event as { function?: { arguments?: unknown } }).function;
  return !!functionRecord
    && Object.prototype.hasOwnProperty.call(functionRecord, "arguments");
}

function hasOwn(value: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function formatToolDetailValue(value: unknown): string {
  if (typeof value === "string") return redactActivityText(value);
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  if (typeof value === "boolean" || typeof value === "number" || typeof value === "bigint") {
    return String(value);
  }
  try {
    return JSON.stringify(
      sanitizeToolDetailValue(value, new WeakSet<object>()),
      null,
      2,
    ) ?? String(value);
  } catch {
    return redactActivityText(String(value));
  }
}

function sanitizeToolDetailValue(value: unknown, seen: WeakSet<object>): unknown {
  if (typeof value === "string") return redactActivityText(value);
  if (value === null || typeof value !== "object") return value;
  if (seen.has(value)) return "[Circular]";
  seen.add(value);
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeToolDetailValue(item, seen));
  }
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    result[key] = isSensitiveToolDetailKey(key)
      ? "<redacted>"
      : sanitizeToolDetailValue(item, seen);
  }
  return result;
}

function isSensitiveToolDetailKey(key: string): boolean {
  const compact = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
  return compact === "token"
    || compact === "secret"
    || compact === "password"
    || compact === "passwd"
    || compact === "authorization"
    || compact === "cookie"
    || compact === "setcookie"
    || compact === "credential"
    || compact === "credentials"
    || compact.endsWith("apikey")
    || compact.endsWith("accesstoken")
    || compact.endsWith("refreshtoken")
    || compact.endsWith("idtoken")
    || compact.endsWith("clientsecret")
    || compact.endsWith("password");
}
