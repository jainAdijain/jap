import type { Ats } from '../api/client'
import { atsLabel } from '../format'

/** One badge per board the job is on; the count badge shows when it is on more than one. */
export default function Platforms({ list }: { list: Ats[] }) {
  return (
    <span className="platforms">
      {list.length > 1 && (
        <span className="badge platform-count" title={`On ${list.length} boards`}>
          {list.length}
        </span>
      )}
      {list.map((a) => (
        <span key={a} className={`ats ats-${a}`}>
          {atsLabel(a)}
        </span>
      ))}
    </span>
  )
}
