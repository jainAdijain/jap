import type { Extracted, Salary } from '../api/client'

const money = (n: number, currency: string | null) =>
  new Intl.NumberFormat(undefined, {
    style: currency ? 'currency' : 'decimal',
    currency: currency ?? undefined,
    maximumFractionDigits: 0,
  }).format(n)

function salaryText(s: Salary | undefined): string | null {
  if (!s || (s.min == null && s.max == null)) return null
  const range = [s.min, s.max].filter((v): v is number => v != null).map((v) => money(v, s.currency)).join(' – ')
  return s.period ? `${range} a ${s.period}` : range
}

export default function JobFields({ f }: { f: Extracted }) {
  const rows: [string, string | null, boolean][] = [
    ['Seniority', f.seniority ?? null, false],
    ['Work mode', f.work_mode ?? null, f.direct.includes('work_mode')],
    ['Salary', salaryText(f.salary), f.direct.includes('salary')],
    ['Remote regions', f.remote_regions?.length ? f.remote_regions.join(', ') : null, false],
    ['Visa sponsorship', f.visa_sponsorship ?? null, false],
    ['Cover letter asked', f.cover_letter_asked == null ? null : f.cover_letter_asked ? 'Yes' : 'No', false],
  ]
  return (
    <>
      <dl className="facts">
        {rows.map(([label, value, direct]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {value && value !== 'unknown' ? value : <span className="muted">Not stated</span>}
              {direct && <span className="hint"> (from the board)</span>}
            </dd>
          </div>
        ))}
      </dl>
      <h3>Skills</h3>
      {f.skills?.length ? (
        <ul className="chips">
          {f.skills.map((s) => (
            <li key={s} className="chip">
              {s}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None found.</p>
      )}
    </>
  )
}
