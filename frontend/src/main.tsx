import React,{useCallback,useEffect,useRef,useState}from'react'
import{createRoot}from'react-dom/client'
import{ThemeProvider}from'next-themes'
import{ActivityIcon,BrainIcon,DatabaseIcon,HistoryIcon,ListChecksIcon,MessageSquarePlusIcon,PlayIcon,PlusIcon,RefreshCwIcon,RotateCcwIcon,SearchIcon,SendIcon,Settings2Icon,Share2Icon,SquareIcon,Trash2Icon,Undo2Icon,WrenchIcon}from'lucide-react'
import{api,ApiError,type Memory,type MemoryJob,type MemoryKind,type MemoryReview,type MemoryUndo,type Message as MessageRecord,type Overview,type Session,type SetupStatus,type Trace}from'./api'
import{Alert,AlertAction,AlertDescription,AlertTitle}from'@/components/ui/alert'
import{AlertDialog,AlertDialogAction,AlertDialogCancel,AlertDialogContent,AlertDialogDescription,AlertDialogFooter,AlertDialogHeader,AlertDialogMedia,AlertDialogTitle,AlertDialogTrigger}from'@/components/ui/alert-dialog'
import{Badge}from'@/components/ui/badge'
import{Button}from'@/components/ui/button'
import{Bubble,BubbleContent}from'@/components/ui/bubble'
import{Card,CardContent,CardDescription,CardFooter,CardHeader,CardTitle}from'@/components/ui/card'
import{Empty,EmptyContent,EmptyDescription,EmptyHeader,EmptyMedia,EmptyTitle}from'@/components/ui/empty'
import{Field,FieldDescription,FieldGroup,FieldLabel}from'@/components/ui/field'
import{Input}from'@/components/ui/input'
import{Message as ChatMessage,MessageAvatar,MessageContent,MessageHeader}from'@/components/ui/message'
import{MessageScroller,MessageScrollerButton,MessageScrollerContent,MessageScrollerItem,MessageScrollerProvider,MessageScrollerViewport}from'@/components/ui/message-scroller'
import{Select,SelectContent,SelectGroup,SelectItem,SelectTrigger,SelectValue}from'@/components/ui/select'
import{Spinner}from'@/components/ui/spinner'
import{Table,TableBody,TableCell,TableHead,TableHeader,TableRow}from'@/components/ui/table'
import{Tabs,TabsContent,TabsList,TabsTrigger}from'@/components/ui/tabs'
import{Textarea}from'@/components/ui/textarea'
import{DotPattern}from'@/components/ui/dot-pattern'
import{MagicCard}from'@/components/ui/magic-card'
import'./styles.css'
import'./theme.css'
import{MemoryInspectDialog}from'./MemoryInspectDialog'
import{MemoryReviewQueue}from'./MemoryReviewQueue'
import{SharedGovernancePage}from'./SharedGovernancePage'

