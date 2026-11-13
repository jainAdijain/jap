// Same origin by default (H2): Caddy in test and prod, Vite's proxy in dev, pass /api to the app.
const API_URL = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  code: string
  status: number
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

/** Fired when the API says the session has ended (PRD S5), so the app shows sign-in again. */
export const SIGNED_OUT_EVENT = 'jap:signed-out'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // The session cookie rides along (PRD S5); it is HttpOnly, so scripts never see it.
  const resp = await fetch(`${API_URL}${path}`, { credentials: 'include', ...init })
  if (resp.status === 204) return undefined as T
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    const err = body?.error
    if (resp.status === 401 && err?.code === 'signed_out') window.dispatchEvent(new Event(SIGNED_OUT_EVENT))
    throw new ApiError(resp.status, err?.code ?? 'error', err?.message ?? `API answered ${resp.status}`)
  }
  return body as T
}

const json = (method: string, data: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
})

export type Check = { ok: boolean; detail: string }
export type Health = { ok: boolean; env?: string; checks: Record<string, Check> }

export type WorkMode = 'remote' | 'hybrid' | 'onsite'
/** What a job can fit on. Skills never hide a job (PRD 1.21), so they are not a condition. */
export type Condition = 'role' | 'location'
export type Link = { label: string; url: string }
export type Spend = { month_usd: number; cap_usd: number; reached: boolean }

export type ProfileFields = {
  full_name: string | null
  email: string | null
  phone: string | null
  address: string | null
  links: Link[]
  target_roles: string[]
  must_have_skills: string[]
  nice_to_have_skills: string[]
  locations: string[]
  remote_regions: string[]
  remote_any: boolean
  match_rule: Condition[][]
  work_modes: WorkMode[]
  excluded_companies: string[]
  excluded_title_words: string[]
  tone_note: string | null
}

export type Profile = ProfileFields & {
  apply_mode: 'review' | 'automatic'
  daily_run: boolean
  daily_run_at: string // HH:MM:SS in JAP_TZ
  daily_cap: number
  /** Automatic mode sends only at or above this % of my required skills (F6); 0 is off. */
  auto_apply_min_skills: number
  spend: Spend
}

export type Resume = {
  id: string
  original_name: string
  mime: string
  uploaded_at: string
  is_active: boolean
  accepted_at: string | null
  status: 'parsing' | 'parsed' | 'failed'
  parse_error: string | null
  suggested_skills: string[]
  titles: string[]
  years_experience: number | null
  links_found: string[]
}

export type Ats = 'greenhouse' | 'lever' | 'ashby'
export type Board = {
  id: string
  ats: Ats
  board_token: string
  display_name: string
  origin: string
  last_checked_at: string | null
  last_ok: boolean | null
}
export type Detected = {
  ats: Ats
  board_token: string
  display_name: string
  job_count: number
  already_watched: boolean
}

/** What stopping following a company does, counted in jobs as Jobs shows them. */
export type UnfollowEffect = { display_name: string; hidden: number; kept: number; unread: number }

export type RunCounts = {
  listed?: number
  merged?: number
  new_listings?: number
  possible_duplicates?: number
  boards?: number
  boards_failed?: number
  board_errors?: string[]
  fetched?: number
  new?: number
  changed?: number
  unchanged?: number
  reopened?: number
  closed?: number
  enrich_queued?: number
  apply_queued?: number
  ready_for_review?: number
  applied?: number
  failed?: number
  needs_input?: number
  draft_failed?: number
  spend_cap_reached?: boolean
  daily_cap_reached?: boolean
}
export type Run = {
  id: string
  trigger: 'resume_updated' | 'manual' | 'scheduled' | 'profile_saved'
  mode: 'find_only' | 'find_and_apply'
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  started_at: string | null
  ended_at: string | null
  scope: { roles?: string[]; board_ids?: string[] }
  counts: RunCounts
  error: string | null
  extracting: number
}

export type RunState = { last: Run | null; active: Run | null; queued: Run | null }
export type RunRequest = { mode: 'find_only' | 'find_and_apply'; roles?: string[] }
export type Switches = {
  apply_mode?: Profile['apply_mode']
  daily_run?: boolean
  daily_run_at?: string
  daily_cap?: number
  auto_apply_min_skills?: number
  spend_cap_usd?: number
}

