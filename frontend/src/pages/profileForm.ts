import { useEffect, useState } from 'react'

import { api, type Profile, type ProfileFields } from '../api/client'
import { errorText } from '../format'
import { useApp } from '../appContext'

export const fields = (p: Profile): ProfileFields => ({
  full_name: p.full_name,
  email: p.email,
  phone: p.phone,
  address: p.address,
  links: p.links,
  target_roles: p.target_roles,
  must_have_skills: p.must_have_skills,
  nice_to_have_skills: p.nice_to_have_skills,
  locations: p.locations,
  remote_regions: p.remote_regions,
  remote_any: p.remote_any,
  match_rule: p.match_rule,
  work_modes: p.work_modes,
  excluded_companies: p.excluded_companies,
  excluded_title_words: p.excluded_title_words,
  tone_note: p.tone_note,
})

// Work modes and match rule combinations compare as sets, so re-ticking one counts as no change.
const comparable = (f: ProfileFields) =>
  JSON.stringify({ ...f, work_modes: [...f.work_modes].sort(), match_rule: f.match_rule.map((c) => [...c].sort()).sort() })

/** Profile and Job preferences both edit the one profile: a local copy, saved with PUT. */
export function useProfileForm() {
  const { profile, setProfile, refresh } = useApp()
  const [form, setForm] = useState<ProfileFields | null>(profile ? fields(profile) : null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (profile && !form) setForm(fields(profile))
  }, [profile, form])

  const dirty = !!form && !!profile && comparable(form) !== comparable(fields(profile))
  const set = <K extends keyof ProfileFields>(key: K, value: ProfileFields[K]) => {
    if (!form) return
    setForm({ ...form, [key]: value })
    setError(null)
  }
  const save = async (): Promise<number | null> => {
    if (!form) return null
    setSaving(true)
    setError(null)
    try {
      const saved = await api.saveProfile(form)
      setProfile(saved)
      setForm(fields(saved))
      void refresh()
      return saved.matching_count
    } catch (err) {
      setError(errorText(err))
      return null
    } finally {
      setSaving(false)
    }
  }
  const discard = () => {
    if (profile) setForm(fields(profile))
    setError(null)
  }
  return { profile, form, set, setForm, dirty, saving, error, save, discard }
}
