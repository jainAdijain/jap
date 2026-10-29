import type { Condition } from '../api/client'

const CONDITIONS: { key: Condition; label: string }[] = [
  { key: 'role', label: 'Role' },
  { key: 'skills', label: 'Skills' },
  { key: 'location', label: 'Location' },
]

type Props = { rule: Condition[][]; onChange: (rule: Condition[][]) => void }

export default function MatchRule({ rule, onChange }: Props) {
  const toggle = (i: number, c: Condition) => {
    const combo = rule[i].includes(c) ? rule[i].filter((x) => x !== c) : [...rule[i], c]
    onChange(rule.map((r, j) => (j === i ? combo : r)))
  }
  return (
    <fieldset className="field">
      <legend>Match rule</legend>
      <p className="hint">
        A job is listed when it meets every condition in at least one row. The default lists a job
        that matches any one of Role, Skills or Location.
      </p>
      <ol className="rule">
        {rule.map((combo, i) => (
          <li key={i}>
            {i > 0 && <span className="or">or</span>}
            <span className="combo">
              {CONDITIONS.map(({ key, label }) => (
                <label key={key} className="check">
                  <input type="checkbox" checked={combo.includes(key)} onChange={() => toggle(i, key)} />
                  {label}
                </label>
              ))}
            </span>
            {rule.length > 1 && (
              <button type="button" aria-label={`Remove row ${i + 1}`} onClick={() => onChange(rule.filter((_, j) => j !== i))}>
                Remove
              </button>
            )}
          </li>
        ))}
      </ol>
      <div className="row-buttons">
        <button type="button" onClick={() => onChange([...rule, ['role', 'location']])}>
          Add a row
        </button>
        <button type="button" onClick={() => onChange([['role'], ['skills'], ['location']])}>
          Reset to default
        </button>
      </div>
    </fieldset>
  )
}
