import { useEffect, useState } from 'react'

import { api, type FunnelWeek, type Insights } from '../api/client'
import { useApp } from '../appContext'
import { atsLabel, errorText, money } from '../format'

const STEPS: { key: keyof Omit<FunnelWeek, 'week'>; label: string; help: string }[] = [
  { key: 'found', label: 'Found', help: 'New jobs JAP found' },
  { key: 'matched', label: 'Match you', help: 'Of those, jobs that match your preferences now' },
  { key: 'prepared', label: 'Started', help: 'Applications JAP started' },
  { key: 'applied', label: 'Sent', help: 'Applications sent and confirmed by the site' },
  { key: 'failed', label: 'Failed', help: 'Applications that ended Failed' },
]

const PURPOSES: Record<string, string> = {
  extract: 'Reading job descriptions',
  answer: 'Writing answers',
  check: 'Checking answers',
  cover_letter: 'Writing cover letters',
  claim_check: 'Checking cover letters',
  resume_parse: 'Reading your resume',
  same_job: 'Spotting duplicate jobs',
}

const cost = (n: number) => (n > 0 && n < 0.01 ? `$${n.toFixed(4)}` : money(n))
const pct = (n: number | null) => (n === null ? '–' : `${Math.round(n * 100)}%`)
const rate = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : '–')
const weekLabel = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
const monthLabel = (ym: string) => new Date(`${ym}-01T00:00:00`).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })

export default function InsightsPage() {
  const { tick } = useApp()
  const [weeks, setWeeks] = useState(8)
  const [data, setData] = useState<Insights | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.insights(weeks).then(
      (d) => {
        setData(d)
        setError(null)
      },
      (err) => setError(errorText(err)),
    )
  }, [weeks, tick])

  const t = data?.totals
  const top = Math.max(1, ...(data?.funnel ?? []).map((w) => w.found))
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Insights</h1>
          <p>Is JAP working for you: jobs found and sent, which job sites go well, and where the AI money goes.</p>
        </div>
        <label className="inline">
          Show
          <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} style={{ width: 'auto' }}>
            <option value={4}>4 weeks</option>
            <option value={8}>8 weeks</option>
            <option value={12}>12 weeks</option>
            <option value={26}>26 weeks</option>
          </select>
        </label>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && t && (
        <>
          <div className="sec">
            <div className="sec-l">
              <h2>Jobs to applications</h2>
              <p>Each week from Monday. Replies from employers aren't known to JAP yet, so this ends at Sent.</p>
            </div>
            <div className="sec-r">
              <div className="tiles">
                {STEPS.map((s) => (
                  <div key={s.key} className="tile" title={s.help}>
                    <span className="muted small">{s.label}</span>
                    <b className="mono">{t[s.key]}</b>
                  </div>
                ))}
              </div>
              <p className="help">
                {rate(t.matched, t.found)} of new jobs match you; {rate(t.applied, t.applied + t.failed)} of finished applications were sent.
              </p>
            </div>
            <div className="sec-r flush" style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--line)' }}>
              <div className="table-scroll">
                <table className="hist">
                  <thead>
                    <tr>
                      <th>Week of</th>
                      {STEPS.map((s) => (
                        <th key={s.key} title={s.help}>
                          {s.label}
                        </th>
                      ))}
                      <th style={{ width: '30%' }}>Found this week</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...data.funnel].reverse().map((w) => (
                      <tr key={w.week}>
                        <td className="mono">{weekLabel(w.week)}</td>
                        {STEPS.map((s) => (
                          <td key={s.key} className="mono">
                            {w[s.key]}
                          </td>
                        ))}
                        <td>
                          <div className="meter" aria-hidden="true">
                            <span style={{ width: `${(w.found / top) * 100}%` }} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="sec">
            <div className="sec-l">
              <h2>By job site</h2>
              <p>How each applicant tracking system does, over the same weeks.</p>
            </div>
            <div className="sec-r flush">
              {data.sources.length === 0 ? (
                <p className="muted" style={{ padding: 20 }}>
                  No jobs in these weeks.
                </p>
              ) : (
                <div className="table-scroll">
                  <table className="hist">
                    <thead>
                      <tr>
                        <th>Site</th>
                        <th>Jobs found</th>
                        <th>Match you</th>
                        <th>Sent</th>
                        <th>Failed</th>
                        <th>Sent of finished</th>
                        <th>Why they failed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.sources.map((s) => (
                        <tr key={s.source}>
                          <td>{atsLabel(s.source)}</td>
                          <td className="mono">{s.postings}</td>
                          <td className="mono">{s.matched}</td>
                          <td className="mono">{s.applied}</td>
                          <td className="mono">{s.failed}</td>
                          <td className="mono">{pct(s.success_rate)}</td>
                          <td title={s.top_failures.join('\n')}>{s.top_failures.join('; ') || <span className="muted">None</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>

          <div className="sec">
            <div className="sec-l">
              <h2>AI spend</h2>
              <p>What the AI model was used for this month, and the last six months with the cost of each sent application.</p>
            </div>
            <div className="sec-r flush">
              <div className="table-scroll">
                <table className="hist">
                  <thead>
                    <tr>
                      <th>This month</th>
                      <th>Model</th>
                      <th>Uses</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.spend_this_month.length === 0 && (
                      <tr>
                        <td colSpan={4} className="muted">
                          Nothing yet this month.
                        </td>
                      </tr>
                    )}
                    {data.spend_this_month.map((r) => (
                      <tr key={`${r.purpose}-${r.model}`}>
                        <td>{PURPOSES[r.purpose] ?? r.purpose}</td>
                        <td className="mono">{r.model}</td>
                        <td className="mono">{r.calls}</td>
                        <td className="mono">{cost(r.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <table className="hist">
                  <thead>
                    <tr>
                      <th>Month</th>
                      <th>Cost</th>
                      <th>Sent</th>
                      <th>Per sent application</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...data.months].reverse().map((m) => (
                      <tr key={m.month}>
                        <td>{monthLabel(m.month)}</td>
                        <td className="mono">{cost(m.cost_usd)}</td>
                        <td className="mono">{m.applied}</td>
                        <td className="mono">{m.per_application_usd === null ? '–' : cost(m.per_application_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
