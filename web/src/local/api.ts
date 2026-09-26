export interface CheckResult {
  name: string;
  ok: boolean;
  detail: string;
  hint: string;
  seconds: number;
}

export interface DoctorReport {
  ok: boolean;
  target: string;
  checks: CheckResult[];
}

export interface WorkerView {
  worker: number;
  pid: number;
  running: boolean;
  state: string;
  jobs: number;
  started_at: string;
  stopped_at: string | null;
  exit_code: number | null;
  last_error: string;
}

export interface LocalStatus {
  version: string;
  data_dir: string;
  joined: boolean;
  node: {
    coordinator: string;
    cluster_id: string;
    storage_endpoint: string;
    session_expires_at: string | null;
  } | null;
  operator: { cluster_id: string; role: string; address: string } | null;
  hosting: HostingStatus;
  use_gpu: boolean;
  workers: WorkerView[];
}

export interface HostingStatus {
  hosted: boolean;
  running?: boolean;
  starting?: boolean;
  address?: string;
  pid?: number | null;
  started_at?: string;
  log_file?: string;
}

export interface LogLine {
  timestamp?: string;
  event?: string;
  message?: string;
  [key: string]: unknown;
}

const token =
  document.querySelector<HTMLMetaElement>('meta[name="tableforge-ui-token"]')?.content ?? "";

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-TableForge-UI": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : response.statusText;
    throw new Error(detail);
  }
  return data as T;
}

export const local = {
  status: () => call<LocalStatus>("GET", "/api/status"),
  setup: () => call<{ tailscale_ip: string | null }>("GET", "/api/setup"),
  createCluster: (network: "tailscale" | "local") =>
    call<{ cluster_id: string; address: string; status: LocalStatus }>("POST", "/api/cluster", {
      network,
    }),
  startService: () => call<LocalStatus>("POST", "/api/cluster/service/start"),
  stopService: () => call<LocalStatus>("POST", "/api/cluster/service/stop"),
  join: (invite: string) =>
    call<{ report: Record<string, unknown>; status: LocalStatus }>("POST", "/api/join", { invite }),
  doctor: () => call<DoctorReport>("POST", "/api/doctor"),
  startWorker: () => call<WorkerView>("POST", "/api/workers"),
  stopWorker: (worker: number) => call<WorkerView>("POST", `/api/workers/${worker}/stop`),
  log: (worker: number) =>
    call<{ worker: WorkerView; lines: LogLine[] }>("GET", `/api/workers/${worker}/log`),
};
