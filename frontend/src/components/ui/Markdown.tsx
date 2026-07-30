import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTheme } from '@/contexts/theme'

// Markdown renderer for the agent chat. Uses react-markdown + remark-gfm so we
// get full GFM — tables, task lists, strikethrough, nested blocks — which the
// agent emits routinely (e.g. an app-usage ranking table). XSS-safe by default:
// react-markdown does NOT render raw HTML (no rehype-raw), so any embedded HTML
// is escaped. ```mermaid``` fences render as diagrams via a lazily-imported
// mermaid (kept out of the main bundle); anything else degrades gracefully
// while streaming (a half-arrived table/fence just shows as text until closed).

const mono = 'var(--font-mono)'

const inlineCode: CSSProperties = {
  fontFamily: mono,
  fontSize: '0.88em',
  background: 'var(--bg-base)',
  border: '1px solid var(--bg-border)',
  borderRadius: 6,
  padding: '1px 5px',
}
const preStyle: CSSProperties = {
  margin: '8px 0',
  padding: '10px 12px',
  background: 'var(--bg-base)',
  border: '1px solid var(--bg-border)',
  borderRadius: 'var(--radius-md)',
  overflowX: 'auto',
  fontSize: 12.5,
  lineHeight: 1.5,
}
const blockCode: CSSProperties = { fontFamily: mono, whiteSpace: 'pre', background: 'none', border: 'none', padding: 0 }
const tableStyle: CSSProperties = { borderCollapse: 'collapse', fontSize: 13, width: 'max-content', minWidth: '100%' }
const cellStyle: CSSProperties = {
  border: '1px solid var(--bg-border)',
  padding: '6px 10px',
  textAlign: 'left',
  verticalAlign: 'top',
}
const thStyle: CSSProperties = { ...cellStyle, background: 'var(--bg-surface)', fontWeight: 600 }
const headingStyle = (size: number): CSSProperties => ({ fontWeight: 700, fontSize: size, margin: '10px 0 4px' })

// ---- mermaid: lazily imported so it lands in its own chunk, not the main JS --
let mermaidModule: Promise<typeof import('mermaid')> | null = null
function loadMermaid() {
  if (!mermaidModule) mermaidModule = import('mermaid')
  return mermaidModule
}
let mermaidSeq = 0

function MermaidBlock({ chart }: { chart: string }) {
  const { theme } = useTheme()
  const [svg, setSvg] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setSvg(null)
    loadMermaid()
      .then(async ({ default: mermaid }) => {
        // securityLevel:'strict' sanitizes labels — the chart text comes from
        // the LLM, so we don't trust it to be markup-free.
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: theme === 'dark' ? 'dark' : 'default',
        })
        const out = await mermaid.render(`mmd-${mermaidSeq++}`, chart)
        if (!cancelled) setSvg(out.svg)
      })
      .catch(() => {
        // Incomplete (still streaming) or invalid diagram → fall back to source.
        if (!cancelled) setSvg(null)
      })
    return () => {
      cancelled = true
    }
  }, [chart, theme])

  if (svg === null) {
    return (
      <pre style={preStyle}>
        <code style={blockCode}>{chart}</code>
      </pre>
    )
  }
  return <div style={{ margin: '8px 0', textAlign: 'center' }} dangerouslySetInnerHTML={{ __html: svg }} />
}

const components: Components = {
  // react-markdown wraps fenced blocks in <pre><code>; we passthrough <pre> and
  // let <code> decide (mermaid diagram / styled block / inline span) so a mermaid
  // diagram isn't trapped inside a <pre>.
  pre: ({ children }) => <>{children}</>,
  code({ className, children }) {
    const lang = /language-(\w+)/.exec(className || '')?.[1]
    const raw = String(children ?? '')
    const content = raw.replace(/\n$/, '')
    if (lang === 'mermaid') return <MermaidBlock chart={content} />
    if (lang || raw.includes('\n')) {
      return (
        <pre style={preStyle}>
          <code style={blockCode}>{content}</code>
        </pre>
      )
    }
    return <code style={inlineCode}>{children}</code>
  },
  table: ({ children }) => (
    <div style={{ overflowX: 'auto', margin: '8px 0' }}>
      <table style={tableStyle}>{children}</table>
    </div>
  ),
  th: ({ children }) => <th style={thStyle}>{children}</th>,
  td: ({ children }) => <td style={cellStyle}>{children}</td>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{ color: 'var(--accent)', textDecoration: 'underline' }}
    >
      {children}
    </a>
  ),
  ul: ({ children }) => <ul style={{ margin: '4px 0', paddingLeft: 20 }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '4px 0', paddingLeft: 22 }}>{children}</ol>,
  li: ({ children }) => <li style={{ margin: '2px 0' }}>{children}</li>,
  p: ({ children }) => <p style={{ margin: '4px 0', lineHeight: 1.6 }}>{children}</p>,
  h1: ({ children }) => <div style={headingStyle(18)}>{children}</div>,
  h2: ({ children }) => <div style={headingStyle(16)}>{children}</div>,
  h3: ({ children }) => <div style={headingStyle(15)}>{children}</div>,
  blockquote: ({ children }) => (
    <blockquote
      style={{ borderLeft: '3px solid var(--bg-border)', paddingLeft: 10, margin: '6px 0', color: 'var(--text-secondary)' }}
    >
      {children}
    </blockquote>
  ),
  hr: () => <hr style={{ border: 'none', borderTop: '1px solid var(--bg-border)', margin: '10px 0' }} />,
}

export function Markdown({ text, trailing }: { text: string; trailing?: ReactNode }) {
  return (
    <div style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
      {trailing}
    </div>
  )
}
