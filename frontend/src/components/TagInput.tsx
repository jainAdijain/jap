import { useState } from 'react'

type Props = {
  label: string // for screen readers: "Add a job title"
  placeholder: string
  items: string[]
  onChange: (items: string[]) => void
  /** Tags shown first that cannot be removed, such as "Anywhere (always)". */
  fixed?: string[]
  lower?: boolean
}

/** Tags in a box with a text input at the end: Enter or comma adds, × or Backspace removes. */
export default function TagInput({ label, placeholder, items, onChange, fixed = [], lower = false }: Props) {
  const [draft, setDraft] = useState('')
  const add = (raw: string) => {
    const values = raw
      .split(',')
      .map((v) => (lower ? v.trim().toLowerCase() : v.trim()))
      .filter(Boolean)
    const next = [...items]
    for (const v of values) if (!next.some((i) => i.toLowerCase() === v.toLowerCase())) next.push(v)
    if (next.length !== items.length) onChange(next)
    setDraft('')
  }
  return (
    <div className="tags">
      {fixed.map((t) => (
        <span key={t} className="tag fixed" title="Always included">
          {t}
        </span>
      ))}
      {items.map((t) => (
        <span key={t} className="tag">
          {t}
          <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(items.filter((i) => i !== t))}>
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        aria-label={label}
        placeholder={placeholder}
        value={draft}
        onChange={(e) => (e.target.value.endsWith(',') ? add(e.target.value) : setDraft(e.target.value))}
        onBlur={() => draft.trim() && add(draft)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            add(draft)
          } else if (e.key === 'Backspace' && !draft && items.length) {
            onChange(items.slice(0, -1))
          }
        }}
      />
    </div>
  )
}
