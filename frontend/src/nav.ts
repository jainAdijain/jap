export type View = 'jobs' | 'profile' | 'prefs' | 'insights' | 'settings'
export const VIEWS: View[] = ['jobs', 'profile', 'prefs', 'insights', 'settings']

export const go = (v: View) => {
  window.location.hash = v
}