type Page='chat'|'memory'|'shared'|'runtime'|'tools'|'setup'
const memoryKinds:MemoryKind[]=['fact','preference','profile','goal','procedure']
const date=(value:string)=>new Intl.DateTimeFormat('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value))

function App(){
 const[page,setPage]=useState<Page>('chat'),[sessions,setSessions]=useState<Session[]>([]),[active,setActive]=useState(''),[focusMessageId,setFocusMessageId]=useState(''),[messages,setMessages]=useState<MessageRecord[]>([]),[memories,setMemories]=useState<Memory[]>([]),[jobs,setJobs]=useState<MemoryJob[]>([]),[reviews,setReviews]=useState<MemoryReview[]>([]),[traces,setTraces]=useState<Trace[]>([]),[overview,setOverview]=useState<Overview|null>(null),[setup,setSetup]=useState<SetupStatus|null>(null)
 const[text,setText]=useState(''),[query,setQuery]=useState(''),[searchQuery,setSearchQuery]=useState(''),[busy,setBusy]=useState(false),[reindexing,setReindexing]=useState(false),[memoryNotice,setMemoryNotice]=useState(''),[error,setError]=useState(''),[memoryDraft,setMemoryDraft]=useState<{content:string;kind:MemoryKind;importance:number}>({content:'',kind:'fact',importance:3}),[toolName,setToolName]=useState('calculate'),[toolArgs,setToolArgs]=useState('{"expression":"(27+15)*3"}'),[toolResult,setToolResult]=useState('')
 const[online,setOnline]=useState(false)
 const activeTurn=useRef<{sessionId:string;controller:AbortController}|null>(null)
 const setupRedirected=useRef(false)
 useEffect(()=>{const timer=window.setTimeout(()=>setSearchQuery(query.trim()),400);return()=>window.clearTimeout(timer)},[query])
 const refresh=useCallback(async()=>{
  const[s,m,j,r,t,o,st]=await Promise.allSettled([api.sessions(),api.memories(searchQuery),api.memoryJobs(),api.memoryReviews(),api.traces(active),api.overview(),api.setupStatus()] as const)
  if(s.status==='fulfilled')setSessions(s.value)
  if(m.status==='fulfilled')setMemories(m.value)
  if(j.status==='fulfilled')setJobs(j.value)
  if(r.status==='fulfilled')setReviews(r.value)
  if(t.status==='fulfilled')setTraces(t.value)
  if(o.status==='fulfilled')setOverview(o.value)
  setOnline(o.status==='fulfilled')
  if(st.status==='fulfilled'){setSetup(st.value);if(st.value.setup_needed&&!setupRedirected.current){setupRedirected.current=true;setPage('setup')}}
  const failure=[s,m,j,r,t,o,st].find(result=>result.status==='rejected')
  if(failure?.status==='rejected')showError(failure.reason)
 },[searchQuery,active])
 useEffect(()=>{refresh()},[refresh]);useEffect(()=>{if(active)api.messages(active,focusMessageId).then(setMessages).catch(showError);else setMessages([])},[active,focusMessageId])
 useEffect(()=>{if(page!=='memory'||!jobs.some(job=>['pending','running','retry'].includes(job.status)))return;const timer=window.setInterval(refresh,3000);return()=>window.clearInterval(timer)},[page,jobs,refresh])
 const showError=(value:unknown)=>{const e=value as ApiError;setError(e instanceof ApiError?`${e.message}${e.requestId?` · ${e.requestId}`:''}`:value instanceof Error?value.message:String(value))}
 const newSession=async()=>{try{const item=await api.createSession();setSessions(x=>[item,...x]);setFocusMessageId('');setActive(item.id);setPage('chat')}catch(e){showError(e)}}
 const send=async()=>{if(!text.trim()||busy)return;setBusy(true);setError('');try{let id=active;if(!id){const item=await api.createSession();id=item.id;setActive(id)}const content=text.trim(),stamp=Date.now(),streamId=`stream-${stamp}`,controller=new AbortController();activeTurn.current={sessionId:id,controller};setText('');setMessages(x=>[...x,{id:`pending-${stamp}`,session_id:id,role:'user',content,created_at:new Date().toISOString()}]);await api.chatStream(id,content,event=>{if(event.type==='delta'&&event.content)setMessages(items=>{const found=items.find(item=>item.id===streamId);return found?items.map(item=>item.id===streamId?{...item,content:item.content+event.content}:item):[...items,{id:streamId,session_id:id,role:'assistant',content:event.content,created_at:new Date().toISOString()}]});if(event.type==='error')throw new Error(event.message)},controller.signal);setFocusMessageId('');setMessages(await api.messages(id));await refresh()}catch(e){if((e as Error).name!=='AbortError')showError(e);const id=activeTurn.current?.sessionId;if(id)try{setMessages(await api.messages(id))}catch{}}finally{activeTurn.current=null;setBusy(false)}}
 const cancel=async()=>{const turn=activeTurn.current;if(!turn)return;turn.controller.abort();try{await api.cancelChat(turn.sessionId)}catch(e){showError(e)}finally{setBusy(false)}}
 const addMemory=async()=>{if(!memoryDraft.content.trim())return;try{const result=await api.createMemory(memoryDraft);const label={created:'已创建新记忆',reinforced:'已强化已有记忆',superseded:'已创建新版本并替代旧记忆'}[result.action];setMemoryNotice(`${label}${result.reason?` · ${result.reason}`:''}`);setMemoryDraft({...memoryDraft,content:''});await refresh()}catch(e){showError(e)}}
 const reindexMemories=async()=>{setReindexing(true);setMemoryNotice('');try{const result=await api.reindexMemories();setMemoryNotice(result.enabled?`本次写入 ${result.indexed} 条向量，剩余 ${result.remaining} 条`:'embedding 尚未配置');await refresh()}catch(e){showError(e)}finally{setReindexing(false)}}
 const retryJob=async(id:string)=>{try{await api.retryMemoryJob(id);setMemoryNotice('失败任务已重新排队');await refresh()}catch(e){showError(e)}}
 const undoJob=async(sourceRef:string)=>{const result=await api.undoMemories([sourceRef]);setMemoryNotice(`已撤销 ${result.affected_ids.length} 条变更，恢复 ${result.restored_ids.length} 条旧版本`);await refresh();return result}
 const jumpToSource=async(sourceRef:string)=>{try{const source=await api.messageSource(sourceRef);setActive(source.session_id);setFocusMessageId(source.message_id);setPage('chat')}catch(e){showError(e)}}
 const approveReview=async(id:string,data:{content:string;kind:MemoryKind;importance:number})=>{const result=await api.approveMemoryReview(id,data);const label=({created:'已写入新记忆',reinforced:'已强化已有记忆',superseded:'已替代旧记忆',skipped:'已跳过重复写入'} as Record<string,string>)[result.applied_action||'']||'已处理';setMemoryNotice(`候选记忆已批准：${label}`);await refresh()}
 const rejectReview=async(id:string)=>{await api.rejectMemoryReview(id);setMemoryNotice('候选记忆已拒绝');await refresh()}
 const runTool=async()=>{setBusy(true);setToolResult('');try{const args=JSON.parse(toolArgs);const result=await api.executeTool(toolName,args);setToolResult(result.content);await refresh()}catch(e){showError(e)}finally{setBusy(false)}}
 return <div className="app-shell"><DotPattern width={24} height={24} cr={0.75} className="ambient-pattern"/>
  <aside className="app-sidebar">
   <div className="sidebar-top"><div className="brand"><span className="brand-mark"><BrainIcon/></span><span><strong>Memoria</strong><small>PERSONAL AGENT</small></span></div><Button className="new-chat-button" onClick={newSession}><MessageSquarePlusIcon data-icon="inline-start"/>新建对话</Button></div>
   <div className="sidebar-label">工作空间</div>
   <Tabs value={page} onValueChange={value=>setPage(value as Page)} orientation="vertical"><TabsList className="nav-tabs"><TabsTrigger value="chat"><BrainIcon data-icon="inline-start"/>对话</TabsTrigger><TabsTrigger value="memory"><DatabaseIcon data-icon="inline-start"/>记忆</TabsTrigger><TabsTrigger value="shared"><Share2Icon data-icon="inline-start"/>共享治理</TabsTrigger><TabsTrigger value="runtime"><ActivityIcon data-icon="inline-start"/>追踪</TabsTrigger><TabsTrigger value="tools"><WrenchIcon data-icon="inline-start"/>工具</TabsTrigger><TabsTrigger value="setup"><Settings2Icon data-icon="inline-start"/>设置</TabsTrigger></TabsList></Tabs>
   <div className="session-heading">最近会话 <span>{sessions.length}</span></div><div className="session-list">{sessions.map(s=><button key={s.id} data-active={active===s.id} onClick={()=>{setFocusMessageId('');setActive(s.id);setPage('chat')}}><span className="truncate">{s.title}</span><small>{s.message_count} 条消息</small></button>)}</div>
   <div className="runtime-status" data-online={online}><span className="status-dot"/>{online?'服务已连接':'连接中或不可用'}</div>
  </aside>
  <main className="app-main">
   <div className="workspace-bar"><span>MEMORIA <span className="workspace-divider">/</span> {({chat:'对话',memory:'记忆',shared:'共享治理',runtime:'追踪',tools:'工具',setup:'设置'} as Record<Page,string>)[page]}</span><span className="workspace-edition">{page==='shared'?'GOVERNED SHARED MEMORY':'PERSONAL WORKSPACE'}</span></div>
   {error&&<Alert variant="destructive"><AlertTitle>请求失败</AlertTitle><AlertDescription>{error}</AlertDescription><AlertAction><Button variant="ghost" size="sm" onClick={()=>setError('')}>关闭</Button></AlertAction></Alert>}
   {setup?.setup_needed&&page!=='setup'&&page!=='shared'&&<Alert><AlertTitle>需要完成 Setup</AlertTitle><AlertDescription>主模型尚未完整配置。请先填写模型、Base URL 与 API Key。</AlertDescription><AlertAction><Button size="sm" onClick={()=>setPage('setup')}>打开设置</Button></AlertAction></Alert>}
   {page==='chat'&&<ChatPage active={active} messages={messages} focusMessageId={focusMessageId} text={text} busy={busy} onText={setText} onSend={send} onCancel={cancel} onNew={newSession}/>}
   {page==='memory'&&<MemoryPage items={memories} totalMemories={overview?.memories??memories.length} jobs={jobs} reviews={reviews} query={query} onQuery={setQuery} draft={memoryDraft} onDraft={setMemoryDraft} onAdd={addMemory} onDelete={async id=>{await api.deleteMemory(id);refresh()}} onCorrect={async(id,data)=>{const item=await api.correctMemory(id,data);setMemoryNotice('记忆已纠正，旧版本保留在时间线中');await refresh();return item}} onApproveReview={approveReview} onRejectReview={rejectReview} onSource={jumpToSource} onReindex={reindexMemories} onRetryJob={retryJob} onUndoJob={undoJob} reindexing={reindexing} memoryNotice={memoryNotice} onError={showError}/>}
   <div hidden={page!=='shared'}><SharedGovernancePage/></div>
   {page==='runtime'&&<RuntimePage overview={overview} traces={traces} onRunDrift={async(force)=>{try{await api.runDrift(force);await refresh()}catch(e){showError(e)}}}/>} 
   {page==='tools'&&<ToolsPage overview={overview} name={toolName} args={toolArgs} result={toolResult} busy={busy} onName={setToolName} onArgs={setToolArgs} onRun={runTool} onReloadMcp={async()=>{try{await api.reloadMcp();await refresh()}catch(e){showError(e)}}}/>}
   {page==='setup'&&<SetupPage setup={setup} onSaved={async()=>{await refresh();setPage('chat')}} onError={showError}/>}
  </main>
 </div>
}

function PageHeader({title,description,children}:{title:string;description:string;children?:React.ReactNode}){return <header className="page-header"><div><h1>{title}</h1><p>{description}</p></div>{children}</header>}

function ChatPage({active,messages,focusMessageId,text,busy,onText,onSend,onCancel,onNew}:{active:string;messages:MessageRecord[];focusMessageId:string;text:string;busy:boolean;onText:(v:string)=>void;onSend:()=>void;onCancel:()=>void;onNew:()=>void}){
 const suggestions=['记住我喜欢简洁的回答','我上次提到的目标是什么？','帮我整理今天的思路']
 useEffect(()=>{if(!focusMessageId||!messages.some(item=>item.id===focusMessageId))return;const timer=window.setTimeout(()=>document.getElementById(`message-${focusMessageId}`)?.scrollIntoView({block:'center',behavior:'smooth'}),80);return()=>window.clearTimeout(timer)},[focusMessageId,messages])
 return <section className="page page-chat">
  <PageHeader title="与 Memoria 对话" description="记住重要的事，让每次交流都有上下文"><Badge className="chat-state">{active?'当前会话':'新会话'}</Badge></PageHeader>
  {focusMessageId&&messages.some(item=>item.id===focusMessageId)&&<Alert><AlertTitle>已定位来源消息</AlertTitle><AlertDescription>下方高亮显示原始对话；如需查看最近消息，可重新点击左侧会话。</AlertDescription></Alert>}
  <Card className="chat-card"><CardContent className="chat-content"><MessageScrollerProvider><MessageScroller><MessageScrollerViewport><MessageScrollerContent className="message-content">
   {messages.length===0?<Empty className="chat-welcome"><EmptyHeader><EmptyMedia variant="icon"><BrainIcon/></EmptyMedia><EmptyTitle>今天想聊些什么？</EmptyTitle><EmptyDescription>Memoria 可以记住你的偏好、目标和对话里的重要细节。</EmptyDescription></EmptyHeader><EmptyContent><div className="prompt-grid">{suggestions.map(suggestion=><button key={suggestion} type="button" onClick={()=>onText(suggestion)}>{suggestion}<span>↗</span></button>)}</div><Button variant="outline" onClick={onNew}><PlusIcon data-icon="inline-start"/>创建新会话</Button></EmptyContent></Empty>:messages.map((item,index)=><MessageScrollerItem key={item.id} id={`message-${item.id}`} data-focused={item.id===focusMessageId} scrollAnchor={!focusMessageId&&index===messages.length-1}><ChatMessage align={item.role==='user'?'end':'start'}><MessageAvatar>{item.role==='user'?'你':'M'}</MessageAvatar><MessageContent><MessageHeader>{item.role==='user'?'YOU':'MEMORIA'} · {date(item.created_at)}</MessageHeader><Bubble variant={item.role==='user'?'default':'muted'} align={item.role==='user'?'end':'start'}><BubbleContent><p className="whitespace-pre-wrap">{item.content}</p></BubbleContent></Bubble></MessageContent></ChatMessage></MessageScrollerItem>)}
   {busy&&!messages.some(item=>item.id.startsWith('stream-'))&&<MessageScrollerItem><ChatMessage><MessageAvatar>M</MessageAvatar><MessageContent><Bubble variant="muted"><BubbleContent className="flex items-center gap-2"><Spinner/>正在召回记忆并推理…</BubbleContent></Bubble></MessageContent></ChatMessage></MessageScrollerItem>}
  </MessageScrollerContent></MessageScrollerViewport><MessageScrollerButton/></MessageScroller></MessageScrollerProvider></CardContent>
  <CardFooter className="composer"><Textarea value={text} onChange={e=>onText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();onSend()}}} placeholder="发消息给 Memoria…" aria-label="聊天消息"/><div className="composer-actions"><span>Enter 发送 · Shift + Enter 换行</span>{busy?<Button size="icon" variant="destructive" onClick={onCancel}><SquareIcon/><span className="sr-only">停止生成</span></Button>:<Button size="icon" onClick={onSend} disabled={!text.trim()}><SendIcon/><span className="sr-only">发送</span></Button>}</div></CardFooter></Card>
 </section>
}

