import { useState } from 'react'
import { Check, Pencil } from 'lucide-react'
import type { UseMutationResult } from '@tanstack/react-query'
import type { ApiRecord, FeedbackRequest, FeedbackResponse } from '@/types/api'
import { useCategories } from '@/hooks/useCategories'

interface FeedbackControlsProps {
  record: ApiRecord
  feedback: UseMutationResult<FeedbackResponse, Error, FeedbackRequest>
}

export function FeedbackControls({ record, feedback }: FeedbackControlsProps) {
  const [isEditing, setIsEditing] = useState(false)
  const { data: categoriesData } = useCategories()
  const categories = categoriesData?.categories ?? []

  const handleConfirm = () => {
    // 确认当前分类（无变化）
    feedback.mutate({
      record_id: record.id,
      category: record.category_final!,
      action: 'confirm',
    })
  }

  const handleSelect = (category: string) => {
    feedback.mutate(
      {
        record_id: record.id,
        category,
        action: 'edit',
      },
      {
        onSuccess: () => setIsEditing(false),
      }
    )
  }

  if (isEditing) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          选择新分类：
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {categories.map((cat) => (
            <button
              key={cat.id}
              onClick={() => handleSelect(cat.id)}
              disabled={feedback.isPending}
              aria-pressed={record.category_final === cat.id}
              style={{
                padding: '5px 11px',
                fontSize: 11,
                borderRadius: 'var(--radius-pill)',
                border: '1px solid var(--bg-border)',
                background:
                  record.category_final === cat.id
                    ? 'var(--grad-accent)'
                    : 'var(--bg-raised)',
                color:
                  record.category_final === cat.id
                    ? 'var(--accent-contrast)'
                    : 'var(--text-secondary)',
                cursor: feedback.isPending ? 'not-allowed' : 'pointer',
                opacity: feedback.isPending ? 0.6 : 1,
              }}
            >
              {cat.name}
            </button>
          ))}
        </div>
        <button
          onClick={() => setIsEditing(false)}
          disabled={feedback.isPending}
          style={{
            marginTop: 4,
            padding: '4px 10px',
            fontSize: 11,
            borderRadius: 'var(--radius-md)',
            border: 'none',
            background: 'transparent',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            alignSelf: 'flex-start',
          }}
        >
          取消
        </button>
      </div>
    )
  }

  if (record.category_final) {
    return (
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={handleConfirm}
          disabled={feedback.isPending}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '4px 10px',
            fontSize: 11,
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--bg-border)',
            background: 'var(--bg-raised)',
            color: 'var(--text-secondary)',
            cursor: feedback.isPending ? 'not-allowed' : 'pointer',
            opacity: feedback.isPending ? 0.6 : 1,
          }}
        >
          <Check size={12} aria-hidden="true" />
          确认分类
        </button>
        <button
          onClick={() => setIsEditing(true)}
          disabled={feedback.isPending}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '4px 10px',
            fontSize: 11,
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--bg-border)',
            background: 'var(--bg-raised)',
            color: 'var(--text-secondary)',
            cursor: feedback.isPending ? 'not-allowed' : 'pointer',
            opacity: feedback.isPending ? 0.6 : 1,
          }}
        >
          <Pencil size={12} aria-hidden="true" />
          修改分类
        </button>
      </div>
    )
  }

  // 未分类时直接显示编辑
  return (
    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
      请为此活动选择分类：
      <div style={{ marginTop: 8 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {categories.map((cat) => (
            <button
              key={cat.id}
              onClick={() => handleSelect(cat.id)}
              disabled={feedback.isPending}
              style={{
                padding: '4px 10px',
                fontSize: 11,
                borderRadius: 'var(--radius-pill)',
                border: '1px solid var(--bg-border)',
                background: 'var(--bg-raised)',
                color: 'var(--text-secondary)',
                cursor: feedback.isPending ? 'not-allowed' : 'pointer',
                opacity: feedback.isPending ? 0.6 : 1,
              }}
            >
              {cat.name}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
