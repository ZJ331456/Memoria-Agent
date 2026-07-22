export type Session={id:string;title:string;created_at:string;updated_at:string;message_count:number}
export type Message={id:string;session_id:string;role:'user'|'assistant';content:string;created_at:string}
export type MemoryKind='fact'|'preference'|'profile'|'goal'|'procedure'
export type Memory={id:string;content:string;kind:MemoryKind;importance:number;source:string;created_at:string;updated_at:string;status:'active'|'superseded';reinforcement:number;supersedes_id:string|null;last_reinforced_at:string|null}
export type MemoryReindex={enabled:boolean;indexed:number;remaining:number}
export type MemoryWrite={action:'created'|'reinforced'|'superseded';memory:Memory;previous_id:string|null;reason:string}
export type Tool={name:string;description:string;risk:'read-only'|'write'|string}
export type Trace={id:string;session_id:string;status:string;steps:number;duration_ms:number;memories:Memory[];tools:Array<{name:string;ok:boolean;elapsed_ms:number;preview:string;arguments:Record<string,unknown>}>;error:string|null;created_at:string}
export type Overview={sessions:number;messages:number;memories:number;memories_superseded:number;traces:number;models:Record<string,any>;tools:Tool[];pipeline:Record<string,string[]>}
export class ApiError extends Error{constructor(message:string,public code:string,public requestId:string,public status:number){super(message)}}
const call=async<T>(path:string,init?:RequestInit):Promise<T>=>{const res=await fetch(path,{...init,headers:{'Content-Type':'application/json','X-Request-ID':crypto.randomUUID(),...init?.headers}});if(!res.ok){let body:any={};try{body=await res.json()}catch{}throw new ApiError(body.message||body.detail||res.statusText,body.code||'request_error',body.request_id||res.headers.get('X-Request-ID')||'',res.status)}return res.status===204?undefined as T:res.json()}
export const api={
 overview:()=>call<Overview>('/api/overview'), sessions:()=>call<Session[]>('/api/sessions'),
 createSession:(title='新对话')=>call<Session>('/api/sessions',{method:'POST',body:JSON.stringify({title})}),
 renameSession:(id:string,title:string)=>call<Session>(`/api/sessions/${id}`,{method:'PATCH',body:JSON.stringify({title})}),
 deleteSession:(id:string)=>call<void>(`/api/sessions/${id}`,{method:'DELETE'}), messages:(id:string)=>call<Message[]>(`/api/sessions/${id}/messages`),
 chat:(id:string,content:string)=>call<{message:Message;memories_created:Memory[];trace:Trace}>(`/api/sessions/${id}/chat`,{method:'POST',body:JSON.stringify({content})}),
 memories:(q='')=>call<Memory[]>(`/api/memories?q=${encodeURIComponent(q)}`), createMemory:(data:{content:string;kind:MemoryKind;importance:number})=>call<MemoryWrite>('/api/memories',{method:'POST',body:JSON.stringify(data)}),
 reindexMemories:(limit=1000)=>call<MemoryReindex>(`/api/memories/reindex?limit=${limit}`,{method:'POST'}),
 updateMemory:(id:string,data:Partial<Pick<Memory,'content'|'kind'|'importance'>>)=>call<Memory>(`/api/memories/${id}`,{method:'PATCH',body:JSON.stringify(data)}), deleteMemory:(id:string)=>call<void>(`/api/memories/${id}`,{method:'DELETE'}),
 traces:(sessionId='')=>call<Trace[]>(`/api/traces?session_id=${encodeURIComponent(sessionId)}`), tools:()=>call<Tool[]>('/api/tools'),
 executeTool:(name:string,arguments_:Record<string,unknown>,confirmWrite=false)=>call<{name:string;ok:boolean;content:string;elapsed_ms:number}>(`/api/tools/${name}/execute`,{method:'POST',body:JSON.stringify({arguments:arguments_,confirm_write:confirmWrite})})
}