type MemoryView = 'library' | 'review' | 'storage'
const kindFilterOptions:Array<MemoryKind|'all'>=['all','fact','preference','profile','goal','procedure']

function MemoryPage({items,totalMemories,jobs,reviews,query,onQuery,draft,onDraft,onAdd,onDelete,onCorrect,onApproveReview,onRejectReview,onSource,onReindex,onRetryJob,onUndoJob,reindexing,memoryNotice,onError}:{items:Memory[];totalMemories:number;jobs:MemoryJob[];reviews:MemoryReview[];query:string;onQuery:(v:string)=>void;draft:{content:string;kind:MemoryKind;importance:number};onDraft:(v:{content:string;kind:MemoryKind;importance:number})=>void;onAdd:()=>void;onDelete:(id:string)=>void;onCorrect:(id:string,data:{content:string;kind:MemoryKind;importance:number;reason:string})=>Promise<Memory>;onApproveReview:(id:string,data:{content:string;kind:MemoryKind;importance:number})=>Promise<void>;onRejectReview:(id:string)=>Promise<void>;onSource:(sourceRef:string)=>Promise<void>;onReindex:()=>void;onRetryJob:(id:string)=>Promise<void>;onUndoJob:(sourceRef:string)=>Promise<MemoryUndo>;reindexing:boolean;memoryNotice:string;onError:(e:unknown)=>void}){
 const[selectedMemory,setSelectedMemory]=useState<Memory|null>(null)
 const[kindFilter,setKindFilter]=useState<MemoryKind|'all'>('all')
 const visibleItems=kindFilter==='all'?items:items.filter(item=>item.kind===kindFilter)
 const[view,setView]=useState<MemoryView>(()=>{
  const saved=window.sessionStorage.getItem('memoria:memory-view')
  return saved==='review'||saved==='storage'?saved:'library'
 })
 useEffect(()=>{window.sessionStorage.setItem('memoria:memory-view',view)},[view])
 const failedJobs=jobs.filter(job=>job.status==='failed').length
 const reviewJobs=new Set(reviews.map(review=>review.job_id))
 return <section className="page memory-page" data-testid="memory-page">
  <PageHeader title="长期记忆" description="审核候选、检查有效记忆，以及查看底层存储与任务。">
   <Button variant="outline" size="sm" onClick={onReindex} disabled={reindexing}>{reindexing?<Spinner data-icon="inline-start"/>:<RefreshCwIcon data-icon="inline-start"/>}回填向量</Button>
  </PageHeader>
  <nav className="memory-view-nav" aria-label="记忆工作区">
   <button type="button" aria-pressed={view==='library'} onClick={()=>setView('library')}><DatabaseIcon/><span><strong>有效记忆库</strong><small>搜索、检查与纠正</small></span><em>{totalMemories}</em></button>
   <button type="button" aria-pressed={view==='review'} onClick={()=>setView('review')}><ListChecksIcon/><span><strong>待审核候选</strong><small>核对原话后决定是否写入</small></span><em>{reviews.length}</em></button>
   <button type="button" aria-pressed={view==='storage'} onClick={()=>setView('storage')}><HistoryIcon/><span><strong>存储与任务</strong><small>Markdown 视图、抽取记录</small></span>{failedJobs>0&&<em className="memory-nav-warning">{failedJobs} 失败</em>}</button>
  </nav>
  {memoryNotice&&<Alert role="status"><AlertTitle>记忆操作完成</AlertTitle><AlertDescription>{memoryNotice}</AlertDescription></Alert>}
  <div className="memory-view" hidden={view!=='library'}>
   <div className="memory-workspace">
    <Card className="memory-library-card"><CardHeader><CardTitle>有效记忆库</CardTitle><CardDescription>点击任意记忆的「检查并纠正」，会立即打开编辑窗口；旧版本保留在窗口内的时间线中。</CardDescription></CardHeader><CardContent>
     <Field><FieldLabel htmlFor="memory-search">搜索有效记忆</FieldLabel><div className="search-field"><SearchIcon/><Input id="memory-search" value={query} onChange={e=>onQuery(e.target.value)} placeholder="例如：我休息日喜欢做什么？"/></div></Field>
     <div className="memory-kind-filter" role="group" aria-label="按类型筛选">{kindFilterOptions.map(option=><button key={option} type="button" aria-pressed={kindFilter===option} className="kind-chip" onClick={()=>setKindFilter(option)}>{option==='all'?'全部':option}</button>)}</div>
     <div className="memory-results-heading"><span>{query.trim()?`匹配 ${visibleItems.length} 条`:`当前显示 ${visibleItems.length} 条`}</span><small>已替代的版本可在对应记忆的检查窗口查看</small></div>
     {visibleItems.length===0?<Empty><EmptyHeader><EmptyMedia variant="icon"><DatabaseIcon/></EmptyMedia><EmptyTitle>{query.trim()?'没有匹配记忆':'还没有有效记忆'}</EmptyTitle><EmptyDescription>{query.trim()?'试试其他关键词，或清空搜索。':'你可以手动添加，或先审核自动提取的候选。'}</EmptyDescription></EmptyHeader></Empty>:<div className="memory-list">{visibleItems.map(item=><Card key={item.id} className="memory-item"><CardHeader><CardTitle className="memory-title"><Badge>{item.kind}</Badge><span>{'★'.repeat(item.importance)}</span></CardTitle><CardDescription>{item.source} · 强化 {item.reinforcement} 次 · {date(item.updated_at)}</CardDescription></CardHeader><CardContent><p>{item.content}</p></CardContent><CardFooter><Button variant="outline" size="sm" onClick={()=>setSelectedMemory(item)}><HistoryIcon data-icon="inline-start"/>检查并纠正</Button><Button variant="ghost" size="sm" onClick={()=>onDelete(item.id)}><Trash2Icon data-icon="inline-start"/>删除</Button></CardFooter></Card>)}</div>}
    </CardContent></Card>
    <Card className="memory-add-card"><CardHeader><CardTitle>手动添加</CardTitle><CardDescription>主动记录确定的事实、偏好、目标或流程。</CardDescription></CardHeader><CardContent><FieldGroup><Field><FieldLabel htmlFor="memory-content">记忆内容</FieldLabel><Textarea id="memory-content" value={draft.content} onChange={e=>onDraft({...draft,content:e.target.value})} placeholder="例如：我偏好简洁的回答"/><FieldDescription>不要保存 API Key 或其他敏感凭据。</FieldDescription></Field><Field><FieldLabel>类型</FieldLabel><Select value={draft.kind} onValueChange={value=>onDraft({...draft,kind:value as MemoryKind})}><SelectTrigger><SelectValue/></SelectTrigger><SelectContent><SelectGroup>{memoryKinds.map(kind=><SelectItem key={kind} value={kind}>{kind}</SelectItem>)}</SelectGroup></SelectContent></Select></Field><Field><FieldLabel htmlFor="importance">重要度：{draft.importance}</FieldLabel><Input id="importance" type="range" min="1" max="5" value={draft.importance} onChange={e=>onDraft({...draft,importance:Number(e.target.value)})}/></Field></FieldGroup></CardContent><CardFooter><Button onClick={onAdd} disabled={!draft.content.trim()}><PlusIcon data-icon="inline-start"/>保存记忆</Button></CardFooter></Card>
   </div>
  </div>
  <div className="memory-view" hidden={view!=='review'}><MemoryReviewQueue items={reviews} onApprove={onApproveReview} onReject={onRejectReview} onSource={onSource} onError={onError}/></div>
  <div className="memory-view memory-storage" hidden={view!=='storage'}>
   <div className="memory-storage-guide"><div><strong>SQLite · 可靠来源</strong><p>有效记忆、审核结果和任务状态保存在本地数据库。</p></div><div><strong>Markdown · 可读视图</strong><p>SELF 可编辑；MEMORY 从有效记忆生成；PENDING 保留旧记录。</p></div></div>
   <MarkdownLayerPanel onError={onError}/>
   <Card><CardHeader><CardTitle className="flex items-center gap-2"><ListChecksIcon/>后台抽取任务</CardTitle><CardDescription>任务完成表示候选提取完成；候选仍需在「待审核候选」中批准才能生效。</CardDescription></CardHeader><CardContent>{jobs.length===0?<Empty><EmptyHeader><EmptyMedia variant="icon"><ListChecksIcon/></EmptyMedia><EmptyTitle>暂无后台任务</EmptyTitle><EmptyDescription>完成一轮对话后任务会显示在这里。</EmptyDescription></EmptyHeader></Empty>:<div className="memory-job-list">{jobs.map(job=><div className="memory-job-row" key={job.id} data-testid={`memory-job-${job.status}`}><div className="memory-job-main"><Badge variant={job.status==='failed'?'destructive':'secondary'}>{({pending:'待处理',running:'处理中',retry:'待重试',completed:'提取完成',failed:'失败'} as Record<string,string>)[job.status]||job.status}</Badge><button type="button" className="source-ref-link" title={job.source_ref} onClick={()=>onSource(job.source_ref)}>来源 ID：{job.source_ref.slice(0,12)}</button>{job.error&&<p className="job-error">{job.error}</p>}</div><div className="memory-job-meta">尝试 {job.attempts} 次 · {date(job.updated_at)}</div><div className="memory-job-action">{job.status==='failed'?<Button variant="outline" size="sm" onClick={()=>onRetryJob(job.id)}><RotateCcwIcon data-icon="inline-start"/>重试</Button>:job.status==='completed'&&reviewJobs.has(job.id)?<span className="text-muted-foreground">等待候选审核</span>:job.status==='completed'?<UndoJobDialog job={job} onUndo={onUndoJob}/>:<span className="text-muted-foreground">自动处理中</span>}</div></div>)}</div>}</CardContent></Card>
  </div>
  {selectedMemory&&<MemoryInspectDialog memory={selectedMemory} onClose={()=>setSelectedMemory(null)} onCorrect={async data=>{const next=await onCorrect(selectedMemory.id,data);setSelectedMemory(next)}} onSource={onSource} onError={onError}/>}
 </section>
}

