const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type Check = { ok: boolean; detail: string }
export type Health = { ok: boolean; checks: Record<string, Check> }

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const resp = await fetch(`${API_URL}/api/health`, { signal })
  if (!resp.ok) throw new Error(`API answered ${resp.status}`)
  return (await resp.json()) as Health
}
