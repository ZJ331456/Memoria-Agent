import { expect, test, type Page, type Route } from '@playwright/test'

const session={id:'session-1',title:'E2E 会话',created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z',message_count:0}
const memory={id:'memory-1',content:'现在喜欢乌龙茶',kind:'preference',importance:4,source:'conversation',source_ref:'source-1',created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z',status:'active',reinforcement:1,supersedes_id:'memory-0',last_reinforced_at:null}
const jobs=[
 {id:'failed-job',source_ref:'source-failed-123',status:'failed',attempts:3,error:'provider unavailable',available_at:'2026-07-23T00:00:00Z',lease_owner:null,lease_expires_at:null,created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z'},
 {id:'completed-job',source_ref:'source-complete-456',status:'completed',attempts:1,error:null,available_at:'2026-07-23T00:00:00Z',lease_owner:null,lease_expires_at:null,created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z'},
]

async function mockApi(page:Page){
 let chatCompleted=false
 await page.route('**/api/**',async(route:Route)=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname
  const json=(value:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)})
  if(path==='/api/sessions/session-1/chat/stream'){
   chatCompleted=true
   return route.fulfill({status:200,contentType:'text/event-stream',body:'data: {"type":"delta","content":"流式回复"}\n\ndata: {"type":"complete"}\n\n'})
  }
  if(path==='/api/sessions/session-1/messages')return json(url.searchParams.has('anchor_id')?[{id:'source-1',session_id:'session-1',role:'user',content:'原始偏好：喜欢红茶',created_at:'2026-07-23T00:00:00Z'}]:chatCompleted?[{id:'assistant-1',session_id:'session-1',role:'assistant',content:'流式回复',created_at:'2026-07-23T00:00:01Z'}]:[])
  if(path==='/api/messages/source-1/source')return json({message_id:'source-1',session_id:'session-1',session_title:'E2E 会话',role:'user',content:'原始偏好：喜欢红茶',created_at:'2026-07-23T00:00:00Z'})
  if(path==='/api/sessions')return json([session])
  if(path==='/api/memories/memory-1/timeline')return json([{...memory,id:'memory-0',content:'以前喜欢红茶',status:'superseded',supersedes_id:null,replacement_reason:null}, {...memory,replacement_reason:'偏好发生变化'}])
  if(path==='/api/memories')return json([memory])
  if(path==='/api/markdown/SELF')return json({name:'SELF',content:''})
  if(path==='/api/setup/status')return json({main:{model:'mock',base_url:'',configured:true},fast:{model:'',base_url:'',configured:false},embedding:{model:'',base_url:'',configured:false},setup_needed:false})
  if(path==='/api/memory-jobs/failed-job/retry')return json({...jobs[0],status:'pending',attempts:0,error:null})
  if(path==='/api/memory-jobs')return json(jobs)
  if(path==='/api/memory-reviews')return json([])
  if(path==='/api/memories/undo')return json({affected_ids:['memory-1'],restored_ids:['memory-old']})
  if(path==='/api/traces')return json([])
  if(path==='/api/overview')return json({sessions:1,messages:0,memories:1,memories_superseded:0,traces:0,memory_jobs_pending:0,memory_jobs_failed:1,models:{},tools:[],pipeline:{}})
  return json({code:'not_found',message:`unmocked ${path}`,request_id:'e2e'},404)
 })
}

test.beforeEach(async({page})=>{await mockApi(page);await page.goto('/')})

test('chat streams without blanking the application shell',async({page})=>{
 await expect(page.getByText('Memoria',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:/E2E 会话/}).click()
 await page.getByLabel('聊天消息').fill('测试流式回复')
 await page.getByRole('button',{name:'发送'}).click()
 await expect(page.getByText('流式回复',{exact:true})).toBeVisible()
 await expect(page.locator('.app-shell')).toBeVisible()
})

test('memory operations preview undo and retry failed jobs',async({page})=>{
 await page.getByRole('tab',{name:'记忆'}).click()
 await expect(page.getByTestId('memory-page')).toBeVisible()
 await page.getByRole('button',{name:/存储与任务/}).click()
 await page.getByTestId('memory-job-failed').getByRole('button',{name:'重试'}).click()
 await expect(page.getByText('失败任务已重新排队')).toBeVisible()
 await page.getByTestId('memory-job-completed').getByRole('button',{name:'撤销'}).click()
 await expect(page.getByRole('heading',{name:'撤销这次自动记忆？'})).toBeVisible()
 await expect(page.getByText(/停用 1 条变更，并恢复 1 条旧版本/)).toBeVisible()
 await page.getByRole('button',{name:'确认撤销'}).click()
 await expect(page.getByText(/已撤销 1 条变更，恢复 1 条旧版本/)).toBeVisible()
})

test('memory timeline opens on demand and mobile navigation keeps all sections',async({page})=>{
 await page.getByRole('tab',{name:'记忆'}).click()
 await expect(page.getByRole('tab',{name:'记忆'})).toHaveAttribute('data-active')
 await page.getByRole('button',{name:'检查并纠正'}).click()
 await expect(page.getByRole('dialog',{name:'检查并纠正记忆'})).toBeVisible()
 await expect(page.getByLabel('纠正后的记忆')).toBeFocused()
 await expect(page.getByRole('button',{name:'保存纠正'})).toBeInViewport()
 await expect(page.getByTestId('memory-timeline')).toContainText('以前喜欢红茶')
 await expect(page.getByTestId('memory-timeline')).toContainText('现在喜欢乌龙茶')
 await page.setViewportSize({width:390,height:844})
 for(const label of ['对话','记忆','共享治理','追踪','工具','设置'])await expect(page.getByRole('tab',{name:label})).toBeVisible()
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)
 expect(overflow).toBe(false)
 await page.keyboard.press('Escape')
 await expect(page.getByRole('dialog',{name:'检查并纠正记忆'})).not.toBeVisible()
})

