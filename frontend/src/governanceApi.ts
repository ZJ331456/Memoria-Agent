import { ApiError, type MemoryKind } from './api'

export type AgentRegistration = { id: string; name: string; enabled: boolean; created_at: string; token: string }
export type SharedSpace = { id: string; name: string; visibility: 'private' | 'shared'; owner_agent_id: string; created_at: string; access_role: 'owner' | SharedGrant['role'] }
export type SharedGrant = { space_id: string; agent_id: string; agent_name?: string; role: 'reader' | 'contributor' | 'curator'; granted_at: string }
export type SharedProposal = {
  id: string; space_id: string; proposer_agent_id: string; content: string; kind: MemoryKind; importance: number;
  topic_key: string; source_type: string; source_ref: string; expires_at: string | null;
  status: 'pending' | 'approved' | 'rejected'; reviewer_id: string | null; review_reason: string | null;
  applied_memory_id: string | null; conflict_memory_id?: string | null; created_at: string; decided_at: string | null;
}
export type SharedMemory = {
  id: string; space_id: string; content: string; kind: MemoryKind; importance: number; topic_key: string;
  status: 'active' | 'superseded' | 'revoked'; source_type: string; source_ref: string | null;
  effective_status?: 'active' | 'superseded' | 'revoked' | 'expired';
  proposal_id: string; proposer_agent_id: string; supersedes_id: string | null; expires_at: string | null;
  version: number; approved_by: string; approved_at: string; revoked_by: string | null; revoked_at: string | null;
}
export type SharedEvent = {
  seq: number; space_id: string; action: string; actor_id: string;
  memory_id: string | null; proposal_id: string | null; reason: string;
  source_type: string | null; source_ref: string | null; created_at: string;
}
export type SharedLineage = SharedMemory[]
export type ProposalInput = {
  space_id: string; content: string; kind: MemoryKind; importance: number; topic_key: string;
  source_type: string; source_ref?: string; expires_at?: string | null;
}

const apiToken = import.meta.env.VITE_MEMORIA_API_TOKEN as string | undefined
async function request<T>(path: string, key: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': crypto.randomUUID(),
      ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
      ...(key ? { 'X-Agent-Key': key } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let body: Record<string, unknown> = {}
    try { body = await response.json() } catch { /* An empty error body still has an HTTP status. */ }
    throw new ApiError(
      String(body.message || body.detail || response.statusText),
      String(body.code || 'request_error'),
      String(body.request_id || response.headers.get('X-Request-ID') || ''),
      response.status,
    )
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

const encoded = (value: string) => encodeURIComponent(value)
const params = (values: Record<string, string>) => {
  const query = new URLSearchParams()
  for (const [name, value] of Object.entries(values)) if (value) query.set(name, value)
  return query.toString()
}

export const governanceApi = {
  createAgent: (name: string) => request<AgentRegistration>('/api/governance/agents', '', { method: 'POST', body: JSON.stringify({ name }) }),
  spaces: (key: string) => request<SharedSpace[]>('/api/shared/spaces', key),
  createSpace: (key: string, name: string) => request<SharedSpace>('/api/shared/spaces', key, { method: 'POST', body: JSON.stringify({ name }) }),
  grants: (key: string, spaceId: string) => request<SharedGrant[]>(`/api/shared/spaces/${encoded(spaceId)}/grants`, key),
  grant: (key: string, spaceId: string, agentId: string, role: SharedGrant['role']) => request<SharedGrant>(`/api/shared/spaces/${encoded(spaceId)}/grants`, key, { method: 'POST', body: JSON.stringify({ agent_id: agentId, role }) }),
  revokeGrant: (key: string, spaceId: string, agentId: string) => request<void>(`/api/shared/spaces/${encoded(spaceId)}/grants/${encoded(agentId)}`, key, { method: 'DELETE' }),
  proposals: (key: string, spaceId: string, status = '') => request<SharedProposal[]>(`/api/shared/proposals?${params({ space_id: spaceId, status })}`, key),
  propose: (key: string, data: ProposalInput) => request<SharedProposal>('/api/shared/proposals', key, { method: 'POST', body: JSON.stringify(data) }),
  approve: (key: string, id: string, reason: string, expectedReplacesId: string | null) => request<SharedMemory & { action: string }>(`/api/shared/proposals/${encoded(id)}/approve`, key, { method: 'POST', body: JSON.stringify({ reason, expected_replaces_id: expectedReplacesId }) }),
  reject: (key: string, id: string, reason: string) => request<SharedProposal>(`/api/shared/proposals/${encoded(id)}/reject`, key, { method: 'POST', body: JSON.stringify({ reason }) }),
  memories: (key: string, spaceId: string, query = '') => request<SharedMemory[]>(`/api/shared/memories?${params({ space_id: spaceId, q: query })}`, key),
  lineage: (key: string, id: string) => request<SharedLineage>(`/api/shared/memories/${encoded(id)}/lineage`, key),
  revokeMemory: (key: string, id: string, reason: string) => request<SharedMemory>(`/api/shared/memories/${encoded(id)}/revoke`, key, { method: 'POST', body: JSON.stringify({ reason }) }),
  events: (key: string, spaceId: string) => request<SharedEvent[]>(`/api/shared/events?${params({ space_id: spaceId })}`, key),
}
