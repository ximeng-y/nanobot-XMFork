import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, readForkUpdate, startForkUpdate, type ForkUpdateTarget, type WebUIMutationTransport } from "@/lib/api";

type Pending = ForkUpdateTarget & { version: string; deadline: number };
const key = () => `nanobot.fork-update:${window.location.pathname}`;
function remembered(): Pending | null {
  try {
    const p = JSON.parse(sessionStorage.getItem(key()) || "null") as Pending | null;
    return p && p.deadline > Date.now() && typeof p.commit === "string" && typeof p.tag === "string" && typeof p.version === "string" ? p : null;
  } catch { return null; }
}
function remember(p: Pending | null) {
  try {
    if (p) sessionStorage.setItem(key(), JSON.stringify(p));
    else sessionStorage.removeItem(key());
  } catch { /* The current tab can still follow the gateway in memory. */ }
}

export function useForkUpdate(client: WebUIMutationTransport | undefined, token: string) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(remembered);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  useEffect(() => {
    if (!pending || !token || !client) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    function finish(message: string) {
      remember(null);
      setPending(null);
      setError(message);
    }
    async function poll(p: Pending) {
      if (submitting.current) {
        timer = setTimeout(() => void poll(p), 2_000);
        return;
      }
      const state = await readForkUpdate(token).catch(() => null);
      if (!active) return;
      if (state?.last?.tag === p.tag && state.last.commit === p.commit) {
        if (state.last.phase === "restarting" && state.runningVersion === p.version) {
          finish("");
          window.location.reload();
          return;
        }
        if (state.last.phase === "failed") { finish(t("updates.failed")); return; }
      }
      if (Date.now() >= p.deadline) { finish(t("updates.delayed")); return; }
      timer = setTimeout(() => void poll(p), 2_000);
    }
    timer = setTimeout(() => void poll(pending), 2_000);
    return () => { active = false; clearTimeout(timer); };
  }, [pending, client, token, t]);

  async function apply(target: ForkUpdateTarget, version: string, revision: number) {
    if (!client || submitting.current || pending) return;
    submitting.current = true;
    setError("");
    const intent = { ...target, version, deadline: Date.now() + 15 * 60_000 };
    remember(intent);
    setPending(intent);
    try {
      await startForkUpdate(client, target, revision);
    } catch (reason) {
      // An absent acknowledgement is not a refusal: keep following the exact request on reconnect.
      if (reason instanceof ApiError && reason.status < 500) {
        remember(null);
        setError(reason.message);
        setPending(null);
      }
    } finally { submitting.current = false; }
  }
  return { busy: !!pending, error, apply };
}