type MarkdownName = 'SELF' | 'PENDING' | 'MEMORY'
const markdownDraftKey = (name: MarkdownName) => `memoria:markdown-draft:${name}`

function MarkdownLayerPanel({onError}:{onError:(e:unknown)=>void}){
 const[tab,setTab]=useState<MarkdownName>('SELF')
 const[content,setContent]=useState('')
 const[savedContent,setSavedContent]=useState('')
 const[loading,setLoading]=useState(true)
 const[busy,setBusy]=useState(false)
 const[notice,setNotice]=useState('')
 const onErrorRef=useRef(onError)
 onErrorRef.current=onError
 useEffect(()=>{
  let cancelled=false
  setLoading(true)
  api.readMarkdown(tab).then(item=>{
   if(cancelled)return
   const cached=tab==='MEMORY'?null:window.sessionStorage.getItem(markdownDraftKey(tab))
   if(cached===item.content)window.sessionStorage.removeItem(markdownDraftKey(tab))
   setSavedContent(item.content)
   setContent(cached??item.content)
   if(cached!==null&&cached!==item.content)setNotice('已恢复此浏览器标签页中未保存的草稿。')
  }).catch(error=>{if(!cancelled){setContent('');setSavedContent('');setNotice('文件读取失败，请重试。');onErrorRef.current(error)}}).finally(()=>{if(!cancelled)setLoading(false)})
  return()=>{cancelled=true}
 },[tab])
 const dirty=tab!=='MEMORY'&&content!==savedContent
 const update=(value:string)=>{
  setContent(value)
  if(value===savedContent)window.sessionStorage.removeItem(markdownDraftKey(tab))
  else window.sessionStorage.setItem(markdownDraftKey(tab),value)
 }
 const changeTab=(name:MarkdownName)=>{if(name===tab)return;setNotice('');setTab(name)}
 const save=async()=>{if(!dirty||loading||busy)return;setBusy(true);setNotice('');try{await api.writeMarkdown(tab,content);window.sessionStorage.removeItem(markdownDraftKey(tab));setSavedContent(content);setNotice(`${tab}.md 已保存到本地文件`)}catch(e){onError(e)}finally{setBusy(false)}}
 const sync=async()=>{setBusy(true);setNotice('');try{await api.syncMemoryMarkdown();setTab('MEMORY');setNotice('MEMORY.md 已从有效记忆重新生成')}catch(e){onError(e)}finally{setBusy(false)}}
 return <Card data-testid="markdown-layer"><CardHeader><CardTitle>Markdown 存储视图</CardTitle><CardDescription>文件视图供检查和编辑；有效记忆与审核状态以 SQLite 为准。</CardDescription></CardHeader><CardContent>
  <div className="markdown-file-nav" aria-label="Markdown 文件"><Button size="sm" variant={tab==='SELF'?'default':'outline'} onClick={()=>changeTab('SELF')}>SELF.md</Button><Button size="sm" variant={tab==='MEMORY'?'default':'outline'} onClick={()=>changeTab('MEMORY')}>MEMORY.md</Button><Button size="sm" variant={tab==='PENDING'?'default':'outline'} onClick={()=>changeTab('PENDING')}>PENDING.md</Button></div>
  <p className="markdown-file-hint">{tab==='SELF'?'用户可编辑的长期设定，会注入对话上下文。':tab==='MEMORY'?'由有效记忆生成的只读快照；修改记忆请回到有效记忆库。':'旧版待处理记录；新的自动提取候选请在待审核候选中处理。'}</p>
  {notice&&<p className="markdown-notice" role="status">{notice}</p>}
  <div className="markdown-editor-heading"><span>{tab}.md</span><small>{loading?'读取中…':dirty?'草稿未保存 · 暂存于当前浏览器标签页':tab==='MEMORY'?'只读':'已保存'}</small></div>
  {loading?<div className="markdown-loading"><Spinner/>正在读取文件…</div>:<Textarea className="font-mono min-h-56" value={content} onChange={e=>update(e.target.value)} readOnly={tab==='MEMORY'||busy} aria-label={`${tab}.md 内容`}/>}
 </CardContent><CardFooter className="markdown-footer">{tab==='MEMORY'?<span className="text-sm text-muted-foreground">此文件只读，保存记忆后可同步更新。</span>:<Button onClick={save} disabled={!dirty||busy||loading}>{busy?<Spinner data-icon="inline-start"/>:null}保存 {tab}.md</Button>}<Button size="sm" variant="outline" onClick={sync} disabled={busy||loading}><RefreshCwIcon data-icon="inline-start"/>同步 MEMORY.md</Button></CardFooter></Card>
}