export type Salary = { min: number | null; max: number | null; currency: string | null; period: string | null }
export type Extracted = {
  skills?: string[]
  seniority?: string
  work_mode?: string
  salary?: Salary
  visa_sponsorship?: string
  remote_regions?: string[]
  cover_letter_asked?: boolean
  direct: string[]
}
export type PostingRow = {
  id: string
  source: Ats
  company: string
  title: string
  locations: string[]
  posted_at: string | null
  state: 'open' | 'closed'
  extracted: boolean
  work_mode: string | null
  first_seen_at: string
}
export type PostingPage = { total: number; page: number; page_size: number; rows: PostingRow[] }
export type PostingDetail = PostingRow & {
  department: string | null
  employment_type: string | null
  requisition_id: string | null
  posting_url: string
  apply_url: string
  description_text: string
  fields: Extracted | null
  closed_at: string | null
}
export type Answer = { id: string; question: string; answer: string }

export type MatchPreview = { matching_count: number; by_condition: Record<Condition, number> }
export type ListingStatus =
  | 'not_applied'
  | 'ready_for_review'
  | 'applying'
  | 'applied'
  | 'needs_input'
  | 'failed'
  | 'manual_apply'
  | 'skipped'
  | 'closed'
export type ListingRow = {
  id: string
  title: string
  company: string
  locations: string[]
  work_mode: string | null
  status: ListingStatus
  last_posted_at: string | null
  found_at: string
  platforms: Ats[]
  postings: number
  role: string
  matched: Condition[]
  matched_role: string | null
  nice_skills: string[]
  skills_match: SkillsMatch
}
/** How a job fits my skills (F5). Shown on every job; it never hides one. */
export type SkillsMatch = {
  stated: boolean // false: the job states no skills, shown as Skills not stated
  required_hit: string[]
  required_missing: string[]
  required_total: number
  required_pct: number | null // null when unknown
  nice_hit: string[]
  nice_total: number
}
export type SortKey = 'title' | 'company' | 'location' | 'skills' | 'work_mode' | 'status' | 'posted'
export type ListingFilters = {
  q?: string
  status?: 'open' | 'active' | 'all' | ListingStatus
  role?: string
  location?: string
  matched?: Condition
  min_skills?: number
  work_mode?: WorkMode | 'unknown'
  posted_days?: number
  sort?: SortKey
  dir?: 'asc' | 'desc'
  page?: number
  page_size?: number
}
export type StatusCounts = { listings: number } & Record<Exclude<ListingStatus, 'closed'>, number>
export type ListingSummary = {
  totals: StatusCounts
  by_role: (StatusCounts & { role: string | null })[]
  locations: string[]
}
/** Role filter value for listings matched through Location only. */
export const OTHER_ROLE = '__other'
export type ListingPage = { total: number; page: number; page_size: number; rows: ListingRow[] }
export type AlsoOn = {
  posting_id: string
  source: Ats
  title: string
  company: string
  locations: string[]
  posting_url: string
  state: 'open' | 'closed'
  posted_at: string | null
}
export type FormField = {
  key: string
  label: string
  type: string
  required: boolean
  options: string[]
  max_chars: number | null
  filled_from: string
  value: string
}
export type Pending = { key: string; label: string; reason: string; options: string[]; blocked: boolean; draft_failed: boolean }
export type ApplicationStatus =
  | 'preparing'
  | 'ready_for_review'
  | 'applying'
  | 'applied'
  | 'needs_input'
  | 'failed'
  | 'cancelled'
export type Claim = { claim: string; found: boolean; evidence: string }
export type CoverLetter = {
  text: string
  words: number
  source: 'generated' | 'you'
  as_file: boolean
  has_pdf: boolean
  claims: Claim[]
}
export type Application = {
  id: string
  listing_id: string
  status: ApplicationStatus
  mode: 'review' | 'automatic'
  created_at: string
  updated_at: string
  resume: { id: string; original_name: string; uploaded_at: string } | null
  jd: { title: string; text: string; posting_url: string; source: string; captured_at: string | null } | null
  fields: FormField[]
  pending: Pending[]
  cover_letter_text: string | null
  cover_letter: CoverLetter | null
  error: string | null
  review_reason: string | null
  submitted_at: string | null
  email_sent_at: string | null
  has_screenshot: boolean
  has_trace: boolean
  submit_pressed: boolean
}
export type ListingDetail = ListingRow & {
  skills: string[]
  seniority: string | null
  remote_regions: string[]
  primary: PostingDetail | null
  also_on: AlsoOn[]
  same_company: { id: string; title: string }[]
  application: Application | null
  attempts: number
  apply_blocked: string | null
  unfollowed?: boolean // I stopped following its company
}
export type DuplicateSide = { id: string; title: string; platforms: Ats[]; locations: string[] }
export type Duplicate = {
  id: string
  company: string
  a: DuplicateSide
  b: DuplicateSide
  title_similarity: number
  description_similarity: number
}

