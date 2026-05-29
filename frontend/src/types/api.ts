/** Single activity record returned by GET /v1/records */
export interface ApiRecord {
  id: string
  ts_start: number        // epoch ms
  ts_end: number | null
  app_name: string
  process_name: string
  window_title: string
  url: string | null
  capture_reason: string | null
  status: string
  created_at: number
  updated_at: number
  // from analysis_results
  vlm_desc: string | null
  category_final: string | null
  confidence: number | null
  // from screenshots subquery
  thumb_path: string | null
  screenshot_count: number
}

export interface RecordsResponse {
  items: ApiRecord[]
  next_cursor: string | null
}

/** Single record detail with screenshots list (GET /v1/records/{id}) */
export interface ApiRecordDetail extends ApiRecord {
  analysis_status: string | null
  screenshots: ApiScreenshot[]
}

export interface ApiScreenshot {
  id: string
  record_id: string
  path: string
  thumb_path: string | null
  width: number | null
  height: number | null
  hash_sha256: string | null
  privacy_level: string
  created_at: number
}

export interface ApiCategory {
  id: string
  name: string
  parent_id: string | null
  description: string | null
  is_builtin: number
  is_hidden: number
}

export interface CategoriesResponse {
  categories: ApiCategory[]
}

export interface FeedbackRequest {
  record_id: string
  action: 'confirm' | 'edit'
  category?: string
  tags?: string[]
  user_note?: string
}

export interface FeedbackResponse {
  status: string
  record_id: string
  feedback_id: string
}

export interface RuntimeInfo {
  version: string
  data_dir: string
  api_host: string
  api_port: number
}

export interface ApiApp {
  name: string
  count: number
}

export interface AppsResponse {
  items: ApiApp[]
}

/** Per-result explanation of why a screenshot matched the search query. */
export interface SearchMatchInfo {
  visual_distance: number | null
  semantic_rank: number | null
  text_rank: number | null
  rrf_score: number
  reasons: string[]
}

/** Single item returned by POST /v1/search/by-image */
export interface SearchResultItem {
  screenshot_id: string
  record_id: string
  ts_start: number
  ts_end: number | null
  app_name: string
  window_title: string
  url: string | null
  thumb_path: string | null
  vlm_desc: string | null
  category_final: string | null
  match: SearchMatchInfo
}

export type ChannelStatus = 'ok' | 'disabled' | 'unavailable'

export interface SearchByImageResponse {
  items: SearchResultItem[]
  total: number
  visual_channel: ChannelStatus
  semantic_channel: ChannelStatus
}

/** Auth — the cookie-session principal returned by /v1/auth/me. */
export interface AuthMe {
  username: string
  must_change_password: boolean
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  username: string
  must_change_password: boolean
}

export interface ChangePasswordRequest {
  old_password: string
  new_password: string
}

/** Admin tokens — bearer tokens for MCP / capture clients. */
export interface TokenSummary {
  label: string
  created_at: number | null
}

export interface TokenCreated {
  label: string
  value: string
  created_at: number | null
}
