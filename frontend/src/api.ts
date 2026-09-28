export type Session={id:string;title:string;created_at:string;updated_at:string;message_count:number}
export type Message={id:string;session_id:string;role:'user'|'assistant';content:string;created_at:string}
export type MemoryKind='fact'|'preference'|'profile'|'goal'|'procedure'
export type Memory={id:string;content:string;kind:MemoryKind;importance:number;source:string;created_at:string;updated_at:string;status:'active'|'superseded';reinforcement:number;supersedes_id:string|null;last_reinforced_at:string|null}
export type MemoryTimelineEntry=Memory&{replacement_reason:string|null}
export type MemoryReindex={enabled:boolean;indexed:number;remaining:number}
export type MemoryWrite={action:'created'|'reinforced'|'superseded';memory:Memory;previous_id:string|null;reason:string}
export type MemoryJob={id:string;source_ref:string;status:'pending'|'running'|'retry'|'completed'|'failed';attempts:number;error:string|null;available_at:string|null;lease_owner:string|null;lease_expires_at:string|null;created_at:string;updated_at:string}
export type MemoryUndo={affected_ids:string[];restored_ids:string[]}
export type Tool={name:string;description:string;risk:'read-only'|'write'|string;owner?:string;always_on?:boolean;search_hint?:string}
export type ToolSearchStatus={enabled:boolean;direct_tools:string[];searchable_tools:string[];groups:Array<{owner:string;tools:string[]}>}
export type McpServerStatus={name:string;enabled:boolean;connected:boolean;tools:string[];error:string;command:string[];risk:string}
export type McpStatus={enabled:boolean;config_path:string;servers:McpServerStatus[];tool_count:number}
export type DriftStatus={enabled:boolean;running:boolean;busy:boolean;min_idle_seconds:number;interval_seconds:number;max_steps:number;daily_budget:number;runs_today:number;budget_remaining:number;quiet_hours:number[];allowed_skills:string[];allow_write_tools:string[];timezone:string;last_run:any;idle_seconds:number|null;session_id:string|null}
export type DriftRun={id:string;session_id:string|null;skill:string;trigger:string;status:string;steps:number;summary:string;trace_id:string;error:string;created_at:string;finished_at:string|null}
export type Trace={id:string;session_id:string;status:string;steps:number;duration_ms:number;memories:Memory[];tools:Array<{name:string;ok:boolean;elapsed_ms:number;preview:string;arguments:Record<string,unknown>}>;metadata:Record<string,any>;error:string|null;created_at:string}
export type ModelSlot={model:string;base_url:string;configured:boolean;api_key_set?:boolean}
export type SetupStatus={main:ModelSlot;fast:ModelSlot;embedding:ModelSlot;setup_needed:boolean;override_path?:string}
export type MarkdownStatus={enabled:boolean;directory?:string;files?:Record<string,number>;pending_open?:number}
export type Overview={sessions:number;messages:number;memories:number;memories_superseded:number;traces:number;memory_jobs_pending:number;memory_jobs_failed:number;drift_runs?:number;models:Record<string,any>;vector_index?:{enabled:boolean;backend:string;dimension:number|null;error:string};tools:Tool[];tool_search?:ToolSearchStatus;mcp?:McpStatus;drift?:DriftStatus;pipeline:Record<string,string[]>;markdown?:MarkdownStatus;setup?:SetupStatus}
export class ApiError extends Error{constructor(message:string,public code:string,public requestId:string,public status:number){super(message)}}
const apiToken=import.meta.env.VITE_MEMORIA_API_TOKEN as string|undefined
const requestHeaders=()=>({'Content-Type':'application/json','X-Request-ID':crypto.randomUUID(),...(apiToken?{Authorization:`Bearer ${apiToken}`}:{})})
const call=async<T>(path:string,init?:RequestInit):Promise<T>=>{const res=await fetch(path,{...init,headers:{...requestHeaders(),...init?.headers}});if(!res.ok){let body:any={};try{body=await res.json()}catch{}throw new ApiError(body.message||body.detail||res.statusText,body.code||'request_error',body.request_id||res.headers.get('X-Request-ID')||'',res.status)}return res.status===204?undefined as T:res.json()}
const chatStream=async(id:string,content:string,onEvent:(event:any)=>void,signal?:AbortSignal)=>{const res=await fetch(`/api/sessions/${id}/chat/stream`,{method:'POST',headers:requestHeaders(),body:JSON.stringify({content}),signal});if(!res.ok){let body:any={};try{body=await res.json()}catch{}throw new ApiError(body.message||body.detail||res.statusText,body.code||'request_error',body.request_id||res.headers.get('X-Request-ID')||'',res.status)}if(!res.body)throw new Error('浏览器未提供流式响应体');const reader=res.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const{done,value}=await reader.read();buffer+=decoder.decode(value,{stream:!done});const blocks=buffer.split('\n\n');buffer=blocks.pop()||'';for(const block of blocks){const data=block.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trim()).join('');if(data)onEvent(JSON.parse(data))}if(done)break}}
export const api={
 overview:()=>call<Overview>('/api/overview'), sessions:()=>call<Session[]>('/api/sessions'),
 createSession:(title='新对话')=>call<Session>('/api/sessions',{method:'POST',body:JSON.stringify({title})}),
 renameSession:(id:string,title:string)=>call<Session>(`/api/sessions/${id}`,{method:'PATCH',body:JSON.stringify({title})}),
 deleteSession:(id:string)=>call<void>(`/api/sessions/${id}`,{method:'DELETE'}), messages:(id:string)=>call<Message[]>(`/api/sessions/${id}/messages`),
 chat:(id:string,content:string)=>call<{message:Message;memories_created:Memory[];trace:Trace}>(`/api/sessions/${id}/chat`,{method:'POST',body:JSON.stringify({content})}),
 chatStream, cancelChat:(id:string)=>call<{status:'cancelled'|'idle';session_id:string}>(`/api/sessions/${id}/cancel`,{method:'POST'}),
 memories:(q='')=>call<Memory[]>(`/api/memories?q=${encodeURIComponent(q)}`), createMemory:(data:{content:string;kind:MemoryKind;importance:number})=>call<MemoryWrite>('/api/memories',{method:'POST',body:JSON.stringify(data)}),
 memoryTimeline:(id:string)=>call<MemoryTimelineEntry[]>(`/api/memories/${encodeURIComponent(id)}/timeline`),
 reindexMemories:(limit=1000)=>call<MemoryReindex>(`/api/memories/reindex?limit=${limit}`,{method:'POST'}),
 memoryJobs:(limit=50)=>call<MemoryJob[]>(`/api/memory-jobs?limit=${limit}`), retryMemoryJob:(id:string)=>call<MemoryJob>(`/api/memory-jobs/${id}/retry`,{method:'POST'}),
 undoMemories:(sourceRefs:string[],dryRun=false)=>call<MemoryUndo>('/api/memories/undo',{method:'POST',body:JSON.stringify({source_refs:sourceRefs,dry_run:dryRun})}),
 updateMemory:(id:string,data:Partial<Pick<Memory,'content'|'kind'|'importance'>>)=>call<Memory>(`/api/memories/${id}`,{method:'PATCH',body:JSON.stringify(data)}), deleteMemory:(id:string)=>call<void>(`/api/memories/${id}`,{method:'DELETE'}),
 traces:(sessionId='')=>call<Trace[]>(`/api/traces?session_id=${encodeURIComponent(sessionId)}`), tools:()=>call<Tool[]>('/api/tools'),
 toolSearchStatus:()=>call<ToolSearchStatus>('/api/tools/search'),
 searchTools:(query:string,topK=5)=>call<{matched_groups:any[];tip:string}>('/api/tools/search',{method:'POST',body:JSON.stringify({query,top_k:topK})}),
 mcpStatus:()=>call<McpStatus>('/api/mcp'),
 reloadMcp:()=>call<{status:McpStatus}>('/api/mcp/reload',{method:'POST'}),
 driftStatus:()=>call<{status:DriftStatus;runs:DriftRun[]}>('/api/drift'),
 runDrift:(force=false)=>call<{result:any;status:DriftStatus}>('/api/drift/run',{method:'POST',body:JSON.stringify({force})}),
 executeTool:(name:string,arguments_:Record<string,unknown>,confirmWrite=false)=>call<{name:string;ok:boolean;content:string;elapsed_ms:number}>(`/api/tools/${name}/execute`,{method:'POST',body:JSON.stringify({arguments:arguments_,confirm_write:confirmWrite})}),
 setupStatus:()=>call<SetupStatus>('/api/setup/status'),
 getModels:()=>call<SetupStatus>('/api/settings/models'),
 updateModels:(data:Partial<Record<'main'|'fast'|'embedding',{model?:string;base_url?:string;api_key?:string}>>)=>call<SetupStatus>('/api/settings/models',{method:'PUT',body:JSON.stringify(data)}),
 testModel:(slot:'main'|'fast'|'embedding')=>call<{ok:boolean;slot:string;message:string;status_code?:number}>('/api/settings/models/test',{method:'POST',body:JSON.stringify({slot})}),
 markdownStatus:()=>call<MarkdownStatus>('/api/markdown'),
 readMarkdown:(name:string)=>call<{name:string;content:string}>(`/api/markdown/${name}`),
 writeMarkdown:(name:string,content:string)=>call<{name:string;content:string}>(`/api/markdown/${name}`,{method:'PUT',body:JSON.stringify({content})}),
 syncMemoryMarkdown:()=>call<{name:string;content:string}>('/api/markdown/MEMORY/sync',{method:'POST'}),
}
