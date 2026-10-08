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

/** The API root, for the one caller that cannot go through `request`: the SSE stream. */
export const apiBase = (): string => BASE;
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

async function send(
  path: string,
  init: RequestInit,
  authenticated: boolean,
): Promise<{ response: Response; text: string }> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) headers.set("Content-Type", "application/json");

  const token = storedToken();
  if (authenticated && token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  if (response.status === 401 && authenticated) {
    storeToken(null);
    onUnauthorised();
  }
  return { response, text: await response.text() };
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { authenticated = true }: { authenticated?: boolean } = {},
): Promise<T> {
  const { response, text } = await send(path, init, authenticated);
  const body = text ? (JSON.parse(text) as Record<string, unknown>) : null;
  if (!response.ok) throw new ApiProblem(response.status, body);
  return body as T;
}

/**
 * The same call for an endpoint that answers with a document rather than JSON.
 *
 * The rendered report is HTML behind the same bearer token as everything else, so it cannot be
 * an `<a href>` or an `<iframe src>`: neither carries the header. It is fetched here and
 * handed to the page as text. A failure still arrives as Problem Details JSON, so the error
 * path parses and the success path does not.
 */
async function requestText(path: string): Promise<string> {
  const { response, text } = await send(path, {}, true);
  if (!response.ok) {
    let body: Record<string, unknown> | null = null;
    try {
      body = text ? (JSON.parse(text) as Record<string, unknown>) : null;
    } catch {
      /* a non-JSON error body: ApiProblem falls back to the status line */
    }
    throw new ApiProblem(response.status, body);
  }
  return text;
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
  /** Start a governed run on the latest version of each named dataset (PRD 25.2). */
  startRun: (pipeline: string, inputs: Record<string, string>) =>
    request<{ run_id: string; state: string }>("/pipelines/run", {
      method: "POST",
      body: JSON.stringify({ pipeline, inputs }),
    }),
  run: (id: string) => request<RunDetail>(`/pipelines/${id}`),
  runDecisions: (id: string) => request<Decision[]>(`/pipelines/${id}/decisions`),
  runArtifacts: (id: string) => request<Artifact[]>(`/pipelines/${id}/artifacts`),
  runLogs: (id: string) => request<LogLine[]>(`/pipelines/${id}/logs`),
  /** FR-036: each execution, with every Spark stage its job ran. */
  runExecutions: (id: string) => request<Execution[]>(`/pipelines/${id}/executions`),
  cancelRun: (id: string) => request<{ run_id: string; state: string }>(
    `/pipelines/${id}/cancel`, { method: "POST" },
  ),
  /** PRD section 30: promote an escalated candidate anyway. One approval per required role. */
  requestOverride: (runKey: string, note: string) =>
    request<Approval[]>(`/pipelines/${runKey}/override`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  models: () => request<ModelSummary[]>("/models"),
  model: (ref: string) => request<ModelDetail>(`/models/${ref}`),
  /** PRD 19.5: restore the previous recorded champion. Pre-approved, audited, alerted. */
  rollback: (ref: string, previousVersion: string, reason: string) =>
    request<ModelDetail>(`/models/${ref}/rollback`, {
      method: "POST",
      body: JSON.stringify({ previous_version: previousVersion, reason }),
    }),
  approvals: (status = "pending") => request<Approval[]>(`/approvals?status=${status}`),
  /** PRD 30.5: the evidence bundle a request is decided on, not just where it is stored. */
  approvalEvidence: (id: string) => request<EvidenceBundle>(`/approvals/${id}/evidence`),
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
  auditActions: () => request<AuditActionCount[]>("/audit-logs/actions"),
  chainStatus: () => request<ChainStatus>("/audit-logs/verify"),
  datasets: () => request<DatasetSummary[]>("/datasets"),
  datasetVersions: (ref: string) =>
    request<DatasetVersionSummary[]>(`/datasets/${encodeURIComponent(ref)}/versions`),
  datasetProfile: (ref: string, versionNo: number) =>
    request<Profile>(`/datasets/${encodeURIComponent(ref)}/versions/${versionNo}/profile`),
  /** The rendered document, not its metadata row (FR-072). */
  reportHtml: (id: string) => requestText(`/reports/${id}?file=true`),
  settings: () => request<SettingsView>("/settings"),
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
  // `value` is what `predict.explain` writes, not `importance`. Typing it wrong cost a
  // silently empty chart: the mapped values came back undefined, the bar chart dropped them as
  // not-finite, and the panel said "nothing measured" over five perfectly good measurements.
  feature_importance: {
    method: string;
    note: string;
    global: { feature: string; value: number }[];
  } | null;
}

export interface AuditActionCount {
  action: string;
  count: number;
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
  /** Set while an escalated run's candidate failed only a rule a person may override. */
  override?: { eligible: boolean; failed_rules: string[]; reason: string } | null;
}

export interface Decision {
  id: string;
  agent: string;
  decision_type: string;
  decision: string;
  summary: string | null;
  policy_rule_ids: string[];
  evidence_refs: Record<string, unknown>;
  /** The options refused, each with the rule that refused it. Empty where a decision had no
   *  alternative: a routing rule match has one answer. */
  alternatives: DecisionAlternative[];
  outcome: string;
  created_at: string | null;
}

export interface DecisionAlternative {
  family?: string;
  reason?: string;
  rule?: string;
  evidence?: Record<string, number | null>;
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
  /** One share of a decision needing several people: the role that must decide it and the
   *  group its shares belong to. Null for an ordinary single approval. */
  policy_action: string | null;
  required_role: string | null;
  group_id: string | null;
}

/** One Spark stage as the job's listener measured it (FR-036). */
export interface SparkStage {
  stage_id: number;
  attempt: number;
  name: string;
  num_tasks: number;
  duration_ms: number;
  executor_run_time_ms: number;
  jvm_gc_time_ms: number;
  input_bytes: number;
  output_bytes: number;
  shuffle_read_bytes: number;
  shuffle_write_bytes: number;
  memory_spill_bytes: number;
  disk_spill_bytes: number;
  median_task_ms: number;
  max_task_ms: number;
  task_skew: number;
  failed: boolean;
}

export interface Execution {
  id: string;
  stage: string;
  engine: string;
  status: string;
  exit_code: number | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  spark_app_id: string | null;
  spark: {
    totals: Record<string, number>;
    stages: SparkStage[];
  } | null;
}

export interface ThresholdReviewSegment {
  segment: string;
  rows?: number;
  positives?: number;
  recall_at_global_threshold?: number | null;
  segment_threshold?: number;
  recall_at_segment_threshold?: number;
  flagged_share?: number;
  insufficient_data?: boolean;
}

export interface EvidenceBundle {
  action_type: string;
  target_ref: string;
  metrics_vs_champion: { mean?: number; lower_95?: number; upper_95?: number; note?: string };
  segment_checks: {
    rule: string;
    result: string;
    recall_gaps?: Record<string, number>;
    threshold_review?: {
      segmentation?: string;
      global_threshold?: number;
      target_recall?: number;
      gap_at_segment_thresholds?: number;
      flagged_share_global?: number;
      flagged_share_segment_aware?: number;
      segments?: ThresholdReviewSegment[];
      note?: string;
    };
  }[];
  calibration: Record<string, unknown>;
  decision_records: Record<string, unknown>[];
  risk_notes: string[];
  override_note: string | null;
  model_card_uri: string | null;
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
  /** What the claim check actually checked (FR-071): counts, and anything it refused. */
  claim_check_json: {
    passed?: boolean;
    facts_verified?: number;
    unverified_numbers?: string[];
    unknown_placeholders?: string[];
    causal_phrases?: string[];
  };
  created_at: string | null;
}

export interface DatasetSummary {
  id: string;
  name: string;
  entity_type: string | null;
  source_type: string | null;
  latest_version: number | null;
  latest_quality_score: number | null;
  updated_at: string | null;
}

export interface DatasetVersionSummary {
  version_no: number;
  row_count: number | null;
  checksum: string | null;
  quality_score: number | null;
  created_at: string | null;
  storage_uri: string | null;
}

export interface ProfileColumn {
  name: string;
  physical_type: string;
  semantic_type: string;
  null_count: number;
  null_rate: number;
  empty_string_count: number;
  approx_distinct: number;
  /** Counted exactly only for key columns and low-cardinality ones; null means approximated. */
  exact_distinct: number | null;
  min_value: string | number | null;
  max_value: string | number | null;
  mean: number | null;
  stddev: number | null;
  outlier_count: number | null;
  quantiles: Record<string, number>;
  top_values: { value: string | null; count: number }[];
}

export interface ProfileIssue {
  id: string;
  column: string | null;
  severity: string;
  message: string;
  evidence: Record<string, unknown>;
}

/** The profile document the profiling stage wrote, served per dataset version (FR-068). */
export interface Profile {
  dataset: string;
  version_no: number | null;
  row_count: number;
  column_count: number;
  duplicate_rows: number;
  key_columns: string[];
  key_duplicate_rows: number;
  quality_score: number;
  quality_dimensions: Record<string, number>;
  issues: ProfileIssue[];
  columns: ProfileColumn[];
  computed_at: string | null;
  schema_hash: string | null;
  sample_fraction: number | null;
  schema_diff: {
    added_columns?: string[];
    removed_columns?: string[];
    retyped_columns?: { column: string; from: string; to: string }[];
    rename_candidates?: { from: string; to: string }[];
    row_count_delta?: number;
  };
  population_stability: Record<string, number>;
}

export interface SettingsView {
  environment: string;
  policies: {
    name: string;
    path: string;
    version: string;
    valid: boolean;
    load_error: string | null;
    sha256: string | null;
    action_count: number;
    defaults: Record<string, number | string | boolean>;
    text: string;
  }[];
  budgets: {
    keys: string[];
    code_default: Record<string, number>;
    pipeline_config: Record<string, number>;
    pipeline_config_path: string;
    pipeline_config_error: string | null;
  };
  connections: { name: string; target: string; detail: string }[];
  users: {
    id: string;
    email: string;
    display_name: string;
    role: string;
    is_active: boolean;
    created_at: string | null;
    last_login_at: string | null;
  }[];
}

export function isAggregate(
  value: Prediction[] | PredictionAggregate,
): value is PredictionAggregate {
  return !Array.isArray(value);
}