function SetupPage({setup,onSaved,onError}:{setup:SetupStatus|null;onSaved:()=>Promise<void>;onError:(e:unknown)=>void}){
 type Slot='main'|'fast'|'embedding'
 const[draft,setDraft]=useState<Record<Slot,{model:string;base_url:string;api_key:string}>>({main:{model:'',base_url:'',api_key:''},fast:{model:'',base_url:'',api_key:''},embedding:{model:'',base_url:'',api_key:''}})
 const[busy,setBusy]=useState(false),[message,setMessage]=useState('')
 useEffect(()=>{if(!setup)return;setDraft({main:{model:setup.main.model,base_url:setup.main.base_url,api_key:''},fast:{model:setup.fast.model,base_url:setup.fast.base_url,api_key:''},embedding:{model:setup.embedding.model,base_url:setup.embedding.base_url,api_key:''}})},[setup])
 const update=(slot:Slot,key:'model'|'base_url'|'api_key',value:string)=>setDraft(current=>({...current,[slot]:{...current[slot],[key]:value}}))
 const save=async()=>{setBusy(true);setMessage('');try{const payload:Partial<Record<Slot,{model?:string;base_url?:string;api_key?:string}>>={};(Object.keys(draft) as Slot[]).forEach(slot=>{const item=draft[slot];payload[slot]={model:item.model,base_url:item.base_url,...(item.api_key?{api_key:item.api_key}:{})}});await api.updateModels(payload);setMessage('模型配置已保存（密钥不会回显）');await onSaved()}catch(e){onError(e)}finally{setBusy(false)}}
 const test=async(slot:Slot)=>{setBusy(true);setMessage('');try{const result=await api.testModel(slot);setMessage(result.ok?`${slot} 连通正常`:`${slot} 失败：${result.message}`)}catch(e){onError(e)}finally{setBusy(false)}}
 const slots:Array<[Slot,string]> =[['main','主模型'],['fast','快速模型'],['embedding','Embedding']]
 return <section className="page" data-testid="setup-page"><PageHeader title="模型与 Setup" description="在页面热更新模型配置并测试连通性；密钥只写入本地 data/models.override.toml，API 永不回显。"><Badge variant={setup?.setup_needed?'destructive':'secondary'}>{setup?.setup_needed?'待配置':'已就绪'}</Badge></PageHeader>{message&&<Alert><AlertTitle>设置</AlertTitle><AlertDescription>{message}</AlertDescription></Alert>}<div className="two-column">{slots.map(([slot,label])=><Card key={slot}><CardHeader><CardTitle>{label}</CardTitle><CardDescription>{setup?.[slot].configured?'密钥已配置':'密钥未配置'} · 留空 API Key 表示保持原值</CardDescription></CardHeader><CardContent><FieldGroup><Field><FieldLabel>Model</FieldLabel><Input value={draft[slot].model} onChange={e=>update(slot,'model',e.target.value)}/></Field><Field><FieldLabel>Base URL</FieldLabel><Input value={draft[slot].base_url} onChange={e=>update(slot,'base_url',e.target.value)}/></Field><Field><FieldLabel>API Key</FieldLabel><Input type="password" value={draft[slot].api_key} onChange={e=>update(slot,'api_key',e.target.value)} placeholder={setup?.[slot].configured?'已配置，输入以覆盖':''}/></Field></FieldGroup></CardContent><CardFooter className="gap-2"><Button variant="outline" onClick={()=>test(slot)} disabled={busy}>测试连通</Button></CardFooter></Card>)}</div><div className="mt-4"><Button onClick={save} disabled={busy}>{busy?<Spinner data-icon="inline-start"/>:null}保存并继续</Button></div></section>
}

