import type { Ats } from './api/client'

export const ATS_LIST: { key: Ats; label: string; example: string }[] = [
  { key: 'greenhouse', label: 'Greenhouse', example: 'boards.greenhouse.io/<board>' },
  { key: 'lever', label: 'Lever', example: 'jobs.lever.co/<board>' },
  { key: 'ashby', label: 'Ashby', example: 'jobs.ashbyhq.com/<board>' },
]

export const atsLabel = (ats: Ats) => ATS_LIST.find((a) => a.key === ats)?.label ?? ats

export const shortDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '–'
