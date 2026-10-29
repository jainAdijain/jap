import { useState } from 'react'

type Props = {
  title: string
  hint?: string
  placeholder: string
  items: string[]
  onChange: (items: string[]) => void
}

export default function ChipList({ title, hint, placeholder, items, onChange }: Props) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const value = draft.trim()
    if (value && !items.some((i) => i.toLowerCase() === value.toLowerCase())) {
      onChange([...items, value])
    }
    setDraft('')
  }
  const id = title.toLowerCase().replace(/\W+/g, '-')
  return (
    <div className="field">
      <div className="field-head">
        <label htmlFor={id}>{title}</label>
        <span className="count">{items.length} added</span>
      </div>
      {hint && <p className="hint">{hint}</p>}
      <ul className="chips">
        {items.map((item) => (
          <li key={item} className="chip">
            {item}
            <button
              type="button"
              aria-label={`Remove ${item}`}
              onClick={() => onChange(items.filter((i) => i !== item))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="add-row">
        <input
          id={id}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <button type="button" onClick={add}>
          Add
        </button>
      </div>
    </div>
  )
}
