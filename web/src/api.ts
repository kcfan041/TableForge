export type Role = "viewer" | "operator" | "admin";

export interface Session {
  operator_id: string;
  role: Role;
  label: string;
  cluster_id: string;
  csrf_token: string | null;
}

export interface Overview {
  cluster_id: string;
  version: string;
  nodes: { total: number; online: number };
  gpus: number;
  experiments: Record<string, number>;
  jobs: Record<string, number>;
  latest_event: number;
}

export interface Gpu {
  device_id: string;
  model: string;
  total_memory_bytes: number | null;
  available_memory_bytes: number | null;
  driver_version: string | null;
  compute_capability: string | null;
}

export interface NodeView {
  node_id: string;
  online: boolean;
  last_heartbeat_at: string;
  registered_at: string;
  operating_system: string;
  architecture: string;
  logical_cpu_cores: number | null;
  total_memory_bytes: number | null;
  available_memory_bytes: number | null;
  roles: string[];
  runtimes: string[];
  python_version: string;
  capability_revision: number;
  warnings: string[];
  gpus: Gpu[];
  workers: { worker_id: string; label: string; last_seen_at: string }[];
}

export interface Preset {
  key: string;
  version: string;
  kind: string;
  hands: number;
  hands_per_job: number;
  players: number;
  rounds: number;
  league: boolean;
  learner_runtime: string;
}

export interface ExperimentSummary {
  experiment_id: string;
  preset: string;
  kind: string;
  players: number;
  status: string;
  revision: number;
  created_at: string;
  updated_at: string;
  job_counts: Record<string, number>;
  league: boolean;
}

export interface JobView {
  job_id: string;
  type: string;
  payload_type: string;
  status: string;
  created_at: string;
}

export interface ExperimentDetail extends ExperimentSummary {
  config: Record<string, unknown>;
  jobs: JobView[];
  evaluation: Record<string, unknown> | null;
  results: {
    hands: number;
    actions: number;
    showdowns: number;
    folds: number;
    winnings: number[];
  } | null;
}

export interface MatrixCell {
  bb_per_100: number;
  confidence_low: number;
  confidence_high: number;
  hands: number;
}

export interface Matrix {
  available: boolean;
  reason?: string;
  players?: string[];
  cells?: Record<string, Record<string, MatrixCell>>;
}

export interface CheckpointView {
  checkpoint_id: string;
  agent_id: string;
  experiment_id: string;
  round_index: number;
  status: string;
  decision_reason: string;
  parent_id: string | null;
  sha256: string;
  created_at: string;
  updated_at: string;
}

