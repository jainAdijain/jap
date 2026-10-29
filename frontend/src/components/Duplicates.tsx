import { useState } from 'react'

import { api, type Duplicate } from '../api/client'
import Platforms from './Platforms'

const pct = (x: number) => `${Math.round(x * 100)}%`

export default function Duplicates({ pairs, onChanged }: { pairs: Duplicate[]; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  if (pairs.length === 0) return null

  const decide = async (id: string, decision: 'merged' | 'kept_apart') => {
    setError(null)
    try {
      await api.decide(id, decision)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="card">
      <h2>
        Possible duplicates <span className="count">{pairs.length}</span>
      </h2>
      <p className="hint">These look close but not close enough to merge on their own. Your choice is remembered.</p>
      <ul className="dupes">
        {pairs.map((d) => (
          <li key={d.id}>
            <div className="dupe-sides">
              <span className="muted">{d.company}</span>
              {[d.a, d.b].map((s) => (
                <div key={s.id}>
                  {s.title} <Platforms list={s.platforms} />
                  <div className="hint">{s.locations.join(' · ') || 'No location given'}</div>
                </div>
              ))}
              <span className="hint">
                Titles {pct(d.title_similarity)} alike, descriptions {pct(d.description_similarity)} alike
              </span>
            </div>
            <span className="row-buttons">
              <button type="button" onClick={() => void decide(d.id, 'merged')}>
                Merge
              </button>
              <button type="button" onClick={() => void decide(d.id, 'kept_apart')}>
                Keep apart
              </button>
            </span>
          </li>
        ))}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}
