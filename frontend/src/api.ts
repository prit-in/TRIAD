/**
 * The only place this application talks to the server (PRD section 26.2).
 *
 * Two rules it enforces so no page has to remember them:
 *
 *  - an error is a Problem Details object, surfaced with its `title` and `detail`. The UI
 *    never invents a friendlier message, because "something went wrong" is exactly what a
 *    user cannot act on, whereas "approver must differ from requester" is;
 *  - a 401 clears the stored token. A page that keeps rendering with a dead token shows the
 *    last data it happened to have, which looks like current data and is not.
 */

const BASE: string = import.meta.env.VITE_API_BASE ?? "/api/v1";
const TOKEN_KEY = "triad.token";

export class ApiProblem extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  readonly type: string;

  constructor(status: number, body: Record<string, unknown> | null) {
    const title = typeof body?.title === "string" ? body.title : "Request failed";
    const detail = typeof body?.detail === "string" ? body.detail : `HTTP ${status}`;
    super(detail);
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.type = typeof body?.type === "string" ? body.type : "";
  }
}

export function storedToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token === null) window.localStorage.removeItem(TOKEN_KEY);
    else window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* a browser with storage disabled still works, it just forgets on reload */
  }
}

let onUnauthorised: () => void = () => {};

export function whenUnauthorised(handler: () => void): void {
  onUnauthorised = handler;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { authenticated = true }: { authenticated?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) headers.set("Content-Type", "application/json");

  const token = storedToken();
  if (authenticated && token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  if (response.status === 401 && authenticated) {
    storeToken(null);
    onUnauthorised();
  }

  const text = await response.text();
  const body = text ? (JSON.parse(text) as Record<string, unknown>) : null;
  if (!response.ok) throw new ApiProblem(response.status, body);
  return body as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>(
      "/auth/token",
      { method: "POST", body: JSON.stringify({ email, password }) },
      { authenticated: false },
    ),
  me: () => request<User>("/auth/me"),
  stats: () => request<Stats>("/stats"),
  runs: (query = "") => request<Page<RunSummary>>(`/pipelines${query}`),
  run: (id: string) => request<RunDetail>(`/pipelines/${id}`),
  runDecisions: (id: string) => request<Decision[]>(`/pipelines/${id}/decisions`),
  runArtifacts: (id: string) => request<Artifact[]>(`/pipelines/${id}/artifacts`),
  runLogs: (id: string) => request<LogLine[]>(`/pipelines/${id}/logs`),
  cancelRun: (id: string) => request<{ run_id: string; state: string }>(
    `/pipelines/${id}/cancel`, { method: "POST" },
  ),
  models: () => request<ModelSummary[]>("/models"),
  model: (ref: string) => request<ModelDetail>(`/models/${ref}`),
  approvals: (status = "pending") => request<Approval[]>(`/approvals?status=${status}`),
  decide: (id: string, approved: boolean, comment: string) =>
    request<Approval>(`/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ approved, comment }),
    }),
  alerts: (status = "open") => request<Alert[]>(`/alerts?status=${status}`),
  acknowledgeAlert: (id: string) =>
    request<Alert>(`/alerts/${id}/acknowledge`, { method: "POST" }),
  predictions: (query = "") => request<Prediction[] | PredictionAggregate>(`/predictions${query}`),
  drift: () => request<DriftReport[]>("/drift"),
  reports: () => request<Report[]>("/reports"),
  /** The curves, intervals, segment metrics and importance behind one version's charts. */
  evaluation: (modelRef: string, versionId: string) =>
    request<Evaluation>(`/models/${modelRef}/versions/${versionId}/evaluation`),
  experiments: () => request<ExperimentResult[]>("/experiments"),
  auditLogs: (query = "") => request<AuditEntry[]>(`/audit-logs${query}`),
  chainStatus: () => request<ChainStatus>("/audit-logs/verify"),
};

// --- what the API returns -------------------------------------------------------

/**
 * The evaluation document a run writes, served per model version.
 *
 * These are point series, not scalars, and they are read rather than recomputed: a ROC or a
 * reliability curve needs the labels and the scores together, and the predictions table holds
 * no labels. Drawing them from anything the browser could derive would mean inventing them.
 */
export interface Evaluation {
  run_key: string;
  model_name: string;
  selected_family: string;
  threshold: number;
  generated_at: string;
  test: MetricsBlock;
  valid: MetricsBlock;
  segments: MetricsBlock[];
  champion_delta: Record<string, number> | null;
  candidates: { family: string; valid_pr_auc: number | null; valid_roc_auc: number | null; selected: boolean }[];
  rejected: { family: string; reason: string }[];
  acceptance: Record<string, { passed: boolean; reason_codes: string[] }>;
  rules_applied: string[];
  feature_importance: { method: string; note: string; global: { feature: string; importance: number }[] } | null;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor_type: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  run_id: string | null;
  outcome: string;
  details: Record<string, unknown>;
  entry_hash: string;
}

export interface ChainStatus {
  ok: boolean;
  entries_checked: number;
  chains_checked: number;
  broken_at: number | null;
  broken_scope: string | null;
  detail: string | null;
}

/** One case-study experiment as the harness wrote it (PRD section 33). */
export interface ExperimentResult {
  exp_id: string;
  title?: string;
  hypothesis?: string;
  status: string;
  hypothesis_held: boolean | null;
  conclusion: string;
  limitations: string[];
  started_at?: string;
  duration_s?: number;
  trials: {
    condition: string;
    ok: boolean;
    detail: string;
    measurements?: { name: string; value: unknown; unit: string; note: string }[];
  }[];
}

export interface MetricsBlock {
  split: string;
  segment: string | null;
  n_rows: number;
  prevalence: number;
  values: Record<string, number>;
  intervals: Record<string, [number, number]>;
  confusion: { tn: number; fp: number; fn: number; tp: number };
  curves: Curves | Record<string, never>;
}

export interface Curves {
  roc: { fpr: number[]; tpr: number[] };
  pr: { recall: number[]; precision: number[]; baseline: number };
  calibration: {
    bin_low: number;
    bin_high: number;
    n: number;
    mean_predicted: number;
    observed_rate: number;
  }[];
  lift: { k: number; lift: number; cumulative_gain: number }[];
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  role: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface Stats {
  runs_today: number;
  runs_finished: number;
  success_rate: number | null;
  open_alerts: number;
  pending_approvals: number;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  limit: number;
}

export interface RunSummary {
  run_id: string;
  pipeline: string;
  state: string;
  current_stage: string | null;
  trigger_type: string;
  triggered_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_summary: string | null;
}

export interface Stage {
  name: string;
  status: string;
  attempts: number;
  duration_s: number | null;
}

export interface RunDetail extends RunSummary {
  mode: string;
  config_hash: string | null;
  stages: Stage[];
  budget_used: Record<string, number>;
  budget_limits: Record<string, number | undefined>;
  pending_approval: {
    id: string;
    action_type: string;
    target_ref: string | null;
    expires_at: string | null;
  } | null;
}

export interface Decision {
  id: string;
  agent: string;
  decision_type: string;
  decision: string;
  summary: string | null;
  policy_rule_ids: string[];
  evidence_refs: Record<string, unknown>;
  outcome: string;
  created_at: string | null;
}

export interface Artifact {
  id: string;
  language: string;
  template_id: string | null;
  slot_id: string | null;
  content_hash: string;
  static_check_status: string | null;
  compile_status: string | null;
  gate_results: { gates?: { gate: number; name: string; passed: boolean; detail: string }[] };
}

export interface LogLine {
  at: string | null;
  stage: string | null;
  level: string;
  message: string;
}

export interface ModelVersion {
  id: string;
  algorithm: string;
  mlflow_version: string | null;
  feature_set_version: string | null;
  governance_status: string;
  threshold: number | null;
  created_at: string | null;
  promoted_at: string | null;
  metrics: Record<string, number>;
}

export interface ModelSummary {
  id: string;
  name: string;
  task_type: string;
  target_definition: string;
  champion: ModelVersion | null;
  challenger: ModelVersion | null;
}

export interface ModelDetail extends ModelSummary {
  versions: ModelVersion[];
  model_card_uri: string | null;
}

export interface Approval {
  id: string;
  run_id: string;
  action_type: string;
  target_ref: string;
  status: string;
  requested_by: string;
  decided_by: string | null;
  decision_note: string | null;
  evidence_uri: string | null;
  requested_at: string | null;
  expires_at: string | null;
  decided_at: string | null;
}

export interface Alert {
  id: string;
  alert_type: string;
  severity: string;
  message: string;
  status: string;
  run_id: string | null;
  created_at: string | null;
}

export interface Prediction {
  customer_ref: string;
  score_date: string;
  churn_probability: number;
  risk_band: string;
  prediction_set: string;
  model_version_id: string;
  feature_set_version: string | null;
  top_reasons: { feature: string; value: number }[];
}

export interface PredictionAggregate {
  rows: number;
  by_risk_band: Record<string, number>;
  mean_probability: number | null;
  score_dates: string[];
}

export interface DriftReport {
  id: string;
  model_version_id: string;
  reference_ref: string;
  current_ref: string;
  flagged_features: string[];
  max_psi: number | null;
  score_flagged: boolean | null;
  computed_at: string | null;
}

export interface Report {
  id: string;
  run_id: string;
  report_type: string;
  format: string;
  status: string;
  storage_uri: string;
  claim_check_status: string | null;
  created_at: string | null;
}

export function isAggregate(
  value: Prediction[] | PredictionAggregate,
): value is PredictionAggregate {
  return !Array.isArray(value);
}