export interface DomainEvent {
  sequence: number;
  type: string;
  aggregate_type: string;
  aggregate_id: string;
  version: number;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface Diagnostics {
  version: string;
  protocol: number;
  min_protocol: number;
  schema_version: number;
  metadata_backend: string;
  artifact_backend: string;
  direct_transfer: boolean;
  grpc_listen: string;
  http_listen: string;
  node_ttl_seconds: number;
  certificate: { present: boolean; hosts?: string[]; fingerprint?: string };
  unpublished_events: number;
  advertised_address?: string;
}

export interface InviteView {
  invite_id: string;
  scope: string;
  label: string;
  created_at: string;
  expires_at: string;
  redeemed_at: string | null;
  redeemed_by: string | null;
  state: "open" | "used" | "expired" | "revoked";
}

export interface CreatedInvite {
  invite_id: string;
  invite: string;
  scope: string;
  expires_at: string;
  warnings: string[];
}

export interface OperatorView {
  operator_id: string;
  role: Role;
  label: string;
  created_at: string;
  expires_at: string;
  current: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;
let onUnauthorized: () => void = () => undefined;

// The same pages run in two places: served by the coordinator ("coordinator" mode, cookie
// session) or inside `tableforge ui` ("local" mode). In local mode the local server holds
// this machine's operator credential and forwards every call to the coordinator over
// gRPC, so the page authenticates to the local server with its per-process token only.
const localToken =
  typeof document === "undefined"
    ? ""
    : (document.querySelector<HTMLMetaElement>('meta[name="tableforge-ui-token"]')?.content ?? "");
export const localMode = localToken !== "";

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (localMode) headers["X-TableForge-UI"] = localToken;
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : response.statusText;
    if (response.status === 401 && path !== "/api/session") onUnauthorized();
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

export interface InviteRequest {
  address?: string;
  node: boolean;
  role: Role | null;
  ttl_hours: number;
  label: string;
  confirm_admin?: boolean;
}

function query<T>(resource: string, argument = ""): Promise<T> {
  const suffix = argument ? `?argument=${encodeURIComponent(argument)}` : "";
  return request<T>("GET", `/api/cluster/query/${resource}${suffix}`);
}

function command<T>(action: string, body: object): Promise<T> {
  return request<T>("POST", `/api/cluster/command/${action}`, body);
}

const coordinatorApi = {
  session: () => request<Session>("GET", "/api/session"),
  login: (credential: string) => request<Session>("POST", "/api/session", { credential }),
  logout: () => request<{ ok: boolean }>("DELETE", "/api/session"),
  overview: () => request<Overview>("GET", "/api/overview"),
  nodes: () => request<NodeView[]>("GET", "/api/nodes"),
  presets: () => request<Preset[]>("GET", "/api/presets"),
  experiments: () => request<ExperimentSummary[]>("GET", "/api/experiments"),
  experiment: (id: string) =>
    request<ExperimentDetail>("GET", `/api/experiments/${encodeURIComponent(id)}`),
  createExperiment: (preset: string) =>
    request<ExperimentSummary>("POST", "/api/experiments", { preset }),
  startExperiment: (id: string) =>
    request<ExperimentSummary>("POST", `/api/experiments/${encodeURIComponent(id)}/start`),
  stopExperiment: (id: string) =>
    request<ExperimentSummary>("POST", `/api/experiments/${encodeURIComponent(id)}/stop`),
  matrix: (id: string) =>
    request<Matrix>("GET", `/api/experiments/${encodeURIComponent(id)}/matrix`),
  checkpoints: () => request<CheckpointView[]>("GET", "/api/checkpoints"),
  events: (limit = 200) => request<DomainEvent[]>("GET", `/api/events?limit=${limit}`),
  eventsAfter: (after: number) =>
    Promise.reject<{ latest: number; events: DomainEvent[] }>(new Error(`not used (${after})`)),
  diagnostics: () => request<Diagnostics>("GET", "/api/diagnostics"),
  invites: () => request<InviteView[]>("GET", "/api/admin/invites"),
  createInvite: (body: InviteRequest) =>
    request<CreatedInvite>("POST", "/api/admin/invites", body),
  revokeInvite: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/admin/invites/${encodeURIComponent(id)}`),
  operators: () => request<OperatorView[]>("GET", "/api/admin/operators"),
  revokeOperator: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/admin/operators/${encodeURIComponent(id)}`),
  revokeNode: (id: string) =>
    request<{ revoked_sessions: number }>(
      "POST",
      `/api/admin/nodes/${encodeURIComponent(id)}/revoke`,
    ),
};

const localApi: typeof coordinatorApi = {
  session: () => request<Session>("GET", "/api/cluster/session"),
  login: () => Promise.reject(new ApiError(400, "not used in local mode")),
  logout: () => Promise.resolve({ ok: true }),
  overview: () => query<Overview>("overview"),
  nodes: () => query<NodeView[]>("nodes"),
  presets: () => query<Preset[]>("presets"),
  experiments: () => query<ExperimentSummary[]>("experiments"),
  experiment: (id) => query<ExperimentDetail>("experiment", id),
  createExperiment: (preset) => command<ExperimentSummary>("create_experiment", { preset }),
  startExperiment: (id) => command<ExperimentSummary>("start_experiment", { experiment_id: id }),
  stopExperiment: (id) => command<ExperimentSummary>("stop_experiment", { experiment_id: id }),
  matrix: (id) => query<Matrix>("matrix", id),
  checkpoints: () => query<CheckpointView[]>("checkpoints"),
  events: (limit = 200) => query<DomainEvent[]>("events", String(limit)),
  eventsAfter: (after) =>
    query<{ latest: number; events: DomainEvent[] }>("events_after", String(after)),
  diagnostics: () => query<Diagnostics>("diagnostics"),
  invites: () => query<InviteView[]>("invites"),
  createInvite: (body) => command<CreatedInvite>("create_invite", body),
  revokeInvite: (id) => command<{ ok: boolean }>("revoke_invite", { invite_id: id }),
  operators: () => query<OperatorView[]>("operators"),
  revokeOperator: (id) => command<{ ok: boolean }>("revoke_operator", { operator_id: id }),
  revokeNode: (id) => command<{ revoked_sessions: number }>("revoke_node", { node_id: id }),
};

export const api = localMode ? localApi : coordinatorApi;
