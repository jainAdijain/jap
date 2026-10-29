import type { Condition } from '../api/client'

const CONDITIONS: { key: Condition; label: string }[] = [
  { key: 'role', label: 'Role' },
  { key: 'skills', label: 'Skills' },
  { key: 'location', label: 'Location' },
]
const LABEL: Record<Condition, string> = { role: 'Role', skills: 'Skills', location: 'Location' }

const PRESETS: { label: string; rule: Condition[][] }[] = [
  { label: 'Any one (default)', rule: [['role'], ['skills'], ['location']] },
  { label: 'Role and Location', rule: [['role', 'location']] },
  { label: 'Role and Skills', rule: [['role', 'skills']] },
  { label: 'All three', rule: [['role', 'skills', 'location']] },
]

const same = (a: Condition[][], b: Condition[][]) =>
  JSON.stringify(a.map((r) => [...r].sort())) === JSON.stringify(b.map((r) => [...r].sort()))

/** "(Role and Location) or Skills": how the rule reads, rows joined by or. */
function ruleText(rule: Condition[][]): string {
  const rows = rule.filter((r) => r.length > 0)
  return rows
    .map((r) => {
      const words = CONDITIONS.filter((c) => r.includes(c.key)).map((c) => LABEL[c.key])
      const text = words.join(' and ')
      return rows.length > 1 && words.length > 1 ? `(${text})` : text
    })
    .join(' or ')
}

/** Rows that change nothing: another row already lists every job they would. */
function redundant(rule: Condition[][]): { row: number; by: number }[] {
  const out: { row: number; by: number }[] = []
  rule.forEach((r, i) => {
    if (r.length === 0) return
    const by = rule.findIndex(
      (o, j) => j !== i && o.length > 0 && o.every((c) => r.includes(c)) && (o.length < r.length || j < i),
    )
    if (by >= 0) out.push({ row: i, by })
  })
  return out
}

type Props = { rule: Condition[][]; onChange: (rule: Condition[][]) => void }

export default function MatchRule({ rule, onChange }: Props) {
  const toggle = (i: number, c: Condition) => {
    const combo = rule[i].includes(c) ? rule[i].filter((x) => x !== c) : [...rule[i], c]
    onChange(rule.map((r, j) => (j === i ? combo : r)))
  }
  const extra = redundant(rule)
  return (
    <fieldset className="field">
      <legend>Match rule</legend>
      <p className="hint">
        Conditions in one row must all match (and). The rows are alternatives (or): a job is listed
        when it meets any one row.
      </p>
      <div className="row-buttons presets" role="group" aria-label="Common rules">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            aria-pressed={same(rule, p.rule)}
            onClick={() => onChange(p.rule)}
          >
            {p.label}
          </button>
        ))}
      </div>
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
      <p className="rule-text" role="status">
        Lists a job that matches: <strong>{ruleText(rule) || 'nothing yet'}</strong>
      </p>
      {extra.map(({ row, by }) => (
        <p key={row} className="error">
          Row {row + 1} ({ruleText([rule[row]])}) doesn&apos;t narrow anything, because row {by + 1} (
          {ruleText([rule[by]])}) already lists every job it would. To require{' '}
          {ruleText([rule[row]])}, keep only that row, for example with the buttons above.
        </p>
      ))}
      <div className="row-buttons">
        <button type="button" onClick={() => onChange([...rule, ['role', 'location']])}>
          Add a row
        </button>
      </div>
    </fieldset>
  )
}
