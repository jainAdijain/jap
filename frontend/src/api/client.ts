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
}
export type Run = {
  id: string
  trigger: 'resume_updated' | 'manual' | 'scheduled'
  mode: 'find_only' | 'find_and_apply'
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  started_at: string | null
  ended_at: string | null
  counts: RunCounts
  error: string | null
  extracting: number
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

export type ListingRow = {
  id: string
  title: string
  company: string
  locations: string[]
  work_mode: string | null
  status: string
  last_posted_at: string | null
  platforms: Ats[]
  postings: number
}
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
export type ListingDetail = ListingRow & {
  canonical_role: string
  skills: string[]
  seniority: string | null
  remote_regions: string[]
  primary: PostingDetail | null
  also_on: AlsoOn[]
  same_company: { id: string; title: string }[]
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
  startRun: () => request<Run>('/api/runs', json('POST', { mode: 'find_only' })),
  latestRun: () => request<Run | null>('/api/runs/latest'),
  postings: (params: { q?: string; state?: 'open' | 'closed' | 'all'; page?: number }) => {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    if (params.state) query.set('state', params.state)
    if (params.page) query.set('page', String(params.page))
    return request<PostingPage>(`/api/postings?${query}`)
  },
  posting: (id: string) => request<PostingDetail>(`/api/postings/${id}`),
  listings: (params: { q?: string; status?: 'open' | 'closed' | 'all'; page?: number }) => {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    if (params.status) query.set('status', params.status)
    if (params.page) query.set('page', String(params.page))
    return request<ListingPage>(`/api/listings?${query}`)
  },
  listing: (id: string) => request<ListingDetail>(`/api/listings/${id}`),
  merge: (id: string, other: string) =>
    request<ListingDetail>(`/api/listings/${id}/merge`, json('POST', { other_listing_id: other })),
  split: (postingId: string) =>
    request<{ listing_id: string }>(`/api/postings/${postingId}/split`, json('POST', {})),
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
