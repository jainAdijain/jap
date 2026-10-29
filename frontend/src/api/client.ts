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
export type Board = { id: string; ats: Ats; board_token: string; display_name: string; origin: string }
export type Answer = { id: string; question: string; answer: string }

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
  addBoard: (b: Omit<Board, 'id' | 'origin'>) => request<Board>('/api/boards', json('POST', b)),
  removeBoard: (id: string) => request<void>(`/api/boards/${id}`, { method: 'DELETE' }),
  answers: () => request<Answer[]>('/api/answers'),
  addAnswer: (a: Omit<Answer, 'id'>) => request<Answer>('/api/answers', json('POST', a)),
  editAnswer: (a: Answer) =>
    request<Answer>(`/api/answers/${a.id}`, json('PUT', { question: a.question, answer: a.answer })),
  removeAnswer: (id: string) => request<void>(`/api/answers/${id}`, { method: 'DELETE' }),
}

export const getHealth = api.health