function UndoJobDialog({job,onUndo}:{job:MemoryJob;onUndo:(sourceRef:string)=>Promise<MemoryUndo>}){
 const[open,setOpen]=useState(false),[preview,setPreview]=useState<MemoryUndo|null>(null),[loading,setLoading]=useState(false),[dialogError,setDialogError]=useState('')
 const changeOpen=async(next:boolean)=>{setOpen(next);if(!next)return;setLoading(true);setDialogError('');try{setPreview(await api.undoMemories([job.source_ref],true))}catch(e){setDialogError(e instanceof Error?e.message:String(e))}finally{setLoading(false)}}
 const confirm=async()=>{setLoading(true);setDialogError('');try{await onUndo(job.source_ref);setOpen(false)}catch(e){setDialogError(e instanceof Error?e.message:String(e))}finally{setLoading(false)}}
 return <AlertDialog open={open} onOpenChange={changeOpen}><AlertDialogTrigger render={<Button variant="outline" size="sm"/>}><Undo2Icon data-icon="inline-start"/>撤销</AlertDialogTrigger><AlertDialogContent><AlertDialogHeader><AlertDialogMedia><Undo2Icon/></AlertDialogMedia><AlertDialogTitle>撤销这次自动记忆？</AlertDialogTitle><AlertDialogDescription>{loading?'正在预览影响…':dialogError||`将停用 ${preview?.affected_ids.length??0} 条变更，并恢复 ${preview?.restored_ids.length??0} 条旧版本。版本历史不会删除。`}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={loading}>取消</AlertDialogCancel><AlertDialogAction variant="destructive" disabled={loading||!!dialogError} onClick={confirm}>{loading?<Spinner data-icon="inline-start"/>:<Undo2Icon data-icon="inline-start"/>}确认撤销</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}

