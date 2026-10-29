type Props = { name: string; ok: boolean | null; detail: string }

export default function StatusRow({ name, ok, detail }: Props) {
  const state = ok === null ? 'pending' : ok ? 'ok' : 'down'
  const label = ok === null ? 'Checking' : ok ? 'OK' : 'Not reachable'
  return (
    <li className={`status-row ${state}`}>
      <span className="dot" aria-hidden="true" />
      <span className="name">{name}</span>
      <span className="label">{label}</span>
      <span className="detail">{detail}</span>
    </li>
  )
}
