/**
 * Phase 4 renderer: single route that pings the main process and the sidecar.
 *
 * The chart UI (phase 5) is owned by `ui-builder`. This component is the
 * placeholder that proves the shell↔sidecar↔main channels work end-to-end.
 */
import { useEffect, useState } from "react";
import { sidecarFetch } from "./api/client";

interface Status {
  app: string | null;
  sidecar: "loading" | "ok" | "error";
  detail?: string;
}

export function App(): JSX.Element {
  const [status, setStatus] = useState<Status>({ app: null, sidecar: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const info = await window.api.app.getInfo();
        if (cancelled) return;
        setStatus((s) => ({ ...s, app: info.version }));

        const healthz = await sidecarFetch("/healthz");
        if (cancelled) return;
        if (healthz.ok) {
          setStatus((s) => ({ ...s, sidecar: "ok" }));
        } else {
          setStatus((s) => ({
            ...s,
            sidecar: "error",
            detail: `healthz ${healthz.status}`,
          }));
        }
      } catch (err) {
        if (!cancelled) {
          setStatus((s) => ({
            ...s,
            sidecar: "error",
            detail: (err as Error).message,
          }));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h1>market-analyser</h1>
      <p>app version: {status.app ?? "…"}</p>
      <p>
        sidecar:{" "}
        {status.sidecar === "ok"
          ? "ok"
          : status.sidecar === "loading"
            ? "…"
            : `error — ${status.detail ?? "unknown"}`}
      </p>
      <p style={{ color: "#666", fontSize: 12 }}>
        Phase 5 (`ui-builder`) will replace this view with a candlestick chart.
      </p>
    </main>
  );
}
