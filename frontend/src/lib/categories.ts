/** Map an activity category to a stable accent color. Colors are CSS-var
 *  references so they re-theme automatically (see index.css --cat-*). Known
 *  categories get a curated hue; unknown ones get a stable hash pick so the
 *  same category always renders the same color. */

const KNOWN: Record<string, string> = {
  work: 'var(--cat-blue)',
  development: 'var(--cat-purple)',
  dev: 'var(--cat-purple)',
  coding: 'var(--cat-purple)',
  communication: 'var(--cat-cyan)',
  social: 'var(--cat-pink)',
  productivity: 'var(--cat-green)',
  study: 'var(--cat-green)',
  learning: 'var(--cat-green)',
  entertainment: 'var(--cat-pink)',
  media: 'var(--cat-orange)',
  game: 'var(--cat-purple)',
  gaming: 'var(--cat-purple)',
  browsing: 'var(--cat-cyan)',
  reading: 'var(--cat-amber)',
  life: 'var(--cat-orange)',
  design: 'var(--cat-orange)',
  other: 'var(--cat-slate)',
  uncategorized: 'var(--cat-slate)',
}

const FALLBACK = [
  'var(--cat-blue)',
  'var(--cat-green)',
  'var(--cat-orange)',
  'var(--cat-pink)',
  'var(--cat-purple)',
  'var(--cat-cyan)',
  'var(--cat-amber)',
]

function hash(s: string): number {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h) ^ s.charCodeAt(i)
  return h >>> 0
}

export function categoryColor(category?: string | null): string {
  if (!category) return 'var(--cat-slate)'
  const key = category.toLowerCase().trim()
  return KNOWN[key] ?? FALLBACK[hash(key) % FALLBACK.length]
}
