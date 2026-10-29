import { useEffect, useState } from 'react'

import { api, type Answer } from '../api/client'

export default function AnswerBank() {
  const [answers, setAnswers] = useState<Answer[]>([])
  const [editing, setEditing] = useState<Answer | null>(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = async () => setAnswers(await api.answers())
  useEffect(() => {
    load().catch((err) => setError(String(err)))
  }, [])

  const run = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setError(null)
    try {
      await fn()
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    }
  }

  return (
    <section className="card">
      <h2>Answer bank</h2>
      <p className="hint">Saved answers to form questions, such as notice period or work authorization. It also grows when you answer a Needs input question.</p>
      {answers.length > 0 && (
        <ul className="answers">
          {answers.map((a) =>
            editing?.id === a.id ? (
              <li key={a.id} className="answer editing">
                <input aria-label="Question" value={editing.question} onChange={(e) => setEditing({ ...editing, question: e.target.value })} />
                <textarea aria-label="Answer" rows={2} value={editing.answer} onChange={(e) => setEditing({ ...editing, answer: e.target.value })} />
                <span className="row-buttons">
                  <button type="button" className="primary" onClick={() => void run(() => api.editAnswer(editing)).then((ok) => ok && setEditing(null))}>
                    Save
                  </button>
                  <button type="button" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                </span>
              </li>
            ) : (
              <li key={a.id} className="answer">
                <span className="q">{a.question}</span>
                <span className="a">{a.answer}</span>
                <span className="row-buttons">
                  <button type="button" onClick={() => setEditing(a)}>
                    Edit
                  </button>
                  <button type="button" aria-label={`Remove answer to ${a.question}`} onClick={() => void run(() => api.removeAnswer(a.id))}>
                    Remove
                  </button>
                </span>
              </li>
            ),
          )}
        </ul>
      )}
      <div className="answer-add">
        <label htmlFor="new-q">Question</label>
        <input id="new-q" placeholder="e.g. What is your notice period?" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <label htmlFor="new-a">Answer</label>
        <textarea id="new-a" rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)} />
        <div>
          <button
            type="button"
            disabled={!question.trim() || !answer.trim()}
            onClick={() =>
              void run(() => api.addAnswer({ question, answer })).then((ok) => {
                if (!ok) return
                setQuestion('')
                setAnswer('')
              })
            }
          >
            Add answer
          </button>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}
