export type Session={id:string;title:string;created_at:string;updated_at:string;message_count:number}
export type Message={id:string;session_id:string;role:'user'|'assistant';content:string;created_at:string}
export type MemoryKind='fact'|'preference'|'profile'|'goal'|'procedure'
export type Memory={id:string;content:string;kind:MemoryKind;importance:number;source:string;created_at:string;updated_at:string;status:'active'|'superseded';reinforcement:number;supersedes_id:string|null;last_reinforced_at:string|null}
export type MemoryReindex={enabled:boolean;indexed:number;remaining:number}
export type MemoryWrite={action:'created'|'reinforced'|'superseded';memory:Memory;previous_id:string|null;reason:string}
export type MemoryJob={id:string;source_ref:string;status:'pending'|'running'|'retry'|'completed'|'failed';attempts:number;error:string|null;available_at:string|null;lease_owner:string|null;lease_expires_at:string|null;created_at:string;updated_at:string}
export type MemoryUndo={affected_ids:string[];restored_ids:string[]}
export type Tool={name:string;description:string;risk:'read-only'|'write'|string}
export type Trace={id:string;session_id:string;status:string;steps:number;duration_ms:number;memories:Memory[];tools:Array<{name:string;ok:boolean;elapsed_ms:number;preview:string;arguments:Record<string,unknown>}>;metadata:Record<string,any>;error:string|null;created_at:string}
export type Overview={sessions:number;messages:number;memories:number;memories_superseded:number;traces:number;memory_jobs_pending:number;memory_jobs_failed:number;models:Record<string,any>;vector_index?:{enabled:boolean;backend:string;dimension:number|null;error:string};tools:Tool[];pipeline:Record<string,string[]>}
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
 reindexMemories:(limit=1000)=>call<MemoryReindex>(`/api/memories/reindex?limit=${limit}`,{method:'POST'}),
 memoryJobs:(limit=50)=>call<MemoryJob[]>(`/api/memory-jobs?limit=${limit}`), retryMemoryJob:(id:string)=>call<MemoryJob>(`/api/memory-jobs/${id}/retry`,{method:'POST'}),
 undoMemories:(sourceRefs:string[],dryRun=false)=>call<MemoryUndo>('/api/memories/undo',{method:'POST',body:JSON.stringify({source_refs:sourceRefs,dry_run:dryRun})}),
 updateMemory:(id:string,data:Partial<Pick<Memory,'content'|'kind'|'importance'>>)=>call<Memory>(`/api/memories/${id}`,{method:'PATCH',body:JSON.stringify(data)}), deleteMemory:(id:string)=>call<void>(`/api/memories/${id}`,{method:'DELETE'}),
 traces:(sessionId='')=>call<Trace[]>(`/api/traces?session_id=${encodeURIComponent(sessionId)}`), tools:()=>call<Tool[]>('/api/tools'),
 executeTool:(name:string,arguments_:Record<string,unknown>,confirmWrite=false)=>call<{name:string;ok:boolean;content:string;elapsed_ms:number}>(`/api/tools/${name}/execute`,{method:'POST',body:JSON.stringify({arguments:arguments_,confirm_write:confirmWrite})})
}