function MetricCard({label,value}:{label:string;value:unknown}){return <MagicCard className="rounded-xl" gradientColor="hsl(var(--primary) / 0.12)" gradientOpacity={1}><Card className="metric-card"><CardHeader><CardDescription>{label}</CardDescription><CardTitle className="metric-value">{String(value??"—")}</CardTitle></CardHeader></Card></MagicCard>}

function RuntimePage({overview,traces,onRunDrift}:{overview:Overview|null;traces:Trace[];onRunDrift:(force:boolean)=>void}){const drift=overview?.drift;return <section className="page"><PageHeader title="运行追踪" description="检查每轮推理的耗时、工具步骤、状态与空闲 Drift"><Badge>{traces.length} 条最近记录</Badge></PageHeader><div className="metric-grid">{[['会话',overview?.sessions],['消息',overview?.messages],['有效记忆',overview?.memories],['已替代',overview?.memories_superseded],['待处理任务',overview?.memory_jobs_pending],['失败任务',overview?.memory_jobs_failed],['Drift 次数',overview?.drift_runs],['Trace',overview?.traces]].map(([label,value])=><MetricCard key={label as string} label={String(label)} value={value}/>)}</div><Card><CardHeader><CardTitle>Drift 空闲任务</CardTitle><CardDescription>无人对话时按预算跑限定技能；写工具受白名单约束。</CardDescription></CardHeader><CardContent><div className="metric-grid"><MetricCard label="开关" value={drift?.enabled?'已启用':'关闭'}/><MetricCard label="今日/预算" value={`${drift?.runs_today??0}/${drift?.daily_budget??'—'}`}/><MetricCard label="空闲秒" value={drift?.idle_seconds??'—'}/><MetricCard label="技能" value={(drift?.allowed_skills||[]).join(', ')||'—'}/></div>{drift?.last_run&&<p className="text-muted-foreground mt-3 text-sm">最近：{drift.last_run.status} · {drift.last_run.skill} · {(drift.last_run.summary||drift.last_run.error||'').slice(0,120)}</p>}</CardContent><CardFooter className="gap-2"><Button variant="outline" onClick={()=>onRunDrift(false)}><PlayIcon data-icon="inline-start"/>按规则跑一轮</Button><Button onClick={()=>onRunDrift(true)}><RefreshCwIcon data-icon="inline-start"/>强制跑一轮</Button></CardFooter></Card><Card><CardHeader><CardTitle>Turn Trace</CardTitle><CardDescription>选择左侧会话后仅显示该会话；未选择时显示全局记录。</CardDescription></CardHeader><CardContent>{traces.length===0?<Empty><EmptyHeader><EmptyMedia variant="icon"><ActivityIcon/></EmptyMedia><EmptyTitle>暂无运行记录</EmptyTitle><EmptyDescription>发送消息后这里会出现完整 trace。</EmptyDescription></EmptyHeader></Empty>:<Table><TableHeader><TableRow><TableHead>状态</TableHead><TableHead>时间</TableHead><TableHead>耗时</TableHead><TableHead>步骤</TableHead><TableHead>工具链</TableHead></TableRow></TableHeader><TableBody>{traces.map(trace=><TableRow key={trace.id}><TableCell><Badge>{trace.status}</Badge></TableCell><TableCell>{date(trace.created_at)}</TableCell><TableCell>{trace.duration_ms} ms</TableCell><TableCell>{trace.steps}</TableCell><TableCell>{trace.tools.length?trace.tools.map(tool=><Badge key={`${trace.id}-${tool.name}`}>{tool.name} · {tool.elapsed_ms}ms</Badge>):<span className="text-muted-foreground">无工具</span>}{trace.error&&<p className="text-destructive">{trace.error}</p>}</TableCell></TableRow>)}</TableBody></Table>}</CardContent></Card></section>}

