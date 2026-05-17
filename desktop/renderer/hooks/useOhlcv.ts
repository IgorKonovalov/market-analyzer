/**
 * Ad-hoc fetch hook for `GET /ohlcv`. No React Query dep yet — Plan 0001 phase 5
 * picked the small-hook option to avoid an architect-scope dep-add.
 *
 * Re-fetches whenever `symbol`, `timeframe`, `start`, or `end` change, or when
 * `refetchToken` is bumped. Cancels stale responses on unmount and on re-trigger
 * so a slow request followed by a fast one never overwrites with stale data.
 */
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { Bar } from "../types/sidecar/bar";

export interface UseOhlcvParams {
  symbol: string;
  timeframe: string;
  start: Date;
  end: Date;
}

export interface UseOhlcvResult {
  bars: Bar[] | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useOhlcv({ symbol, timeframe, start, end }: UseOhlcvParams): UseOhlcvResult {
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [refetchToken, setRefetchToken] = useState(0);

  const refetch = useCallback(() => {
    setRefetchToken((n) => n + 1);
  }, []);

  const startMs = start.getTime();
  const endMs = end.getTime();

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);

    api
      .getOhlcv({ symbol, timeframe, start: new Date(startMs), end: new Date(endMs) })
      .then((result) => {
        if (cancelled) return;
        setBars(result);
        setIsLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
        setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, startMs, endMs, refetchToken]);

  return { bars, isLoading, error, refetch };
}
