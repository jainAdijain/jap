import type { Ats, ListingRow, ListingStatus, Run } from './api/client'

export const ATS_LIST: { key: Ats; label: string; example: string }[] = [
  { key: 'greenhouse', label: 'Greenhouse', example: 'boards.greenhouse.io/<board>' },
  { key: 'lever', label: 'Lever', example: 'jobs.lever.co/<board>' },
  { key: 'ashby', label: 'Ashby', example: 'jobs.ashbyhq.com/<board>' },
]

export const atsLabel = (ats: Ats | string) => ATS_LIST.find((a) => a.key === ats)?.label ?? ats

export const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`

const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()

/** Posted dates in the table: Today, Yesterday, 28 Oct (with the year when it is not this one). */
export function shortDate(iso: string | null): string {
  if (!iso) return '–'
  const d = new Date(iso)
  const now = new Date()
  if (sameDay(d, now)) return 'Today'
  if (sameDay(d, new Date(now.getTime() - 86400000))) return 'Yesterday'
  return d.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric',
  })
}

/** "Today 06:00", "29 Oct 21:43". */
export function when(iso: string): string {
  const d = new Date(iso)
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  if (sameDay(d, new Date())) return `Today ${time}`
  return `${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${time}`
}

/** "just now", "5 min ago", "2 h ago", "3 days ago". */
export function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return plural(Math.floor(s / 86400), 'day') + ' ago'
}

/** The status pill: the words on screen (PRD 1.5) and its colour. */
export const STATUS: Record<ListingStatus, [string, string]> = {
  not_applied: ['New', 's-new'],
  needs_input: ['Needs you', 's-you'],
  ready_for_review: ['Ready to send', 's-ready'],
  applying: ['In progress', 's-ready'],
  applied: ['Sent', 's-sent'],
  failed: ['Failed', 's-fail'],
  manual_apply: ['Apply yourself', 's-you'],
  skipped: ['Hidden', 's-new'],
  closed: ['Closed', 's-new'],
}

export const workModeLabel = (mode: string | null) =>
  mode === 'onsite' ? 'On-site' : mode === 'hybrid' ? 'Hybrid' : mode === 'remote' ? 'Remote' : 'Not stated'

/** "Fits Backend Engineer · Bangalore · your skills": what the job matched. */
export function fitsLine(row: ListingRow): string {
  const parts: string[] = []
  if (row.matched.includes('role')) parts.push(row.matched_role ?? 'your job titles')
  if (row.matched.includes('location')) {
    const office = row.locations.find((l) => !/remote/i.test(l))
    parts.push(row.work_mode === 'remote' || !office ? 'Remote' : office.split(',')[0])
  }
  if (row.matched.includes('skills')) parts.push('your skills')
  return parts.length ? `Fits ${parts.join(' · ')}` : ''
}

export const STARTED_BY: Record<Run['trigger'], string> = {
  manual: 'You',
  scheduled: 'Daily search',
  resume_updated: 'New resume',
  profile_saved: 'Preferences saved',
}

export const money = (n: number) => `$${n.toFixed(2)}`

export const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))
