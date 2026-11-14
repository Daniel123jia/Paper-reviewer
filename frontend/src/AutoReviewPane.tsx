import {useEffect, useState} from 'react';
import P4ReportPane from './P4ReportPane';

type Task = {id:string; status:string; error:string|null; request_count:number;
  known_input_tokens:number; known_output_tokens:number; known_cost_usd:string;
  unknown_usage_calls:number; p4_project_id:string|null; p4_draft_id:string|null;
  total_budget:unknown;stage_budgets:unknown;
  stages:{stage:string;status:string;run_id:string|null}[]};
const names: Record<string,string> = {p1:'论文解析',p2a:'论文理解',p2b:'六维专业审稿',p2c:'候选问题整合',p3:'证据核验',p4:'中文报告生成'};
const budget=(calls:number,input:number,output:number,per:number,seconds:number,cost:string)=>({
  max_calls:calls,max_input_tokens:input,max_output_tokens:output,max_output_tokens_per_request:per,
  max_duration_seconds:seconds,max_cost_usd:cost,input_usd_per_million:'0.80',output_usd_per_million:'4.00'});
const defaults = () => ({
  total_budget:budget(111,2000000,332000,6000,14400,'3.20'),
  p2a_budget:budget(42,650000,120000,6000,5400,'1.10'),
  p2b_budget:budget(18,600000,50000,6000,1800,'0.65'),
  p2c_budget:budget(5,180000,30000,6000,1800,'0.30'),
  p3_budget:budget(30,450000,100000,6000,2700,'0.85'),
  p4_budget:budget(16,200000,32000,2000,1800,'0.30'),
});
async function api<T>(url:string, body?:unknown):Promise<T>{
  const response=await fetch(url,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok) throw new Error(typeof value.detail==='string'?value.detail:value.detail?.code || `请求失败 (${response.status})`);
  return value;
}

export default function AutoReviewPane({documentId,onEvidence}:{documentId:string;onEvidence:(doc:string,ref:string)=>void}){
  const [target,setTarget]=useState('');
  const [requirements,setRequirements]=useState('');
  const [limits,setLimits]=useState(defaults);
  const [task,setTask]=useState<Task|null>(null);
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{
    let cancelled=false;
    api<{id:string}[]>(`/api/documents/${documentId}/auto-review-tasks`).then(async list=>{
      if(list[0]){const next=await api<Task>(`/api/auto-review-tasks/${list[0].id}`);if(!cancelled)setTask(next);}
    }).catch(e=>{if(!cancelled)setError(String(e));});
    return()=>{cancelled=true;};
  },[documentId]);
  useEffect(()=>{
    if(task?.status!=='running')return;
    let cancelled=false;
    const timer=setInterval(()=>api<Task>(`/api/auto-review-tasks/${task.id}`).then(value=>{if(!cancelled)setTask(value);}).catch(e=>{if(!cancelled)setError(String(e));}),1000);
    return()=>{cancelled=true;clearInterval(timer);};
  },[task?.id,task?.status]);
  async function create(){
    setBusy(true);setError('');setConfirmed(false);
    try{
      const configured=limits;
      const next=await api<{id:string}>('/api/auto-review-tasks',{...configured,document_id:documentId,target_journal:target || null,review_requirements:requirements || null});
      setTask(await api<Task>(`/api/auto-review-tasks/${next.id}`));
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function start(){
    if(!task || !confirmed)return;
    setBusy(true);setError('');
    try{
      await api(`/api/auto-review-tasks/${task.id}/start`,{confirm_budget:true});
      setTask(await api<Task>(`/api/auto-review-tasks/${task.id}`));
    }catch(e){setError(String(e));}finally{setBusy(false);setConfirmed(false);}
  }
  return <section className="auto-review-pane">
    <h2>完整AI审稿</h2>
    <p>单篇首次审稿 · 中文内部草稿。上传和创建任务不调用模型；确认后自动执行全部阶段。报告仍需人工复核。</p>
    {!task && <>
      <label>目标期刊（可选）<input aria-label="完整审稿目标期刊" value={target} maxLength={200} onChange={e=>setTarget(e.target.value)}/></label>
      <label>本次审稿要求<textarea aria-label="完整审稿要求" value={requirements} maxLength={2000} onChange={e=>setRequirements(e.target.value)}/></label>
      <p>费用为暂估站内额度，单价未核实。软件预算不是供应商硬消费上限；未知Usage会立即停止，不自动重试。</p>
      <details open><summary>总预算及各阶段限额（创建前可修改）</summary>
        {Object.entries(limits).map(([stage,values])=><details key={stage} open={stage==='total_budget'}>
          <summary>{stage==='total_budget'?'整份任务总预算':names[stage.replace('_budget','')]}</summary>
          <div className="p4-controls">{Object.entries(values).map(([field,value])=><label key={field}>
            {({max_calls:'请求次数',max_input_tokens:'输入Token',max_output_tokens:'输出Token',max_output_tokens_per_request:'每次输出上限',max_duration_seconds:'最长秒数',max_cost_usd:'规划费用上限',input_usd_per_million:'输入单价 / 百万Token',output_usd_per_million:'输出单价 / 百万Token'} as Record<string,string>)[field]}
            <input aria-label={`${stage} ${field}`} type="number" min="0" step="any" value={value}
              onChange={e=>setLimits(previous=>({...previous,[stage]:{...previous[stage as keyof typeof previous],[field]:typeof value==='number'?Number(e.target.value):e.target.value}}))}/>
          </label>)}</div>
        </details>)}
      </details>
      <button disabled={busy} onClick={create}>创建待启动任务</button>
    </>}
    {task && <>
      <p>任务 <code>{task.id}</code> · <strong data-testid="auto-task-status">{task.status}</strong></p>
      {task.status==='pending' && <>
        <p>预算已冻结。确认后运行 P2-A 至 P4；不会自动批准报告。</p>
        <details><summary>查看已冻结预算</summary><pre>{JSON.stringify(task.total_budget,null,2)}</pre><pre>{JSON.stringify(task.stage_budgets,null,2)}</pre></details>
        <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我确认总预算和各阶段限额，并接受供应商计量风险</label>
        <button disabled={!confirmed || busy} onClick={start}>确认预算并开始完整审稿</button>
      </>}
      <ol aria-label="完整审稿阶段进度">{task.stages.map(stage=><li key={stage.stage}>{names[stage.stage]}：{stage.status}{stage.run_id && <details><summary>运行谱系</summary><code>{stage.run_id}</code></details>}</li>)}</ol>
      <p>已派发请求：<span data-testid="auto-request-count">{task.request_count}</span> · 已知输入 / 输出 Token：{task.known_input_tokens} / {task.known_output_tokens}</p>
      <p>已知估算费用：{task.known_cost_usd} · 供应商实际扣费：未核实 · 未知Usage：{task.unknown_usage_calls}</p>
      {task.error && <p role="alert">停止原因：{task.error}。已完成产物保留，不自动重跑。</p>}
      {task.p4_project_id && task.p4_draft_id && <P4ReportPane key={task.p4_project_id} documentId={documentId} initialProjectId={task.p4_project_id} allowGeneration={false} onEvidence={onEvidence}/>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
