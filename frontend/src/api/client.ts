const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8001'

export class ApiError extends Error {
  code: string
  status: number
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, init)
  if (resp.status === 204) return undefined as T
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    const err = body?.error
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
export type Health = { ok: boolean; checks: Record<string, Check> }

export type WorkMode = 'remote' | 'hybrid' | 'onsite'
export type Condition = 'role' | 'skills' | 'location'
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
export type Switches = { apply_mode?: Profile['apply_mode']; daily_run?: boolean; daily_run_at?: string }

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
  platforms: Ats[]
  postings: number
  role: string
  matched: Condition[]
  matched_role: string | null
  nice_skills: string[]
}
export type ListingFilters = {
  q?: string
  status?: 'open' | 'all' | ListingStatus
  role?: string
  location?: string
  matched?: Condition
  page?: number
}
export type StatusCounts = { listings: number } & Record<Exclude<ListingStatus, 'closed'>, number>
export type ListingSummary = { totals: StatusCounts; by_role: (StatusCounts & { role: string | null })[] }
/** Role filter value for listings matched through Skills or Location only. */
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
export type Pending = { key: string; label: string; reason: string; options: string[]; blocked: boolean }
export type ApplicationStatus =
  | 'preparing'
  | 'ready_for_review'
  | 'applying'
  | 'applied'
  | 'needs_input'
  | 'failed'
  | 'cancelled'
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
  error: string | null
  submitted_at: string | null
  email_sent_at: string | null
  has_screenshot: boolean
  has_trace: boolean
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
  editAnswers: (appId: string, answers: Record<string, string>) =>
    request<Application>(`/api/applications/${appId}`, json('PUT', { answers })),
  approve: (appId: string) => request<Application>(`/api/applications/${appId}/approve`, json('POST', {})),
  answer: (appId: string, answers: Record<string, string>, saveToBank: boolean) =>
    request<Application>(`/api/applications/${appId}/answer`, json('POST', { answers, save_to_bank: saveToBank })),
  retry: (appId: string) => request<{ listing_id: string; queued: boolean }>(`/api/applications/${appId}/retry`, json('POST', {})),
  fileUrl: (kind: 'screenshot' | 'trace', appId: string) => `${API_URL}/api/files/${kind}/${appId}`,
  duplicates: () => request<Duplicate[]>('/api/duplicates'),
  decide: (id: string, decision: 'merged' | 'kept_apart') =>
    request<void>(`/api/duplicates/${id}`, json('POST', { decision })),
  removeBoard: (id: string) => request<void>(`/api/boards/${id}`, { method: 'DELETE' }),
  answers: () => request<Answer[]>('/api/answers'),
  addAnswer: (a: Omit<Answer, 'id'>) => request<Answer>('/api/answers', json('POST', a)),
  editAnswer: (a: Answer) =>
    request<Answer>(`/api/answers/${a.id}`, json('PUT', { question: a.question, answer: a.answer })),
  removeAnswer: (id: string) => request<void>(`/api/answers/${id}`, { method: 'DELETE' }),
}

export const getHealth = api.health
