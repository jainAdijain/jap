import type { ListingSummary } from '../api/client'

const TOTALS: { key: keyof ListingSummary['totals']; label: string }[] = [
  { key: 'listings', label: 'Listings' },
  { key: 'applied', label: 'Applied' },
  { key: 'not_applied', label: 'Not applied' },
  { key: 'failed', label: 'Failed' },
  { key: 'needs_input', label: 'Needs input' },
]

type Props = { summary: ListingSummary | null; onRole: (role: string | null) => void }

/** Totals (U1) and Jobs by role (U2): one count per listing, however many boards post it. */
export default function Summary({ summary, onRole }: Props) {
  if (!summary) return null
  const { totals, by_role } = summary
  const roles = by_role.filter((r) => r.role !== null || r.listings > 0)
  return (
    <section className="card">
      <h2>Totals</h2>
      <p className="hint">Open jobs that match your profile, counted once each however many boards post them.</p>
      <ul className="totals">
        {TOTALS.map(({ key, label }) => (
          <li key={key}>
            <span className="total-n">{totals[key]}</span>
            <span className="total-label">{label}</span>
          </li>
        ))}
      </ul>
      <h3>Jobs by role</h3>
      {roles.length === 0 ? (
        <p className="muted">Add target roles in My profile to see counts per role.</p>
      ) : (
        <div className="table-wrap">
          <table className="jobs by-role">
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Listings</th>
                <th scope="col">Applied</th>
                <th scope="col">Not applied</th>
                <th scope="col">Failed</th>
                <th scope="col">Needs input</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((r) => (
                <tr key={r.role ?? 'other'}>
                  <td>
                    <button type="button" className="link" title="Show only these jobs" onClick={() => onRole(r.role)}>
                      {r.role ?? 'Other (matched by skills or location)'}
                    </button>
                  </td>
                  <td>{r.listings}</td>
                  <td>{r.applied}</td>
                  <td>{r.not_applied}</td>
                  <td>{r.failed}</td>
                  <td>{r.needs_input}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