test('memory correction creates a visible version with a reason',async({page})=>{
 let current={...memory}
 await page.route('**/api/memories',route=>route.fulfill({contentType:'application/json',body:JSON.stringify([current])}))
 await page.route('**/api/memories/memory-1/correct',async route=>{
  const body=route.request().postDataJSON()
  current={...current,id:'memory-2',content:body.content,kind:body.kind,importance:body.importance,source:'user_correction',supersedes_id:'memory-1'}
  await route.fulfill({contentType:'application/json',body:JSON.stringify(current)})
 })
 await page.route('**/api/memories/memory-2/timeline',route=>route.fulfill({contentType:'application/json',body:JSON.stringify([{...memory,status:'superseded',replacement_reason:null,replacement_relation:null},{...current,replacement_reason:'用户更正',replacement_relation:'correction'}])}))
 await page.getByRole('tab',{name:'记忆'}).click()
 await page.getByRole('button',{name:'检查并纠正'}).click()
 await page.getByLabel('纠正后的记忆').fill('现在喜欢普洱茶')
 await page.getByLabel('纠正原因').fill('用户更正')
 await page.getByRole('button',{name:'保存纠正'}).click()
 await expect(page.getByText('纠正已保存，旧版本保留在下方时间线中。')).toBeVisible()
 await expect(page.getByTestId('memory-timeline')).toContainText('用户更正')
 await expect(page.getByTestId('memory-timeline')).toContainText('现在喜欢普洱茶')
})

test('review candidates stay in queue until approved and source opens the original message',async({page})=>{
 const review={id:'review-1',job_id:'completed-job',source_ref:'source-1',ordinal:0,content:'可能喜欢红茶',kind:'preference',importance:3,status:'pending',applied_memory_id:null,applied_action:null,created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z'}
 let pending=[review]
 await page.route('**/api/memory-reviews?*',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(pending)}))
 await page.route('**/api/memory-reviews/review-1/approve',async route=>{
  pending=[]
  await route.fulfill({contentType:'application/json',body:JSON.stringify({...review,status:'approved',applied_action:'created',applied_memory_id:'memory-2'})})
 })
 await page.reload()
 await page.getByRole('tab',{name:'记忆'}).click()
 await page.getByRole('button',{name:/待审核候选/}).click()
 await expect(page.getByTestId('memory-review-item')).toHaveCount(1)
 await page.getByTestId('memory-review-item').getByRole('button',{name:'来源 ID：source-1'}).click()
 await expect(page.getByText('原始偏好：喜欢红茶')).toBeVisible()
 await expect(page.locator('[data-focused="true"]')).toHaveCount(1)
 await page.getByRole('tab',{name:'记忆'}).click()
 await page.getByTestId('memory-review-item').getByLabel('候选内容').fill('现在喜欢乌龙茶')
 await page.getByRole('button',{name:'批准并写入'}).click()
 await expect(page.getByTestId('memory-review-item')).toHaveCount(0)
 await expect(page.getByText(/候选记忆已批准/)).toBeVisible()
})

