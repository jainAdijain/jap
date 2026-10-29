import { useEffect, useState } from 'react'

import { api, type Ats, type Board } from '../api/client'

const ATS_LIST: { key: Ats; label: string; example: string }[] = [
  { key: 'greenhouse', label: 'Greenhouse', example: 'boards.greenhouse.io/<board>' },
  { key: 'lever', label: 'Lever', example: 'jobs.lever.co/<board>' },
  { key: 'ashby', label: 'Ashby', example: 'jobs.ashbyhq.com/<board>' },
]

export default function BoardsSection() {
  const [boards, setBoards] = useState<Board[]>([])
  const [ats, setAts] = useState<Ats>('greenhouse')
  const [token, setToken] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = async () => setBoards(await api.boards())
  useEffect(() => {
    load().catch((err) => setError(String(err)))
  }, [])

  const add = async () => {
    setError(null)
    try {
      await api.addBoard({ ats, board_token: token.trim(), display_name: name.trim() || token.trim() })
      setToken('')
      setName('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="card">
      <h2>Companies to watch</h2>
      <p className="hint">
        The job boards each run reads. Add a company by its board name, the last part of its
        careers URL. Pasting the whole URL arrives with ingestion.
      </p>
      <div className="ats-groups">
        {ATS_LIST.map(({ key, label, example }) => {
          const list = boards.filter((b) => b.ats === key)
          return (
            <div key={key} className="ats-group">
              <h3>
                {label} <span className="count">{list.length}</span>
              </h3>
              <p className="hint mono">{example}</p>
              {list.length === 0 ? (
                <p className="muted">None yet.</p>
              ) : (
                <ul className="chips">
                  {list.map((b) => (
                    <li key={b.id} className="chip" title={b.board_token}>
                      {b.display_name}
                      <button
                        type="button"
                        aria-label={`Stop watching ${b.display_name}`}
                        onClick={() => void api.removeBoard(b.id).then(load)}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
      <div className="add-row board-add">
        <label className="sr-only" htmlFor="board-ats">ATS</label>
        <select id="board-ats" value={ats} onChange={(e) => setAts(e.target.value as Ats)}>
          {ATS_LIST.map((a) => (
            <option key={a.key} value={a.key}>
              {a.label}
            </option>
          ))}
        </select>
        <label className="sr-only" htmlFor="board-token">Board name</label>
        <input id="board-token" placeholder="Board name, e.g. anthropic" value={token} onChange={(e) => setToken(e.target.value)} />
        <label className="sr-only" htmlFor="board-name">Company name</label>
        <input id="board-name" placeholder="Company name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="button" disabled={!token.trim()} onClick={() => void add()}>
          Add
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}
