import { useState } from 'react'
import { CheckIcon, ExternalLinkIcon, ListChecksIcon, XIcon } from 'lucide-react'
import { type MemoryKind, type MemoryReview } from './api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'

type Draft = { content: string; kind: MemoryKind; importance: number }
const kinds: MemoryKind[] = ['fact', 'preference', 'profile', 'goal', 'procedure']

function ReviewItem({ item, onApprove, onReject, onSource, onError }: {
  item: MemoryReview
  onApprove: (id: string, data: Draft) => Promise<void>
  onReject: (id: string) => Promise<void>
  onSource: (sourceRef: string) => Promise<void>
  onError: (error: unknown) => void
}) {
  const [draft, setDraft] = useState<Draft>({ content: item.content, kind: item.kind, importance: item.importance })
  const [busy, setBusy] = useState(false)
  const [confirmReject, setConfirmReject] = useState(false)
  const approve = async () => {
    if (!draft.content.trim() || busy) return
    setBusy(true)
    try { await onApprove(item.id, { ...draft, content: draft.content.trim() }) }
    catch (error) { onError(error) }
    finally { setBusy(false) }
  }
  const reject = async () => {
    setBusy(true)
    try { await onReject(item.id) }
    catch (error) { onError(error); setBusy(false); setConfirmReject(false) }
  }

  return <Card className="review-item" data-testid="memory-review-item">
    <CardHeader><CardTitle className="review-item-heading"><Badge variant="secondary">待审核 · {item.kind}</Badge><Button variant="ghost" size="sm" onClick={() => onSource(item.source_ref)}><ExternalLinkIcon data-icon="inline-start" />查看原始对话</Button></CardTitle></CardHeader>
    <CardContent>
      <button type="button" className="source-ref-link" title={item.source_ref} onClick={() => onSource(item.source_ref)}>来源 ID：{item.source_ref}</button>
      <Field><FieldLabel htmlFor={`review-content-${item.id}`}>候选内容</FieldLabel><Textarea id={`review-content-${item.id}`} maxLength={4000} value={draft.content} onChange={event => setDraft({ ...draft, content: event.target.value })} /></Field>
      <div className="review-item-fields">
        <Field><FieldLabel>类型</FieldLabel><Select value={draft.kind} onValueChange={value => setDraft({ ...draft, kind: value as MemoryKind })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{kinds.map(kind => <SelectItem key={kind} value={kind}>{kind}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
        <Field><FieldLabel htmlFor={`review-importance-${item.id}`}>重要度：{draft.importance}</FieldLabel><Input id={`review-importance-${item.id}`} type="range" min="1" max="5" value={draft.importance} onChange={event => setDraft({ ...draft, importance: Number(event.target.value) })} /></Field>
      </div>
      <div className="review-item-actions">
        {confirmReject ? <><span className="review-confirm">确认丢弃这条候选？</span><Button variant="outline" size="sm" onClick={() => setConfirmReject(false)} disabled={busy}>取消</Button><Button variant="destructive" size="sm" onClick={reject} disabled={busy}><XIcon data-icon="inline-start" />确认拒绝</Button></> : <><Button variant="ghost" size="sm" onClick={() => setConfirmReject(true)} disabled={busy}>拒绝</Button><Button size="sm" onClick={approve} disabled={busy || !draft.content.trim()}>{busy ? <Spinner data-icon="inline-start" /> : <CheckIcon data-icon="inline-start" />}批准并写入</Button></>}
      </div>
    </CardContent>
  </Card>
}

export function MemoryReviewQueue({ items, onApprove, onReject, onSource, onError }: {
  items: MemoryReview[]
  onApprove: (id: string, data: Draft) => Promise<void>
  onReject: (id: string) => Promise<void>
  onSource: (sourceRef: string) => Promise<void>
  onError: (error: unknown) => void
}) {
  return <Card data-testid="memory-review-queue">
    <CardHeader><CardTitle className="review-queue-title"><span><ListChecksIcon />自动记忆审核</span><Badge>{items.length} 条待审核</Badge></CardTitle><p className="text-sm text-muted-foreground">对话提取的候选先停在这里。查看原话、修改内容，再决定是否写入长期记忆。</p></CardHeader>
    <CardContent>{items.length ? <div className="review-queue-list">{items.map(item => <ReviewItem key={item.id} item={item} onApprove={onApprove} onReject={onReject} onSource={onSource} onError={onError} />)}</div> : <p className="text-sm text-muted-foreground">暂无待审核候选。完成对话后，后台提取的记忆会出现在这里。</p>}</CardContent>
  </Card>
}