test('Markdown edits remain as a labeled draft across sections and are cleared after saving',async({page})=>{
 await page.route('**/api/markdown/SELF',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({name:'SELF',content:'已保存设定'})}))
 await page.route('**/api/markdown/MEMORY',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({name:'MEMORY',content:'有效记忆快照'})}))
 await page.route('**/api/markdown/SELF',async route=>{
  if(route.request().method()==='PUT')return route.fulfill({contentType:'application/json',body:JSON.stringify({name:'SELF',content:route.request().postDataJSON().content})})
  return route.fallback()
 })
 await page.getByRole('tab',{name:'记忆'}).click()
 await page.getByRole('button',{name:/存储与任务/}).click()
 await page.getByLabel('SELF.md 内容').fill('还没保存的设定')
 await expect(page.getByText(/草稿未保存/)).toBeVisible()
 await page.getByRole('button',{name:'MEMORY.md',exact:true}).click()
 await expect(page.getByLabel('MEMORY.md 内容')).toHaveValue('有效记忆快照')
 await page.getByRole('tab',{name:'对话'}).click()
 await page.getByRole('tab',{name:'记忆'}).click()
 await page.getByRole('button',{name:'SELF.md',exact:true}).click()
 await expect(page.getByLabel('SELF.md 内容')).toHaveValue('还没保存的设定')
 await page.getByRole('button',{name:'保存 SELF.md'}).click()
 await expect(page.getByText('SELF.md 已保存到本地文件')).toBeVisible()
 expect(await page.evaluate(()=>sessionStorage.getItem('memoria:markdown-draft:SELF'))).toBeNull()
})

test('a failed setup request does not hide conversations',async({page})=>{
 await page.route('**/api/setup/status',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'setup unavailable'})}))
 await page.reload()
 await expect(page.getByRole('button',{name:/E2E 会话/})).toBeVisible()
 await expect(page.getByText('请求失败')).toBeVisible()
})

test('shared governance runs from agent setup through approval and audit without persisting its key',async({page})=>{
 const stamp='2026-10-01T00:00:00Z'
 const owner={id:'agent-owner',name:'研发 Agent',enabled:true,created_at:stamp,token:'one-time-agent-key'}
 let spaces:any[]=[],grants:any[]=[],proposals:any[]=[],memories:any[]=[],events:any[]=[]
 await page.route('**/api/governance/agents',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(owner)}))
 await page.route('**/api/shared/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,body=request.postDataJSON()
  const json=(value:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)})
  expect(request.headers()['x-agent-key']).toBe(owner.token)
  if(path==='/api/shared/spaces'&&request.method()==='GET')return json(spaces)
  if(path==='/api/shared/spaces'&&request.method()==='POST'){
   const space={id:'space-1',name:body.name,visibility:'shared',owner_agent_id:owner.id,access_role:'owner',created_at:stamp}
   spaces=[space]
   return json(space)
  }
  if(path==='/api/shared/spaces/space-1/grants'&&request.method()==='GET')return json(grants)
  if(path==='/api/shared/spaces/space-1/grants'&&request.method()==='POST'){
   const grant={space_id:'space-1',agent_id:body.agent_id,agent_name:'执行 Agent',role:body.role,granted_at:stamp}
   grants=[grant]
   return json(grant)
  }
  if(path==='/api/shared/proposals'&&request.method()==='GET')return json(proposals)
  if(path==='/api/shared/proposals'&&request.method()==='POST'){
   const item={...body,id:'proposal-1',space_id:'space-1',proposer_agent_id:owner.id,source_ref:null,expires_at:null,status:'pending',reviewer_id:null,review_reason:'',applied_memory_id:null,created_at:stamp,decided_at:null,conflict_memory_id:null}
   proposals=[item]
   return json(item)
  }
  if(path==='/api/shared/proposals/proposal-1/approve'){
   proposals=[]
   const memory={id:'shared-memory-1',space_id:'space-1',proposal_id:'proposal-1',proposer_agent_id:owner.id,content:'本周发布候选版本',kind:'fact',importance:3,topic_key:'release',source_type:'manual',source_ref:null,expires_at:null,status:'active',version:1,supersedes_id:null,approved_by:owner.id,approved_at:stamp,revoked_by:null,revoked_at:null}
   memories=[memory]
   events=[{seq:1,space_id:'space-1',actor_id:owner.id,action:'activate',proposal_id:'proposal-1',memory_id:memory.id,reason:body.reason,source_type:'manual',source_ref:null,created_at:stamp}]
   return json({...memory,action:'activate'})
  }
  if(path==='/api/shared/memories'&&request.method()==='GET')return json(memories)
  if(path==='/api/shared/events'&&request.method()==='GET')return json(events)
  return json({detail:`unmocked ${path}`},404)
 })
 await page.getByRole('tab',{name:'共享治理'}).click()
 await page.getByLabel('Agent 名称').fill(owner.name)
 await page.getByRole('button',{name:'创建 Agent'}).click()
 await expect(page.getByText(owner.token,{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'使用此密钥'}).click()
 await page.getByLabel('创建空间').fill('产品研发')
 await page.getByRole('button',{name:'创建',exact:true}).click()
 await expect(page.locator('.governance-space-meta strong')).toHaveText('产品研发')
 await page.getByLabel('目标 Agent ID').fill('agent-worker')
 await page.getByRole('button',{name:'授权',exact:true}).click()
 await expect(page.locator('.governance-grants')).toContainText('执行 Agent')
 await page.getByLabel('记忆内容').fill('本周发布候选版本')
 await page.getByLabel('主题键').fill('release')
 await page.getByRole('button',{name:'提交审核'}).click()
 await expect(page.getByText('本周发布候选版本',{exact:true})).toHaveCount(1)
 await page.getByLabel('审核理由').fill('已核对版本计划')
 await page.getByRole('button',{name:'批准',exact:true}).click()
 await expect(page.getByText('本周发布候选版本',{exact:true})).toBeVisible()
 await expect(page.getByText('已核对版本计划')).toBeVisible()
 expect(await page.evaluate(()=>Object.values(localStorage).concat(Object.values(sessionStorage)).some(value=>String(value).includes('one-time-agent-key')))).toBe(false)
})