export type FormCheck = {
  id: string
  ats: Ats
  ok: boolean
  fields: number
  required: number
  url: string | null
  error: string | null
  has_screenshot: boolean
  checked_at: string
}
export type Ops = {
  last_backup: { file: string; at: string; bytes: number } | null
  backups: number
  backup_today: boolean
  form_checks: FormCheck[]
}

export type User = { email: string; is_admin: boolean; two_step: boolean; recovery_codes_left: number }
export type AuthState = { has_accounts: boolean; sign_up_open: boolean; user: User | null; two_step_pending: boolean }
export type SignInResult = { signed_in: boolean; two_step: boolean; user: User | null }
export type TwoStepSetup = { secret: string; uri: string; qr_svg: string }

export const auth = {
  state: () => request<AuthState>('/api/auth/state'),
  signUp: (email: string, password: string) => request<SignInResult>('/api/auth/sign-up', json('POST', { email, password })),
  signIn: (email: string, password: string) => request<SignInResult>('/api/auth/sign-in', json('POST', { email, password })),
  code: (code: string) => request<SignInResult>('/api/auth/sign-in/code', json('POST', { code })),
  signOut: () => request<void>('/api/auth/sign-out', json('POST', {})),
  signOutEverywhere: () => request<void>('/api/auth/sign-out-everywhere', json('POST', {})),
  me: () => request<User>('/api/auth/me'),
  forgot: (email: string) => request<{ queued: boolean }>('/api/auth/forgot', json('POST', { email })),
  reset: (token: string, password: string) => request<void>('/api/auth/reset', json('POST', { token, password })),
  changePassword: (current: string, next: string) =>
    request<void>('/api/auth/password', json('POST', { current_password: current, new_password: next })),
  twoStepStart: () => request<TwoStepSetup>('/api/auth/two-step/start', json('POST', {})),
  twoStepConfirm: (code: string) => request<{ recovery_codes: string[] }>('/api/auth/two-step/confirm', json('POST', { code })),
  twoStepOff: (password: string) => request<void>('/api/auth/two-step/off', json('POST', { password })),
  newRecoveryCodes: (password: string) =>
    request<{ recovery_codes: string[] }>('/api/auth/two-step/recovery-codes', json('POST', { password })),
}

export type RunBrief = { id: string; trigger: string; status: string; started_at: string; ended_at: string | null; error: string | null }
export type ErrorGroup = { event: string; count: number; last_at: string; last_message: string; application_id: string | null; request_id: string | null }
export type RouteStat = { method: string; route: string; count: number; errors: number; avg_ms: number; max_ms: number }
export type Status = {
  checked_at: string
  env: string
  daily: { on: boolean; at: string | null; last: RunBrief | null; missed: boolean }
  stuck_runs: RunBrief[]
  errors_24h: number
  error_groups: ErrorGroup[]
  slow_routes: RouteStat[]
  disk: { data_bytes: number; backups_bytes: number; free_bytes: number; total_bytes: number; used_pct: number }
  spend: Spend
  alerts: { key: string; sent_at: string; detail: string | null }[]
}

export type FunnelWeek = { week: string; found: number; matched: number; prepared: number; applied: number; failed: number }
export type Insights = {
  weeks: number
  funnel: FunnelWeek[]
  totals: FunnelWeek
  sources: { source: string; postings: number; matched: number; applied: number; failed: number; success_rate: number | null; top_failures: string[] }[]
  spend_this_month: { purpose: string; model: string; calls: number; cost_usd: number }[]
  months: { month: string; cost_usd: number; applied: number; per_application_usd: number | null }[]
}

export type TimelineItem = {
  at: string
  kind: 'step' | 'model' | 'status'
  event: string
  text: string
  level: string
  duration_ms: number | null
  cost_usd: number | null
}
export type Timeline = { application_id: string; status: string; items: TimelineItem[]; kept_days: number }

