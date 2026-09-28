import { useEffect, useRef, useState } from 'react'
import { XIcon } from 'lucide-react'
import { type Memory, type MemoryKind } from './api'
import { MemoryReviewPanel } from './MemoryReviewPanel'
import { Button } from '@/components/ui/button'

type Correction = { content: string; kind: MemoryKind; importance: number; reason: string }

export function MemoryInspectDialog({ memory, onClose, onCorrect, onSource, onError }: {
  memory: Memory
  onClose: () => void
  onCorrect: (data: Correction) => Promise<void>
  onSource: (sourceRef: string) => Promise<void>
  onError: (error: unknown) => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    dialog.showModal()
    const frame = window.requestAnimationFrame(() => dialog.querySelector<HTMLTextAreaElement>('#correction-content')?.focus())
    return () => { window.cancelAnimationFrame(frame); if (dialog.open) dialog.close() }
  }, [])

  return <dialog ref={dialogRef} className="memory-inspect-dialog" aria-labelledby="memory-inspect-title"
    onCancel={event => { event.preventDefault(); onClose() }}
    onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="memory-inspect-shell">
      <header className="memory-inspect-header">
        <div><span className="memory-inspect-eyebrow">记忆库 / 检查与纠正</span><h2 id="memory-inspect-title">检查并纠正记忆</h2><p>先核对当前内容；保存修改后，旧版本会留在历史中。</p></div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭记忆检查"><XIcon /></Button>
      </header>
      <div className="memory-inspect-body">
        {notice && <p className="memory-inspect-notice" role="status">{notice}</p>}
        <MemoryReviewPanel key={memory.id} memory={memory} onCorrect={async data => { await onCorrect(data); setNotice('纠正已保存，旧版本保留在下方时间线中。') }} onSource={onSource} onError={onError} />
      </div>
    </div>
  </dialog>
}