test('shared governance drops cached data after lost access and ignores a late lineage response',async({page})=>{
 const stamp='2026-10-01T00:00:00Z',key='revocable-agent-key'
 const space=(id:string,name:string)=>({id,name,visibility:'shared',owner_agent_id:'owner',access_role:'owner',created_at:stamp})
 const memory=(spaceId:string,content:string)=>({id:`memory-${spaceId}`,space_id:spaceId,proposal_id:`proposal-${spaceId}`,proposer_agent_id:'owner',content,kind:'fact',importance:3,topic_key:'release',source_type:'manual',source_ref:null,expires_at:null,status:'active',version:1,supersedes_id:null,approved_by:'owner',approved_at:stamp,revoked_by:null,revoked_at:null})
 const first=memory('space-a','空间 A 的私有计划'),second=memory('space-b','空间 B 的共享结论')
 let active=true,lateLineage:Route|undefined
 await page.route('**/api/shared/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,spaceId=url.searchParams.get('space_id')||''
  const json=(value:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)})
  expect(request.headers()['x-agent-key']).toBe(key)
  if(path==='/api/shared/spaces')return active?json([space('space-a','空间 A'),space('space-b','空间 B')]):json({detail:'Agent key 无效或已禁用'},401)
  if(path==='/api/shared/proposals')return json([])
  if(path==='/api/shared/events')return json([{seq:1,space_id:spaceId,actor_id:'owner',action:'activate',proposal_id:null,memory_id:null,reason:`${spaceId} 的审计`,source_type:null,source_ref:null,created_at:stamp}])
  if(path.endsWith('/grants'))return json([])
  if(path==='/api/shared/memories')return json(spaceId==='space-a'?[first]:[second])
  if(path==='/api/shared/memories/memory-space-a/lineage'){lateLineage=route;return}
  if(path==='/api/shared/memories/memory-space-b/lineage')return json([second])
  return json({detail:`unmocked ${path}`},404)
 })
 await page.getByRole('tab',{name:'共享治理'}).click()
 await page.getByLabel('已有 Agent 密钥').fill(key)
 await page.getByRole('button',{name:'连接'}).click()
 await expect(page.getByText(first.content,{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'查看沿革'}).click()
 await expect.poll(()=>Boolean(lateLineage)).toBe(true)
 await page.getByLabel('当前空间').selectOption('space-b')
 await expect(page.getByText(first.content,{exact:true})).toHaveCount(0)
 await expect(page.getByText(second.content,{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'查看沿革'}).click()
 await expect(page.locator('.governance-lineage')).toContainText(second.content)
 await lateLineage!.fulfill({contentType:'application/json',body:JSON.stringify([{...first,content:'迟到的空间 A 沿革'}])})
 await expect(page.locator('.governance-lineage')).not.toContainText('迟到的空间 A 沿革')
 active=false
 await page.getByRole('button',{name:'刷新空间与权限'}).click()
 await expect(page.getByText(second.content,{exact:true})).toHaveCount(0)
 await expect(page.getByText('space-b 的审计')).toHaveCount(0)
 await expect(page.getByText('Agent key 无效或已禁用')).toBeVisible()
 await expect(page.getByLabel('已有 Agent 密钥')).toBeVisible()
 expect(await page.evaluate(()=>Object.values(localStorage).concat(Object.values(sessionStorage)).some(value=>String(value).includes('revocable-agent-key')))).toBe(false)
})
