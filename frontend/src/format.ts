import type { Ats, Condition, ListingStatus } from './api/client'

export const ATS_LIST: { key: Ats; label: string; example: string }[] = [
  { key: 'greenhouse', label: 'Greenhouse', example: 'boards.greenhouse.io/<board>' },
  { key: 'lever', label: 'Lever', example: 'jobs.lever.co/<board>' },
  { key: 'ashby', label: 'Ashby', example: 'jobs.ashbyhq.com/<board>' },
]

export const atsLabel = (ats: Ats) => ATS_LIST.find((a) => a.key === ats)?.label ?? ats

export const shortDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '–'

export const STATUS_LABELS: Record<ListingStatus, string> = {
  not_applied: 'Not applied',
  ready_for_review: 'Ready for review',
  applying: 'Applying',
  applied: 'Applied',
  needs_input: 'Needs input',
  failed: 'Failed',
  manual_apply: 'Manual apply',
  skipped: 'Skipped',
  closed: 'Closed',
}

export const CONDITION_LABELS: Record<Condition, string> = { role: 'Role', skills: 'Skills', location: 'Location' }

/** "Role and Location", "Role, Skills and Location". */
export const joinAnd = (words: string[]) =>
  words.length <= 1 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`

export const workModeLabel = (mode: string | null) =>
  mode ? mode.charAt(0).toUpperCase() + mode.slice(1) : 'Work mode unclear'