function ToolsPage({overview,name,args,result,busy,onName,onArgs,onRun,onReloadMcp}:{overview:Overview|null;name:string;args:string;result:string;busy:boolean;onName:(v:string)=>void;onArgs:(v:string)=>void;onRun:()=>void;onReloadMcp:()=>void}){const tools=overview?.tools??[];const search=overview?.tool_search;const mcp=overview?.mcp;return <section className="page"><PageHeader title="工具实验台" description="Tool Search 按需暴露 schema；MCP 接入外部工具生态"><Badge>{tools.length} 个工具</Badge></PageHeader><div className="metric-grid"><MetricCard label="Tool Search" value={search?.enabled?'已启用':'关闭'}/><MetricCard label="直连工具" value={search?.direct_tools?.length??'—'}/><MetricCard label="可搜索工具" value={search?.searchable_tools?.length??'—'}/><MetricCard label="MCP 工具" value={mcp?.tool_count??0}/></div><div className="two-column"><Card><CardHeader><CardTitle>执行工具</CardTitle><CardDescription>默认示例调用安全计算器。写工具需要额外确认，前端暂不开放。</CardDescription></CardHeader><CardContent><FieldGroup><Field><FieldLabel>工具</FieldLabel><Select value={name} onValueChange={value=>onName(value as string)}><SelectTrigger><SelectValue/></SelectTrigger><SelectContent><SelectGroup>{tools.filter(t=>t.risk==='read-only').map(tool=><SelectItem key={tool.name} value={tool.name}>{tool.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field><Field><FieldLabel htmlFor="tool-args">JSON 参数</FieldLabel><Textarea id="tool-args" value={args} onChange={e=>onArgs(e.target.value)} className="font-mono"/></Field></FieldGroup></CardContent><CardFooter><Button onClick={onRun} disabled={busy}>{busy?<Spinner data-icon="inline-start"/>:<PlayIcon data-icon="inline-start"/>}运行</Button></CardFooter></Card><Card><CardHeader><CardTitle>执行结果</CardTitle><CardDescription>结果来自受参数校验和超时保护的 ToolRegistry。</CardDescription></CardHeader><CardContent>{result?<pre className="tool-result">{result}</pre>:<Empty><EmptyHeader><EmptyMedia variant="icon"><Settings2Icon/></EmptyMedia><EmptyTitle>等待执行</EmptyTitle><EmptyDescription>选择工具、填写 JSON 参数，然后点击运行。</EmptyDescription></EmptyHeader></Empty>}</CardContent></Card></div><Card><CardHeader><CardTitle>MCP Servers</CardTitle><CardDescription>配置文件：{mcp?.config_path||'data/mcp_servers.json'}</CardDescription></CardHeader><CardContent>{!mcp||mcp.servers.length===0?<Empty><EmptyHeader><EmptyTitle>尚未配置 MCP</EmptyTitle><EmptyDescription>复制 mcp_servers.example.json 到 data/mcp_servers.json 后点击重载。</EmptyDescription></EmptyHeader></Empty>:<Table><TableHeader><TableRow><TableHead>名称</TableHead><TableHead>状态</TableHead><TableHead>工具</TableHead><TableHead>错误</TableHead></TableRow></TableHeader><TableBody>{mcp.servers.map(server=><TableRow key={server.name}><TableCell className="font-mono">{server.name}</TableCell><TableCell><Badge>{server.connected?'已连接':server.enabled?'未连接':'禁用'}</Badge></TableCell><TableCell>{server.tools.join(', ')||'—'}</TableCell><TableCell className="text-muted-foreground">{server.error||'—'}</TableCell></TableRow>)}</TableBody></Table>}</CardContent><CardFooter><Button variant="outline" onClick={onReloadMcp} disabled={busy}><RefreshCwIcon data-icon="inline-start"/>重载 MCP</Button></CardFooter></Card><Card><CardHeader><CardTitle>工具目录</CardTitle></CardHeader><CardContent><Table><TableHeader><TableRow><TableHead>名称</TableHead><TableHead>组</TableHead><TableHead>描述</TableHead><TableHead>风险</TableHead><TableHead>直连</TableHead></TableRow></TableHeader><TableBody>{tools.map(tool=><TableRow key={tool.name}><TableCell className="font-mono">{tool.name}</TableCell><TableCell>{tool.owner||'builtin'}</TableCell><TableCell>{tool.description}</TableCell><TableCell><Badge>{tool.risk}</Badge></TableCell><TableCell>{tool.always_on?'yes':'search'}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card></section>}

createRoot(document.getElementById('root')!).render(<React.StrictMode><ThemeProvider attribute="class" forcedTheme="light"><App/></ThemeProvider></React.StrictMode>)
