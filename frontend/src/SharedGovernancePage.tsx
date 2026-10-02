import { useEffect, useRef, useState } from 'react'
import { ClipboardCopyIcon, KeyRoundIcon, LockKeyholeIcon, PlusIcon, RefreshCwIcon, ShieldCheckIcon, UsersRoundIcon } from 'lucide-react'
import { ApiError, type MemoryKind } from './api'
import {
  governanceApi, type AgentRegistration, type SharedEvent, type SharedLineage,
  type SharedMemory, type SharedProposal, type SharedSpace, type SharedGrant,
} from './governanceApi'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const kinds: MemoryKind[] = ['fact', 'preference', 'profile', 'goal', 'procedure']
const roles: SharedGrant['role'][] = ['reader', 'contributor', 'curator']
const roleNames: Record<SharedGrant['role'], string> = { reader: '读取', contributor: '提交提案', curator: '审核治理' }
const time = (value?: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : '—'
const errorText = (value: unknown) => value instanceof ApiError ? `${value.message}${value.requestId ? ` · ${value.requestId}` : ''}` : value instanceof Error ? value.message : String(value)

export function SharedGovernancePage() {
  const [agentName, setAgentName] = useState('')
  const [issuedAgent, setIssuedAgent] = useState<AgentRegistration | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [agentKey, setAgentKey] = useState('')
  const [spaces, setSpaces] = useState<SharedSpace[]>([])
  const [spaceId, setSpaceId] = useState('')
  const [newSpaceName, setNewSpaceName] = useState('')
  const [memberId, setMemberId] = useState('')
  const [memberRole, setMemberRole] = useState<SharedGrant['role']>('reader')
  const [proposal, setProposal] = useState({ content: '', kind: 'fact' as MemoryKind, importance: 3, topic_key: '', source_type: 'manual' as 'manual' | 'message' | 'external' | 'agent', source_ref: '', expires_at: '' })
  const [proposals, setProposals] = useState<SharedProposal[]>([])
  const [grants, setGrants] = useState<SharedGrant[]>([])
  const [memories, setMemories] = useState<SharedMemory[]>([])
  const [events, setEvents] = useState<SharedEvent[]>([])
  const [searchText, setSearchText] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [lineageId, setLineageId] = useState('')
  const [lineage, setLineage] = useState<SharedLineage | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const agentGeneration = useRef(0)
  const viewGeneration = useRef(0)
  const lineageGeneration = useRef(0)

  const clearView = () => {
    viewGeneration.current += 1
    lineageGeneration.current += 1
    setProposals([]); setMemories([]); setEvents([]); setGrants([])
    setLineageId(''); setLineage(null)
  }
  const clearIdentity = () => {
    agentGeneration.current += 1
    clearView()
    setSpaces([]); setSpaceId(''); setLoading(false)
  }
  const handleAccessFailure = (value: unknown) => {
    clearIdentity()
    if (value instanceof ApiError && value.status === 401) setAgentKey('')
    setError(errorText(value))
  }
  const refresh = () => {
    agentGeneration.current += 1
    clearView()
    setSpaces([])
    setError('')
    setReload(value => value + 1)
  }
  const changeSpace = (next: string) => {
    if (next === spaceId) return
    clearView()
    setError('')
    setSpaceId(next)
  }

  useEffect(() => {
    if (!agentKey) return
    let alive = true
    const generation = agentGeneration.current
    setLoading(true)
    governanceApi.spaces(agentKey).then(items => {
      if (!alive || generation !== agentGeneration.current) return
      setSpaces(items)
      setSpaceId(current => items.some(item => item.id === current) ? current : items[0]?.id || '')
    }).catch(value => {
      if (!alive || generation !== agentGeneration.current) return
      handleAccessFailure(value)
    }).finally(() => { if (alive && generation === agentGeneration.current) setLoading(false) })
    return () => { alive = false }
  }, [agentKey, reload])

  useEffect(() => {
    if (!agentKey || !spaceId) return
    const accessRole = spaces.find(item => item.id === spaceId)?.access_role
    if (!accessRole) return
    const canSubmit = accessRole === 'contributor' || accessRole === 'curator' || accessRole === 'owner'
    const canReview = accessRole === 'curator' || accessRole === 'owner'
    let alive = true
    const generation = viewGeneration.current
    Promise.allSettled([
      canSubmit ? governanceApi.proposals(agentKey, spaceId, 'pending') : Promise.resolve([]),
      governanceApi.memories(agentKey, spaceId, searchQuery),
      canReview ? governanceApi.events(agentKey, spaceId) : Promise.resolve([]),
      canReview ? governanceApi.grants(agentKey, spaceId) : Promise.resolve([]),
    ]).then(([proposalResult, memoryResult, eventResult, grantResult]) => {
      if (!alive || generation !== viewGeneration.current) return
      const results = [proposalResult, memoryResult, eventResult, grantResult]
      const accessFailure = results.find(result => result.status === 'rejected' && result.reason instanceof ApiError && [401, 403].includes(result.reason.status))
      if (accessFailure?.status === 'rejected') { handleAccessFailure(accessFailure.reason); return }
      setProposals(proposalResult.status === 'fulfilled' ? proposalResult.value : [])
      setMemories(memoryResult.status === 'fulfilled' ? memoryResult.value : [])
      setEvents(eventResult.status === 'fulfilled' ? eventResult.value : [])
      setGrants(grantResult.status === 'fulfilled' ? grantResult.value : [])
      const failure = results.find(result => result.status === 'rejected')
      if (failure?.status === 'rejected') setError(errorText(failure.reason))
    })
    return () => { alive = false }
  }, [agentKey, spaceId, spaces, searchQuery, reload])

  const act = async (name: string, action: () => Promise<unknown>, success: string) => {
    if (busy) return
    setBusy(name); setError(''); setNotice('')
    try { await action(); setNotice(success); refresh() }
    catch (value) {
      if (value instanceof ApiError && [401, 403].includes(value.status)) handleAccessFailure(value)
      else setError(errorText(value))
    }
    finally { setBusy('') }
  }
  const createAgent = () => act('agent', async () => {
    const result = await governanceApi.createAgent(agentName.trim())
    setIssuedAgent(result)
    setAgentName('')
  }, 'Agent 已创建。请现在复制密钥；刷新后无法再次获取。')
  const connect = () => {
    const next = keyInput.trim()
    if (!next) return
    clearIdentity()
    setAgentKey(next)
    setKeyInput('')
    setIssuedAgent(null)
    setError(''); setNotice('Agent 密钥仅保存在当前页面内存中。')
  }
  const disconnect = () => {
    clearIdentity()
    setAgentKey(''); setKeyInput(''); setIssuedAgent(null)
    setError('')
    setNotice('已断开 Agent。密钥已从当前页面内存中清除。')
  }
  const createSpace = () => act('space', async () => {
    const item = await governanceApi.createSpace(agentKey, newSpaceName.trim())
    setNewSpaceName(''); changeSpace(item.id)
  }, '共享空间已创建。当前 Agent 是空间所有者。')
  const submitProposal = () => act('proposal', async () => {
    await governanceApi.propose(agentKey, {
      space_id: spaceId, content: proposal.content.trim(), kind: proposal.kind, importance: proposal.importance,
      topic_key: proposal.topic_key.trim(), source_type: proposal.source_type,
      ...(proposal.source_ref.trim() ? { source_ref: proposal.source_ref.trim() } : {}),
      ...(proposal.expires_at ? { expires_at: new Date(proposal.expires_at).toISOString() } : {}),
    })
    setProposal(current => ({ ...current, content: '', source_type: 'manual', source_ref: '' }))
  }, '提案已进入审核队列，批准前不会成为共享记忆。')
  const showLineage = async (id: string) => {
    const generation = ++lineageGeneration.current
    if (lineageId === id) { setLineageId(''); setLineage(null); return }
    setLineageId(id); setLineage(null)
    try {
      const result = await governanceApi.lineage(agentKey, id)
      if (generation === lineageGeneration.current) setLineage(result)
    } catch (value) {
      if (generation !== lineageGeneration.current) return
      setLineageId(''); setLineage(null)
      if (value instanceof ApiError && [401, 403].includes(value.status)) handleAccessFailure(value)
      else setError(errorText(value))
    }
  }
  const selectedSpace = spaces.find(item => item.id === spaceId)
  const accessRole = selectedSpace?.access_role
  const canSubmit = accessRole === 'contributor' || accessRole === 'curator' || accessRole === 'owner'
  const canReview = accessRole === 'curator' || accessRole === 'owner'
  const isOwner = accessRole === 'owner'

  return <section className="page governance-page" data-testid="governance-page">
    <header className="page-header governance-header"><div><div className="governance-eyebrow"><ShieldCheckIcon/> MEMORY GOVERNANCE</div><h1>共享记忆治理</h1><p>让多个 Agent 有边界地共享知识：提交、审核、追溯、撤销，每一步留下记录。</p></div><Badge>{agentKey ? 'Agent 已连接' : '等待 Agent 身份'}</Badge></header>
    {error && <div className="governance-feedback governance-error" role="alert"><strong>操作未完成</strong><span>{error}</span><Button size="sm" variant="ghost" onClick={() => setError('')}>关闭</Button></div>}
    {notice && <div className="governance-feedback governance-success" role="status">{notice}</div>}

    <div className="governance-top-grid">
      <Card><CardHeader><CardTitle><KeyRoundIcon className="governance-card-icon"/>1 · Agent 身份</CardTitle><CardDescription>管理员创建 Agent 后，密钥只会返回一次。也可以粘贴已有密钥进入工作台。</CardDescription></CardHeader><CardContent className="governance-form-stack">
        {!agentKey && <><div className="governance-inline-form"><label>Agent 名称<Input value={agentName} onChange={event => setAgentName(event.target.value)} placeholder="例如：研究 Agent" maxLength={80}/></label><Button onClick={createAgent} disabled={!agentName.trim() || Boolean(busy)}><PlusIcon data-icon="inline-start"/>创建 Agent</Button></div>
          {issuedAgent && <div className="governance-key-box"><strong>{issuedAgent.name} · ID {issuedAgent.id}</strong><span>以下密钥只显示本次，请保存到你自己的安全位置。</span><code>{issuedAgent.token}</code><div className="governance-actions"><Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(issuedAgent.token).then(() => setNotice('密钥已复制')).catch(value => setError(errorText(value)))}><ClipboardCopyIcon data-icon="inline-start"/>复制密钥</Button><Button size="sm" onClick={() => { clearIdentity(); setAgentKey(issuedAgent.token); setIssuedAgent(null); setNotice('已使用新 Agent 连接工作台。') }}>使用此密钥</Button></div></div>}
          <div className="governance-inline-form"><label>已有 Agent 密钥<Input type="password" autoComplete="off" value={keyInput} onChange={event => setKeyInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') connect() }} placeholder="粘贴 X-Agent-Key"/></label><Button variant="outline" onClick={connect} disabled={!keyInput.trim()}>连接</Button></div></>}
        {agentKey && <div className="governance-connected"><LockKeyholeIcon/><span>当前 Agent 已连接。密钥仅存在于此浏览器页面内存；刷新后需重新输入。</span><Button size="sm" variant="outline" onClick={disconnect}>断开</Button></div>}
      </CardContent></Card>

      <Card><CardHeader><CardTitle><UsersRoundIcon className="governance-card-icon"/>2 · 共享空间</CardTitle><CardDescription>空间所有者负责授权；有权限的 Agent 才能查看记忆或提交提案。</CardDescription>{agentKey && <Button size="sm" variant="outline" onClick={refresh} disabled={loading}><RefreshCwIcon data-icon="inline-start"/>刷新空间与权限</Button>}</CardHeader><CardContent className="governance-form-stack">
        <div className="governance-inline-form"><label>创建空间<Input value={newSpaceName} onChange={event => setNewSpaceName(event.target.value)} placeholder="例如：产品研发" maxLength={80} disabled={!agentKey}/></label><Button onClick={createSpace} disabled={!agentKey || !newSpaceName.trim() || Boolean(busy)}><PlusIcon data-icon="inline-start"/>创建</Button></div>
        <label className="governance-full-label">当前空间<select value={spaceId} onChange={event => changeSpace(event.target.value)} disabled={!agentKey || loading || spaces.length === 0}><option value="">{loading ? '正在加载…' : '选择空间'}</option>{spaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {selectedSpace && <div className="governance-space-meta"><strong>{selectedSpace.name}</strong><span>ID：{selectedSpace.id}</span><span>我的角色：{accessRole}</span><span>所有者：{selectedSpace.owner_agent_id}</span><span>可见性：{selectedSpace.visibility}</span></div>}
      </CardContent></Card>
    </div>

    {agentKey && selectedSpace && <>
      <div className="governance-top-grid">
        {canReview && <Card><CardHeader><CardTitle>3 · 授权成员</CardTitle><CardDescription>读取者可查看，贡献者可提交提案，治理者可批准、拒绝或撤销。</CardDescription></CardHeader><CardContent className="governance-form-stack">
          {isOwner && selectedSpace?.visibility === 'shared' && <>
          <label className="governance-full-label">目标 Agent ID<Input value={memberId} onChange={event => setMemberId(event.target.value)} placeholder="粘贴另一个 Agent 的 ID"/></label>
          <div className="governance-inline-form"><label>权限<select value={memberRole} onChange={event => setMemberRole(event.target.value as SharedGrant['role'])}>{roles.map(role => <option key={role} value={role}>{roleNames[role]}</option>)}</select></label><div className="governance-actions"><Button onClick={() => act('grant', () => governanceApi.grant(agentKey, spaceId, memberId.trim(), memberRole), '授权已更新。')} disabled={!memberId.trim() || Boolean(busy)}>授权</Button><Button variant="outline" onClick={() => act('revoke-grant', () => governanceApi.revokeGrant(agentKey, spaceId, memberId.trim()), '成员权限已撤销。')} disabled={!memberId.trim() || Boolean(busy)}>撤销授权</Button></div></div>
          </>}
          {selectedSpace?.visibility === 'private' && <p className="governance-empty">私有空间仅所有者可用，不开放成员授权。</p>}
          {grants.length ? <div className="governance-grants">{grants.map(grant => <div key={grant.agent_id}><span>{grant.agent_name || grant.agent_id}<small>{grant.agent_id}</small></span><Badge>{roleNames[grant.role]}</Badge>{isOwner && <Button size="sm" variant="ghost" onClick={() => act('revoke-grant', () => governanceApi.revokeGrant(agentKey, spaceId, grant.agent_id), '成员权限已撤销。')} disabled={Boolean(busy)}>移除</Button>}</div>)}</div> : <p className="governance-empty">暂无成员授权。</p>}
        </CardContent></Card>}
        {canSubmit && <Card><CardHeader><CardTitle>4 · 提交记忆提案</CardTitle><CardDescription>相同主题的新提案会先经过治理者审核，避免直接覆盖现有结论。</CardDescription></CardHeader><CardContent className="governance-form-stack">
          <label className="governance-full-label">记忆内容<Textarea value={proposal.content} onChange={event => setProposal(current => ({ ...current, content: event.target.value }))} placeholder="写下需要共享的事实、偏好或目标" maxLength={4000}/></label>
          <div className="governance-field-grid"><label>主题键<Input value={proposal.topic_key} onChange={event => setProposal(current => ({ ...current, topic_key: event.target.value }))} placeholder="如 product.release_date" maxLength={200}/></label><label>类别<select value={proposal.kind} onChange={event => setProposal(current => ({ ...current, kind: event.target.value as MemoryKind }))}>{kinds.map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></label><label>重要度<select value={proposal.importance} onChange={event => setProposal(current => ({ ...current, importance: Number(event.target.value) }))}>{[1, 2, 3, 4, 5].map(number => <option key={number} value={number}>{number}</option>)}</select></label><label>来源类型<select value={proposal.source_type} onChange={event => setProposal(current => ({ ...current, source_type: event.target.value as typeof current.source_type }))}><option value="manual">手动输入</option><option value="message">原始对话</option><option value="external">外部文档</option><option value="agent">Agent 结果</option></select></label><label>来源 ID{proposal.source_type === 'manual' ? '（可选）' : ''}<Input value={proposal.source_ref} onChange={event => setProposal(current => ({ ...current, source_ref: event.target.value }))} placeholder={proposal.source_type === 'message' ? '原始消息 ID' : '对话、文档或任务 ID'} maxLength={500}/></label><label>到期时间（可选）<Input type="datetime-local" value={proposal.expires_at} onChange={event => setProposal(current => ({ ...current, expires_at: event.target.value }))}/></label></div>
          <div className="governance-actions"><Button onClick={submitProposal} disabled={!proposal.content.trim() || !proposal.topic_key.trim() || (proposal.source_type !== 'manual' && !proposal.source_ref.trim()) || Boolean(busy)}>提交审核</Button><small>提案未批准前不会进入共享检索。</small></div>
        </CardContent></Card>}
      </div>

      {canSubmit && <Card><CardHeader className="governance-section-heading"><div><CardTitle>待审核提案 <Badge>{proposals.length}</Badge></CardTitle><CardDescription>{canReview ? '填写理由后批准或拒绝；冲突提案需确认替代对象。' : '你可以查看自己提交的候选，等待空间治理者审核。'}</CardDescription></div><Button size="sm" variant="outline" onClick={refresh}><RefreshCwIcon data-icon="inline-start"/>刷新</Button></CardHeader><CardContent>{proposals.length ? <div className="governance-proposals">{proposals.map(item => <ProposalCard key={item.id} item={item} busy={Boolean(busy)} canReview={canReview} onApprove={reason => act(`approve-${item.id}`, () => governanceApi.approve(agentKey, item.id, reason, item.conflict_memory_id || null), '提案已批准并进入共享记忆。')} onReject={reason => act(`reject-${item.id}`, () => governanceApi.reject(agentKey, item.id, reason), '提案已拒绝，审计记录已保存。')}/>)}</div> : <p className="governance-empty">当前空间没有待审核提案。</p>}</CardContent></Card>}

      <div className="governance-bottom-grid">
        <Card><CardHeader><CardTitle>已生效的共享记忆</CardTitle><CardDescription>只展示当前 Agent 可读取、已经批准且仍有效的记忆。</CardDescription></CardHeader><CardContent className="governance-form-stack"><div className="governance-inline-form"><label>搜索记忆<Input value={searchText} onChange={event => setSearchText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { setMemories([]); setSearchQuery(searchText.trim()) } }} placeholder="关键词"/></label><Button variant="outline" onClick={() => { setMemories([]); setSearchQuery(searchText.trim()) }}>检索</Button></div>
          {memories.length ? <div className="governance-memory-list">{memories.map(item => <MemoryCard key={item.id} item={item} busy={Boolean(busy)} canReview={canReview} lineageOpen={lineageId === item.id} lineage={lineageId === item.id ? lineage : null} onLineage={() => showLineage(item.id)} onRevoke={reason => act(`revoke-${item.id}`, () => governanceApi.revokeMemory(agentKey, item.id, reason), '记忆已撤销，后续检索将不再命中。')}/>)}</div> : <p className="governance-empty">没有匹配的有效共享记忆。</p>}
        </CardContent></Card>
        {canReview && <Card><CardHeader><CardTitle>治理审计</CardTitle><CardDescription>记录空间内的提案、审核、权限和记忆状态变化。</CardDescription></CardHeader><CardContent>{events.length ? <ol className="governance-event-list">{events.map(item => <li key={item.seq}><div><Badge>{item.action}</Badge><time>{time(item.created_at)}</time></div><strong>{item.actor_id || '系统'}</strong>{item.reason && <p>{item.reason}</p>}{item.memory_id && <small>记忆 {item.memory_id}</small>}{item.proposal_id && <small>提案 {item.proposal_id}</small>}{item.source_ref && <small>来源 {item.source_type || '未知'} · {item.source_ref}</small>}</li>)}</ol> : <p className="governance-empty">暂无可查看的审计记录。</p>}</CardContent></Card>}
      </div>
    </>}
    {agentKey && !spaceId && !loading && <Card><CardContent><p className="governance-empty">创建或选择一个共享空间，即可开始授权、审核和检索。</p></CardContent></Card>}
  </section>
}

function ProposalCard({ item, busy, canReview, onApprove, onReject }: { item: SharedProposal; busy: boolean; canReview: boolean; onApprove: (reason: string) => void; onReject: (reason: string) => void }) {
  const [reason, setReason] = useState('')
  return <article className="governance-proposal-card"><div className="governance-card-meta"><Badge>{item.kind}</Badge><span>主题：{item.topic_key}</span><span>{time(item.created_at)}</span></div><p>{item.content}</p><small>提交者：{item.proposer_agent_id}{item.source_ref ? ` · 来源 ${item.source_type} / ${item.source_ref}` : ''}</small>{item.conflict_memory_id && <div className="governance-conflict">与已有记忆 {item.conflict_memory_id} 冲突。批准后将替代此版本。</div>}{canReview ? <><label>审核理由<Input value={reason} onChange={event => setReason(event.target.value)} placeholder="说明批准或拒绝的依据" maxLength={500}/></label><div className="governance-actions"><Button size="sm" onClick={() => onApprove(reason.trim())} disabled={!reason.trim() || busy}>批准</Button><Button size="sm" variant="outline" onClick={() => onReject(reason.trim())} disabled={!reason.trim() || busy}>拒绝</Button></div></> : <small>等待治理者审核</small>}</article>
}

function MemoryCard({ item, busy, canReview, lineageOpen, lineage, onLineage, onRevoke }: { item: SharedMemory; busy: boolean; canReview: boolean; lineageOpen: boolean; lineage: SharedLineage | null; onLineage: () => void; onRevoke: (reason: string) => void }) {
  const [reason, setReason] = useState('')
  return <article className="governance-memory-card"><div className="governance-card-meta"><Badge>{item.kind}</Badge><span>主题：{item.topic_key}</span><span>版本 {item.version}</span><span>重要度 {item.importance}</span></div><p>{item.content}</p><small>ID {item.id} · 批准于 {time(item.approved_at)}</small><small>提交者 {item.proposer_agent_id} · 审核者 {item.approved_by}</small>{item.source_ref && <small>来源：{item.source_type} / {item.source_ref}</small>}<div className="governance-actions"><Button size="sm" variant="outline" onClick={onLineage}>{lineageOpen ? '收起沿革' : '查看沿革'}</Button></div>{lineageOpen && <div className="governance-lineage">{!lineage ? '正在加载沿革…' : lineage.length ? lineage.map(version => <div key={version.id}><strong>v{version.version} · {version.effective_status || version.status} · {time(version.approved_at)}</strong><p>{version.content}</p><small>{version.id}</small></div>) : <span>暂无其他版本</span>}</div>}{canReview && <div className="governance-revoke"><label>撤销理由<Input value={reason} onChange={event => setReason(event.target.value)} placeholder="说明为何此记忆不应继续使用" maxLength={500}/></label><Button size="sm" variant="outline" onClick={() => onRevoke(reason.trim())} disabled={!reason.trim() || busy}>撤销记忆</Button></div>}</article>
}
