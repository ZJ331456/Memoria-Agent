import { useEffect, useState } from 'react'
import { HistoryIcon, RotateCcwIcon, SaveIcon } from 'lucide-react'
import { api, type Memory, type MemoryDetail, type MemoryKind, type MemoryTimelineEntry } from './api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'

type Correction = { content: string; kind: MemoryKind; importance: number; reason: string }
const kinds: MemoryKind[] = ['fact', 'preference', 'profile', 'goal', 'procedure']
const date = (value: string) => new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))

export function MemoryReviewPanel({ memory, onCorrect, onSource, onError }: {
  memory: Memory
  onCorrect: (data: Correction) => Promise<void>
  onSource: (sourceRef: string) => Promise<void>
  onError: (error: unknown) => void
}) {
  const [versions, setVersions] = useState<MemoryTimelineEntry[]>([])
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [draft, setDraft] = useState<Correction>({ content: memory.content, kind: memory.kind, importance: memory.importance, reason: '' })

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.memoryTimeline(memory.id)
      .then(items => { if (!cancelled) setVersions(items) })
      .catch(error => { if (!cancelled) onError(error) })
      .finally(() => { if (!cancelled) setLoading(false) })
    api.memoryDetail(memory.id)
      .then(item => { if (!cancelled) setDetail(item) })
      .catch(() => { if (!cancelled) setDetail(null) })
    return () => { cancelled = true }
  }, [memory.id])

  const changed = draft.content.trim() !== memory.content || draft.kind !== memory.kind || draft.importance !== memory.importance
  const save = async () => {
    if (!draft.content.trim() || !draft.reason.trim() || !changed || saving) return
    setSaving(true)
    try { await onCorrect({ ...draft, content: draft.content.trim(), reason: draft.reason.trim() }) }
    catch (error) { onError(error) }
    finally { setSaving(false) }
  }
  const restore = (version: MemoryTimelineEntry) => {
    setDraft({ content: version.content, kind: version.kind, importance: version.importance, reason: `恢复 ${date(version.created_at)} 的历史版本` })
  }

  return <section className="memory-review" data-testid="memory-review" aria-label="检查并纠正记忆">
    <div className="memory-current"><div className="memory-current-meta"><Badge>当前有效</Badge><span>{memory.kind} · 重要度 {memory.importance}/5</span></div><p>{memory.content}</p><small>来源：{memory.source}{memory.source_ref && ['conversation', 'reviewed_conversation'].includes(memory.source) ? <> · <button type="button" className="source-ref-link" onClick={() => onSource(memory.source_ref!)} title="查看原始对话">{memory.source_ref}</button></> : memory.source_ref ? ` · ${memory.source_ref}` : ''}</small></div>
    {(memory.entities?.length || memory.valid_at || detail) && <div className="memory-graph" data-testid="memory-graph" aria-label="记忆关联与演化">
      {(memory.entities?.length || memory.valid_at || memory.invalid_at) && <div className="memory-temporal"><small>有效期：{memory.valid_at ? date(memory.valid_at) : date(memory.created_at)} → {memory.invalid_at ? date(memory.invalid_at) : '至今有效'}</small>{(memory.entities?.length ?? 0) > 0 && <small> · 实体：{memory.entities!.join('、')}</small>}</div>}
      {(detail?.validity_intervals?.length ?? 0) > 1 && <div className="memory-evolutions" aria-label="记忆生效区间"><span>生效区间（含撤销后恢复）</span><ul>{detail!.validity_intervals!.map((interval, index) => <li key={`${interval.valid_at}-${index}`}><small>{date(interval.valid_at)} → {interval.invalid_at ? date(interval.invalid_at) : '至今有效'}</small></li>)}</ul></div>}
      {(detail?.neighbors?.length ?? 0) > 0 && <div className="memory-neighbors"><span>关联记忆（{detail!.neighbors.length}）</span><ul>{detail!.neighbors.slice(0, 5).map(neighbor => <li key={neighbor.id}><small>{neighbor.kind} · {neighbor.link_relation ?? '相关'}</small><p>{neighbor.content}</p></li>)}</ul></div>}
      {(detail?.evolutions?.length ?? 0) > 0 && <div className="memory-evolutions"><span>演化记录（{detail!.evolutions.length}）</span><ul>{detail!.evolutions.map(evolution => <li key={evolution.id}><small>{date(evolution.created_at)}</small><p>{evolution.summary}</p></li>)}</ul></div>}
    </div>}
    <div className="memory-edit-heading"><h3>修改这条记忆</h3><p>改好内容并填写原因，再点击下方的「保存纠正」。</p></div>
    <FieldGroup>
      <Field><FieldLabel htmlFor="correction-content">纠正后的记忆</FieldLabel><Textarea id="correction-content" maxLength={4000} value={draft.content} onChange={event => setDraft({ ...draft, content: event.target.value })} /></Field>
      <div className="memory-review-fields">
        <Field><FieldLabel>类型</FieldLabel><Select value={draft.kind} onValueChange={value => setDraft({ ...draft, kind: value as MemoryKind })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{kinds.map(kind => <SelectItem key={kind} value={kind}>{kind}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
        <Field><FieldLabel htmlFor="correction-importance">重要度：{draft.importance}</FieldLabel><Input id="correction-importance" type="range" min="1" max="5" value={draft.importance} onChange={event => setDraft({ ...draft, importance: Number(event.target.value) })} /></Field>
      </div>
      <Field><FieldLabel htmlFor="correction-reason">纠正原因</FieldLabel><Input id="correction-reason" maxLength={500} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })} placeholder="例如：偏好已改变，原记录不再准确" /></Field>
    </FieldGroup>
    <div className="memory-review-actions"><Button size="sm" onClick={save} disabled={saving || !draft.content.trim() || !draft.reason.trim() || !changed}>{saving ? <Spinner data-icon="inline-start" /> : <SaveIcon data-icon="inline-start" />}保存纠正</Button></div>
    <div className="memory-timeline" data-testid="memory-timeline" aria-label="记忆版本时间线">
      <div className="memory-timeline-heading"><span><HistoryIcon />版本时间线</span><small>按需读取 · {loading ? '加载中' : `${versions.length} 个版本`}</small></div>
      {loading ? <p className="timeline-loading"><Spinner />正在读取记忆历史…</p> : <ol>{versions.map((version, index) => <li key={version.id} className="timeline-entry"><span className="timeline-node" /><div><div className="timeline-meta"><Badge variant={version.status === 'active' ? 'default' : 'secondary'}>{version.status === 'active' ? '当前版本' : '历史版本'}</Badge>{version.replacement_relation === 'correction' && <Badge variant="outline">用户纠正</Badge>}<time>{date(version.created_at)}</time></div><p>{version.content}</p><small>{version.replacement_reason || (index === 0 ? '最初记录' : '版本更新')} · 来源：{version.source}{version.source_ref && ['conversation', 'reviewed_conversation'].includes(version.source) ? <> · <button type="button" className="source-ref-link" onClick={() => onSource(version.source_ref!)} title="查看原始对话">{version.source_ref}</button></> : version.source_ref ? ` · ${version.source_ref}` : ''}</small>{version.status !== 'active' && <div className="memory-restore"><Button variant="outline" size="sm" onClick={() => restore(version)}><RotateCcwIcon data-icon="inline-start" />填入此版本</Button></div>}</div></li>)}</ol>}
    </div>
  </section>
}
