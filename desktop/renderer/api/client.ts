/**
 * Renderer-side fetch wrapper. Reads `{port, secretToken}` from the preload
 * bridge on first call and injects `Authorization: Bearer <secret>` plus the
 * sidecar base URL. The secret is held in a module-level closure — never
 * passed back across IPC, never written to disk, never logged.
 */
import type { CandlestickData, UTCTimestamp } from "lightweight-charts";

import type { SidecarPort } from "../../shared/schemas/sidecar";
import type { Bar } from "../types/sidecar/bar";

let cached: SidecarPort | null = null;

async function getSidecarConfig(): Promise<SidecarPort> {
  if (cached) return cached;
  cached = await window.api.sidecar.getPort();
  return cached;
}

export async function sidecarFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { port, secretToken } = await getSidecarConfig();
  const url = `http://127.0.0.1:${port}${path}`;
  const headers = new Headers(init.headers);
  if (path !== "/healthz") {
    headers.set("Authorization", `Bearer ${secretToken}`);
  }
  return fetch(url, { ...init, headers });
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(status: number, body: string) {
    super(`sidecar ${status}: ${body || "(empty body)"}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function callJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await sidecarFetch(path, init);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  return (await res.json()) as T;
}

export interface GetOhlcvParams {
  symbol: string;
  timeframe: string;
  start: Date;
  end: Date;
}

export const api = {
  getOhlcv({ symbol, timeframe, start, end }: GetOhlcvParams): Promise<Bar[]> {
    const params = new URLSearchParams({
      symbol,
      timeframe,
      start: start.toISOString(),
      end: end.toISOString(),
    });
    return callJson<Bar[]>(`/ohlcv?${params.toString()}`);
  },
} as const;

export function toLightweightBar(b: Bar): CandlestickData {
  return {
    time: Math.floor(new Date(b.event_ts).getTime() / 1000) as UTCTimestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  };
}
