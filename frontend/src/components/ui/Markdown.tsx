import type { CSSProperties, ReactNode } from 'react'

// Zero-dependency markdown renderer for the SUBSET the agent emits: headings
// (#/##/###), bullet + ordered lists, **bold**, *italic*, `inline code`,
// [text](url), and paragraphs (single newlines preserved). Builds React nodes
// — never dangerouslySetInnerHTML — so it's XSS-safe by construction. Designed
// to degrade gracefully on partial markdown during streaming (an unclosed
// `**` simply renders literally until its closer arrives).
//
// If we ever need full GFM (tables, task lists, nested blocks), swap this for
// react-markdown + remark-gfm — but the chat output doesn't need that today.

const codeStyle: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  fontSize: '0.88em',
  background: 'var(--bg-base)',
  border: '1px solid var(--bg-border)',
  borderRadius: 6,
  padding: '1px 5px',
}

// Earliest-match inline tokenizer: `code` | [text](url) | **bold** | *italic*.
// Code first so ** inside a code span isn't bolded. Unmatched markers fall
// through as literal text (streaming-safe).
const INLINE = /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)/g

function parseInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let i = 0
  let m: RegExpExecArray | null
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyPrefix}-${i++}`
    if (tok.startsWith('`')) {
      nodes.push(
        <code key={key} style={codeStyle}>
          {tok.slice(1, -1)}
        </code>,
      )
    } else if (tok.startsWith('[')) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok)
      if (link) {
        nodes.push(
          <a
            key={key}
            href={link[2]}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: 'var(--accent)', textDecoration: 'underline' }}
          >
            {link[1]}
          </a>,
        )
      } else {
        nodes.push(tok)
      }
    } else if (tok.startsWith('**')) {
      nodes.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    } else {
      nodes.push(<em key={key}>{tok.slice(1, -1)}</em>)
    }
    last = m.index + tok.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

const BLOCK_START = /^(#{1,3})\s|^[-*]\s|^\d+\.\s/

export function Markdown({ text, trailing }: { text: string; trailing?: ReactNode }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const trimmed = lines[i].trim()

    if (trimmed === '') {
      i++
      continue
    }

    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed)
    if (heading) {
      const level = heading[1].length
      const size = level === 1 ? 18 : level === 2 ? 16 : 15
      blocks.push(
        <div key={key} style={{ fontWeight: 700, fontSize: size, margin: '10px 0 4px' }}>
          {parseInline(heading[2], `h${key}`)}
        </div>,
      )
      key++
      i++
      continue
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: ReactNode[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        const content = lines[i].trim().replace(/^[-*]\s+/, '')
        items.push(
          <li key={items.length} style={{ margin: '2px 0' }}>
            {parseInline(content, `li${key}-${items.length}`)}
          </li>,
        )
        i++
      }
      blocks.push(
        <ul key={key} style={{ margin: '4px 0', paddingLeft: 20 }}>
          {items}
        </ul>,
      )
      key++
      continue
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: ReactNode[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        const content = lines[i].trim().replace(/^\d+\.\s+/, '')
        items.push(
          <li key={items.length} style={{ margin: '2px 0' }}>
            {parseInline(content, `ol${key}-${items.length}`)}
          </li>,
        )
        i++
      }
      blocks.push(
        <ol key={key} style={{ margin: '4px 0', paddingLeft: 22 }}>
          {items}
        </ol>,
      )
      key++
      continue
    }

    // Paragraph: gather consecutive plain lines (single newlines kept as <br>
    // via pre-wrap) until a blank line or a block-level starter.
    const para: string[] = []
    while (i < lines.length && lines[i].trim() !== '' && !BLOCK_START.test(lines[i].trim())) {
      para.push(lines[i])
      i++
    }
    blocks.push(
      <p key={key} style={{ margin: '4px 0', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
        {parseInline(para.join('\n'), `p${key}`)}
      </p>,
    )
    key++
  }

  return (
    <div>
      {blocks}
      {trailing}
    </div>
  )
}
