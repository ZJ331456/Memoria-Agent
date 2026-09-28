import { expect, test, type Page, type Route } from '@playwright/test'

const session={id:'session-1',title:'E2E 会话',created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z',message_count:0}
const memory={id:'memory-1',content:'现在喜欢乌龙茶',kind:'preference',importance:4,source:'conversation',created_at:'2026-07-23T00:00:00Z',updated_at:'2026-07-23T00:00:00Z',status:'active',reinforcement:1,supersedes_id:'memory-0',last_reinforced_at:null}
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
  if(path==='/api/sessions/session-1/messages')return json(chatCompleted?[{id:'assistant-1',session_id:'session-1',role:'assistant',content:'流式回复',created_at:'2026-07-23T00:00:01Z'}]:[])
  if(path==='/api/sessions')return json([session])
  if(path==='/api/memories/memory-1/timeline')return json([{...memory,id:'memory-0',content:'以前喜欢红茶',status:'superseded',supersedes_id:null,replacement_reason:null}, {...memory,replacement_reason:'偏好发生变化'}])
  if(path==='/api/memories')return json([memory])
  if(path==='/api/markdown/SELF')return json({name:'SELF',content:''})
  if(path==='/api/setup/status')return json({main:{model:'mock',base_url:'',configured:true},fast:{model:'',base_url:'',configured:false},embedding:{model:'',base_url:'',configured:false},setup_needed:false})
  if(path==='/api/memory-jobs/failed-job/retry')return json({...jobs[0],status:'pending',attempts:0,error:null})
  if(path==='/api/memory-jobs')return json(jobs)
  if(path==='/api/memories/undo')return json({affected_ids:['memory-1'],restored_ids:['memory-old']})
  if(path==='/api/traces')return json([])
  if(path==='/api/overview')return json({sessions:1,messages:0,memories:0,memories_superseded:0,traces:0,memory_jobs_pending:0,memory_jobs_failed:1,models:{},tools:[],pipeline:{}})
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
 await page.getByRole('button',{name:'查看时间线'}).click()
 await expect(page.getByTestId('memory-timeline')).toContainText('以前喜欢红茶')
 await expect(page.getByTestId('memory-timeline')).toContainText('现在喜欢乌龙茶')
 await page.setViewportSize({width:390,height:844})
 for(const label of ['对话','记忆','追踪','工具','设置'])await expect(page.getByRole('tab',{name:label})).toBeVisible()
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)
 expect(overflow).toBe(false)
})

test('a failed setup request does not hide conversations',async({page})=>{
 await page.route('**/api/setup/status',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'setup unavailable'})}))
 await page.reload()
 await expect(page.getByRole('button',{name:/E2E 会话/})).toBeVisible()
 await expect(page.getByText('请求失败')).toBeVisible()
})