export const api = {
  health: (signal?: AbortSignal) => request<Health>('/api/health', { signal }),
  profile: () => request<Profile>('/api/profile'),
  saveProfile: (p: ProfileFields) =>
    request<Profile & { matching_count: number }>('/api/profile', json('PUT', p)),
  previewProfile: (p: ProfileFields, signal?: AbortSignal) =>
    request<MatchPreview>('/api/profile/preview', { ...json('POST', p), signal }),
  resumes: () => request<Resume[]>('/api/resumes'),
  uploadResume: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Resume>('/api/resumes', { method: 'POST', body: form })
  },
  acceptResume: (id: string, must: string[], nice: string[]) =>
    request<Resume>(
      `/api/resumes/${id}/accept`,
      json('POST', { must_have_skills: must, nice_to_have_skills: nice }),
    ),
  resumeFileUrl: (id: string) => `${API_URL}/api/resumes/${id}/file`,
  boards: () => request<Board[]>('/api/boards'),
  addBoard: (b: Pick<Board, 'ats' | 'board_token' | 'display_name'>) =>
    request<Board>('/api/boards', json('POST', b)),
  detectBoard: (text: string) => request<Detected>('/api/boards/detect', json('POST', { text })),
  startRun: (body: RunRequest = { mode: 'find_only' }) => request<Run>('/api/runs', json('POST', body)),
  latestRun: () => request<Run | null>('/api/runs/latest'),
  runs: () => request<Run[]>('/api/runs'),
  runState: () => request<RunState>('/api/runs/state'),
  setSwitches: (s: Switches) => request<Profile>('/api/profile', json('PATCH', s)),
  postings: (params: { q?: string; state?: 'open' | 'closed' | 'all'; page?: number }) => {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    if (params.state) query.set('state', params.state)
    if (params.page) query.set('page', String(params.page))
    return request<PostingPage>(`/api/postings?${query}`)
  },
  posting: (id: string) => request<PostingDetail>(`/api/postings/${id}`),
  listings: (params: ListingFilters) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    return request<ListingPage>(`/api/listings?${query}`)
  },
  summary: () => request<ListingSummary>('/api/listings/summary'),
  listing: (id: string) => request<ListingDetail>(`/api/listings/${id}`),
  merge: (id: string, other: string) =>
    request<ListingDetail>(`/api/listings/${id}/merge`, json('POST', { other_listing_id: other })),
  split: (postingId: string) =>
    request<{ listing_id: string }>(`/api/postings/${postingId}/split`, json('POST', {})),
  applyNow: (id: string) => request<{ listing_id: string; queued: boolean }>(`/api/listings/${id}/apply`, json('POST', {})),
  skip: (id: string) => request<ListingDetail>(`/api/listings/${id}/skip`, json('POST', {})),
  unskip: (id: string) => request<ListingDetail>(`/api/listings/${id}/unskip`, json('POST', {})),
  editAnswers: (appId: string, answers: Record<string, string>, coverLetter?: string) =>
    request<Application>(`/api/applications/${appId}`, json('PUT', { answers, cover_letter: coverLetter ?? null })),
  approve: (appId: string) => request<Application>(`/api/applications/${appId}/approve`, json('POST', {})),
  answer: (appId: string, answers: Record<string, string>, saveToBank: boolean) =>
    request<Application>(`/api/applications/${appId}/answer`, json('POST', { answers, save_to_bank: saveToBank })),
  retry: (appId: string, checked = false) =>
    request<{ listing_id: string; queued: boolean }>(`/api/applications/${appId}/retry`, json('POST', { checked })),
  fileUrl: (kind: 'screenshot' | 'trace' | 'cover_letter', appId: string) => `${API_URL}/api/files/${kind}/${appId}`,
  duplicates: () => request<Duplicate[]>('/api/duplicates'),
  decide: (id: string, decision: 'merged' | 'kept_apart') =>
    request<void>(`/api/duplicates/${id}`, json('POST', { decision })),
  unfollowEffect: (id: string) => request<UnfollowEffect>(`/api/boards/${id}/unfollow`),
  removeBoard: (id: string) => request<void>(`/api/boards/${id}`, { method: 'DELETE' }),
  answers: () => request<Answer[]>('/api/answers'),
  addAnswer: (a: Omit<Answer, 'id'>) => request<Answer>('/api/answers', json('POST', a)),
  editAnswer: (a: Answer) =>
    request<Answer>(`/api/answers/${a.id}`, json('PUT', { question: a.question, answer: a.answer })),
  removeAnswer: (id: string) => request<void>(`/api/answers/${id}`, { method: 'DELETE' }),
  ops: (signal?: AbortSignal) => request<Ops>('/api/ops', { signal }),
  backupNow: () => request<{ queued: boolean }>('/api/ops/backup', json('POST', {})),
  formCheckNow: () => request<{ queued: boolean }>('/api/ops/form-check', json('POST', {})),
  formCheckShotUrl: (id: string) => `${API_URL}/api/ops/form-check/${id}/screenshot`,
  status: (signal?: AbortSignal) => request<Status>('/api/ops/status', { signal }),
  healthCheckNow: () => request<{ queued: boolean }>('/api/ops/health-check', json('POST', {})),
  insights: (weeks = 8) => request<Insights>(`/api/insights?weeks=${weeks}`),
  timeline: (appId: string) => request<Timeline>(`/api/applications/${appId}/timeline`),
}

export const getHealth = api.health
