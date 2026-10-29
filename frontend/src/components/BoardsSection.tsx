import { useEffect, useState } from 'react'

import { api, type Board, type Detected } from '../api/client'
import { ATS_LIST, atsLabel } from '../format'

export default function BoardsSection() {
  const [boards, setBoards] = useState<Board[]>([])
  const [text, setText] = useState('')
  const [found, setFound] = useState<Detected | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => setBoards(await api.boards())
  useEffect(() => {
    load().catch((err) => setError(String(err)))
  }, [])

  const detect = async () => {
    setError(null)
    setFound(null)
    setBusy(true)
    try {
      const d = await api.detectBoard(text.trim())
      setFound(d)
      setName(d.display_name)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    if (!found) return
    setError(null)
    try {
      await api.addBoard({ ats: found.ats, board_token: found.board_token, display_name: name.trim() || found.display_name })
      setText('')
      setFound(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="card">
      <h2>Companies to watch</h2>
      <p className="hint">
        The job boards each run reads. Paste a company's Greenhouse, Lever or Ashby board URL, or
        type its name, and JAP finds the board and checks it answers.
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
                    <li
                      key={b.id}
                      className={b.last_ok === false ? 'chip chip-warn' : 'chip'}
                      title={b.last_ok === false ? `${b.board_token}: not found at the last run` : b.board_token}
                    >
                      {b.display_name}
                      {b.last_ok === false && <span className="sr-only"> (not found at the last run)</span>}
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
      <form
        className="add-row board-add"
        onSubmit={(e) => {
          e.preventDefault()
          void detect()
        }}
      >
        <label className="sr-only" htmlFor="board-text">Board URL or company name</label>
        <input
          id="board-text"
          placeholder="https://jobs.lever.co/spotify or Spotify"
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setFound(null)
          }}
        />
        <button type="submit" disabled={!text.trim() || busy}>
          {busy ? 'Checking…' : 'Find board'}
        </button>
      </form>
      {found && (
        <div className="detected" role="status">
          <p>
            Found <strong>{atsLabel(found.ats)}</strong> board <span className="mono">{found.board_token}</span> with{' '}
            {found.job_count} open job{found.job_count === 1 ? '' : 's'}.
            {found.already_watched && ' You already watch it.'}
          </p>
          {!found.already_watched && (
            <div className="add-row">
              <label className="sr-only" htmlFor="board-name">Company name</label>
              <input id="board-name" value={name} onChange={(e) => setName(e.target.value)} />
              <button type="button" className="primary" onClick={() => void add()}>
                Watch {name.trim() || found.display_name}
              </button>
            </div>
          )}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}
