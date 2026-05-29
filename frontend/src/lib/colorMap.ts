/** djb2 hash for a string → integer */
function djb2(s: string): number {
  let h = 5381
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) ^ s.charCodeAt(i)
  }
  return h >>> 0
}

/** Curated palette visible on dark backgrounds */
const PALETTE = [
  '#6366f1', // Indigo  – generic
  '#8b5cf6', // Violet  – editors
  '#06b6d4', // Cyan    – browsers
  '#10b981', // Emerald – terminals
  '#f59e0b', // Amber   – documents
  '#ef4444', // Red     – communication
  '#ec4899', // Pink    – social
  '#14b8a6', // Teal    – media
  '#84cc16', // Lime    – notes
  '#f97316', // Orange  – design
  '#a78bfa', // Purple  – IDE
  '#38bdf8', // Sky     – cloud
]

/** Fixed colors for well-known apps */
const KNOWN: Record<string, string> = {
  'Visual Studio Code': '#8b5cf6',
  'Code': '#8b5cf6',
  'Cursor': '#8b5cf6',
  'Windsurf': '#8b5cf6',
  'Google Chrome': '#06b6d4',
  'Microsoft Edge': '#06b6d4',
  'Firefox': '#f97316',
  'Brave': '#f97316',
  'Windows Terminal': '#10b981',
  'PowerShell': '#10b981',
  'Command Prompt': '#10b981',
  'WeChat': '#84cc16',
  'Slack': '#ec4899',
  'Discord': '#ec4899',
  'Notion': '#f59e0b',
  'Obsidian': '#f59e0b',
  'Python': '#06b6d4',
}

export function getAppColor(appName: string): string {
  return KNOWN[appName] ?? PALETTE[djb2(appName) % PALETTE.length]
}
