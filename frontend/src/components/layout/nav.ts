import { Clock, Search, Settings, MessageCircle, LayoutDashboard, ScrollText, Layers, Cpu } from 'lucide-react'

/** Single source of truth for the app navigation: the Sidebar renders these
 *  as links and the TopBar looks the current path up for its title. They were
 *  previously two separate maps and had already drifted (TopBar was missing
 *  /pyramid, /audit, /llm-log and fell back to the wrong title). */
export const NAV_ITEMS = [
  { to: '/', icon: Clock, label: '时间轴' },
  { to: '/agent', icon: MessageCircle, label: '问问' },
  { to: '/dashboard', icon: LayoutDashboard, label: '看板' },
  { to: '/pyramid', icon: Layers, label: '金字塔' },
  { to: '/audit', icon: ScrollText, label: '日志' },
  { to: '/llm-log', icon: Cpu, label: 'LLM' },
  { to: '/search', icon: Search, label: '搜索' },
  { to: '/settings', icon: Settings, label: '设置' },
] as const

export function navTitleFor(pathname: string) {
  return NAV_ITEMS.find((item) => item.to === pathname) ?? NAV_ITEMS[0]
}
