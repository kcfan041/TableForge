import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { DomainEvent } from "./api";

interface Live {
  connected: boolean;
  /** Increments whenever a new domain event arrives; pages refetch when it changes. */
  tick: number;
  latest: DomainEvent[];
}

const LiveContext = createContext<Live>({ connected: false, tick: 0, latest: [] });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [tick, setTick] = useState(0);
  const [latest, setLatest] = useState<DomainEvent[]>([]);
  const lastSequence = useRef(-1);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: number | undefined;
    let closed = false;

    const open = () => {
      const after = lastSequence.current;
      source = new EventSource(`/api/events/stream?after=${after}`, { withCredentials: true });
      source.onopen = () => setConnected(true);
      source.addEventListener("domain", (message) => {
        const event = JSON.parse((message as MessageEvent<string>).data) as DomainEvent;
        lastSequence.current = Math.max(lastSequence.current, event.sequence);
        setLatest((previous) => [event, ...previous].slice(0, 50));
        setTick((value) => value + 1);
      });
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry = window.setTimeout(open, 3000);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      source?.close();
    };
  }, []);

  return <LiveContext.Provider value={{ connected, tick, latest }}>{children}</LiveContext.Provider>;
}

export function useLive(): Live {
  return useContext(LiveContext);
}

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Loads data and reloads it (debounced) whenever a live event arrives. */
export function useLiveData<T>(load: () => Promise<T>, deps: unknown[] = []): Loaded<T> {
  const { tick } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [manual, setManual] = useState(0);
  const loader = useRef(load);
  loader.current = load;

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(
      () => {
        loader
          .current()
          .then((value) => {
            if (cancelled) return;
            setData(value);
            setError(null);
          })
          .catch((reason: unknown) => {
            if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
      },
      data === null ? 0 : 400,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [tick, manual, ...deps]);

  const reload = useCallback(() => setManual((value) => value + 1), []);
  return { data, error, loading, reload };
}
