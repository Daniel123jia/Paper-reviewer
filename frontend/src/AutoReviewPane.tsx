import {useEffect, useRef, useState} from 'react';
import P4ReportPane from './P4ReportPane';

type ExecutionPolicy = {max_concurrency:number;requests_per_minute:number;input_reservation_per_minute:number};
type PerformanceStage = {stage_wall_ms?:number|null; wall_span_ms:number|null; model_wait_sum_ms:number|null; peak_concurrency:number|null; timing_complete:boolean; input_bytes:number};
type Task = {execution_policy?:ExecutionPolicy; performance?:{stages:Record<string, PerformanceStage>}; id:string; status:string; error:string|null; request_count:number;
  document_id:string; target_journal:string|null; special_requirements:string|null; review_requirements:string|null;
  known_input_tokens:number; known_output_tokens:number; known_cost_usd:string;
  unknown_usage_calls:number; calls:{status:string}[]; p4_project_id:string|null; p4_draft_id:string|null;
  total_budget:Budget;stage_budgets:Record<string, Budget>;
  stages:{stage:string;status:string;run_id:string|null}[]};
const names: Record<string,string> = {p1:'论文解析',p2a:'论文理解',p2b:'六维专业审稿',p2c:'候选问题整合',p3:'证据核验',p4:'中文报告生成'};
const budget=(calls:number,input:number,output:number,per:number,seconds:number,cost:string)=>({
  max_calls:calls,max_input_tokens:input,max_output_tokens:output,max_output_tokens_per_request:per,
  max_duration_seconds:seconds,max_cost_usd:cost,input_usd_per_million:'0.80',output_usd_per_million:'4.00'});
type Budget = ReturnType<typeof budget>;
const fields: Record<keyof Budget, string> = {
  max_calls:'请求次数', max_input_tokens:'输入 Token', max_output_tokens:'输出 Token',
  max_output_tokens_per_request:'每次输出上限', max_duration_seconds:'最长秒数',
  max_cost_usd:'规划费用上限', input_usd_per_million:'输入单价 / 百万 Token',
  output_usd_per_million:'输出单价 / 百万 Token',
};
const stageNotes: Record<string,string> = {
  p1:'提取论文结构与原文证据', p2a:'梳理研究问题、贡献与主张', p2b:'按六个专业维度审查论文',
  p2c:'整理重复或相关的候选问题', p3:'回查原文、反证与核验限制', p4:'生成供人工复核的中文内部报告',
};
const statusNames: Record<string,string> = {
  pending:'等待中', queued:'已排队', running:'进行中', completed:'已完成',
  limited:'完成，证据受限', failed:'未完成', partial_failed:'部分完成后停止',
  unknown_usage:'已停止，用量未知', budget_exhausted:'已停止，达到预算上限', interrupted:'已中断',
};
const number = (value: number) => value.toLocaleString('zh-CN');

function BudgetSummary({value}:{value:Budget}) {
  return <div className="auto-budget-summary">
    <div><span>本地规划费用上限</span><strong>{value.max_cost_usd} USD</strong></div>
    <small>暂估单价（未核实）：输入 {value.input_usd_per_million}、输出 {value.output_usd_per_million} USD / 百万 Token。
      此上限仅用于本地控制，实际扣费以供应商账单为准。</small>
  </div>;
}
type Paper = {id:string; filename:string; page_count:number};
type History = {id:string;status:string;created_at:string};

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

function configurationMatches(expected: Record<string, any>, saved: Task): boolean {
  if (['document_id','target_journal','special_requirements','review_requirements'].some(k => expected[k] !== (saved as any)[k])) return false;
  if (!saved.execution_policy || Object.keys(expected.execution_policy).some(k => expected.execution_policy[k] !== (saved.execution_policy as any)[k])) return false;
  return ['total','p2a','p2b','p2c','p3','p4'].every(stage => {
    const a = expected[`${stage}_budget`];
    const b = stage === 'total' ? saved.total_budget : saved.stage_budgets?.[stage];
    return a && b && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => Number.isFinite(Number((b as any)[k])) && Number(a[k]) === Number((b as any)[k]));
  });
}

export default function AutoReviewPane({document, onEvidence, onUpload, uploadBusy, uploadError, onShowSource, sourceOpen}: {
  document:Paper|null; onEvidence:(doc:string,ref:string)=>void;
  onUpload:(file?:File)=>Promise<void>; uploadBusy:boolean; uploadError:string;
  onShowSource:()=>void; sourceOpen:boolean;
}){
  const documentId = document?.id;
  const fileInput = useRef<HTMLInputElement>(null);
  const [target,setTarget]=useState('');
  const [specialRequirements,setSpecialRequirements]=useState('');
  const [requirements,setRequirements]=useState('');
  const [limits,setLimits]=useState(defaults);
  const [shortLimits,setShortLimits]=useState<ReturnType<typeof defaults>|null>(null);
  const [preset,setPreset]=useState('standard');
  const [executionPolicy,setExecutionPolicy]=useState<ExecutionPolicy>({max_concurrency:2,requests_per_minute:60,input_reservation_per_minute:1000000});
  const [task,setTask]=useState<Task|null>(null);
  const [history,setHistory]=useState<History[]>([]);
  const [confirmed,setConfirmed]=useState(false);
  const [configurationValid,setConfigurationValid]=useState(true);
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(Boolean(documentId));
  const [error,setError]=useState('');
  const [dragging,setDragging]=useState(false);
  useEffect(()=>{
    if (!documentId) return;
    let cancelled=false;
    api<History[]>(`/api/documents/${documentId}/auto-review-tasks`).then(async list=>{
      if(cancelled) return;
      setHistory(list);
      if(list[0]){
        const next=await api<Task>(`/api/auto-review-tasks/${list[0].id}`);
        if(!cancelled){
          const expected = sessionStorage.getItem(`auto-review-config:${next.id}`);
          if(expected) {
            try { setConfigurationValid(configurationMatches(JSON.parse(expected), next)); }
            catch { setConfigurationValid(false); }
          }
          setTask(next);
        }
      } else if (document && document.page_count <= 8) {
        const suggestion = await api<{budgets:ReturnType<typeof defaults>|null}>(
          `/api/documents/${documentId}/auto-review-budget-suggestion`).catch(() => null);
        if (!cancelled && suggestion?.budgets) {
          setShortLimits(suggestion.budgets);
          setLimits(suggestion.budgets);
          setPreset('short');
        }
      }
    }).catch(e=>{if(!cancelled)setError(String(e));}).finally(()=>{if(!cancelled)setLoading(false);});
    return()=>{cancelled=true;};
  },[documentId,document?.page_count]);
  useEffect(()=>{
    if(task?.status!=='running')return;
    let cancelled=false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll(){
      try {
        const next=await api<Task>(`/api/auto-review-tasks/${task!.id}`);
        if(cancelled)return;
        setTask(next);
        if(next.status!=='running')return;
      } catch(e) {if(!cancelled)setError(String(e));}
      if(!cancelled)timer=setTimeout(poll,1000);
    }
    timer=setTimeout(poll,1000);
    return()=>{cancelled=true;clearTimeout(timer);};
  },[task?.id,task?.status]);

  async function create(){
    setBusy(true);setError('');setConfirmed(false);
    try{
      const expected={...limits,execution_policy:executionPolicy,document_id:documentId,target_journal:target || null,special_requirements:specialRequirements || null,review_requirements:requirements || null};
      const next=await api<{id:string}>('/api/auto-review-tasks',expected);
      sessionStorage.setItem(`auto-review-config:${next.id}`,JSON.stringify(expected));
      const saved=await api<Task>(`/api/auto-review-tasks/${next.id}`);
      setConfigurationValid(configurationMatches(expected,saved));
      setTask(saved);
      setHistory(previous=>[{id:saved.id,status:saved.status,created_at:new Date().toISOString()},...previous]);
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function start(){
    if(!task || !confirmed || !configurationValid)return;
    setBusy(true);setError('');
    try{
      const expected=sessionStorage.getItem(`auto-review-config:${task.id}`);
      if(expected && !configurationMatches(JSON.parse(expected),await api<Task>(`/api/auto-review-tasks/${task.id}`))) {
        setConfigurationValid(false);setConfirmed(false);setError('保存的任务配置与提交内容不一致，已阻止启动。');return;
      }
      await api(`/api/auto-review-tasks/${task.id}/start`,{confirm_budget:true});
      setTask(await api<Task>(`/api/auto-review-tasks/${task.id}`));
    }catch(e){setError(String(e));}finally{setBusy(false);setConfirmed(false);}
  }
  async function openTask(id:string){
    setBusy(true);setConfirmed(false);setError('');
    try{setTask(await api<Task>(`/api/auto-review-tasks/${id}`));}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }

  const report = Boolean(task?.p4_project_id && task?.p4_draft_id);
  const progress = Boolean(task && task.status!=='pending' && !report);
  const title = report ? '中文审稿报告' : progress ? task?.status==='running' ? '正在审稿' : '审稿进度' : '完整AI审稿';
  const phase = report ? 2 : progress ? 1 : 0;
  // The backend includes dispatched, unsettled requests in its conservative unknown count.
  const awaitingUsage = task?.status === 'running'
    ? task.calls.filter(call => call.status === 'dispatch_pending').length : 0;
  const hasKnownUsage = task?.calls.some(call => call.status === 'usage_valid');
  const showKnownTotals = hasKnownUsage || task?.request_count === 0;
  return <section className={`auto-review-pane${report ? ' auto-report-pane' : ''}`}>
    <ol className="auto-stepper" aria-label="审稿流程">
      {['上传与设置','审稿进度','查看报告'].map((label,index)=><li key={label} aria-current={phase===index?'step':undefined}
        className={index===phase?'current':index<phase?'past':''}>{label}</li>)}
    </ol>
    <div className="auto-page-head">
      <div><h1>{title}</h1>
        {!task && <p>上传学术论文，生成中文内部审稿草稿。</p>}
        {report && <p>汇集审稿意见与原文证据，供审稿人复核。</p>}
      </div>
      {history.length>0 && <details className="auto-history"><summary>审稿历史</summary><div>
        {history.map(item=><button type="button" key={item.id} disabled={busy || item.id===task?.id}
          onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');void openTask(item.id);}}>
          {item.id.slice(0,8)} · {statusNames[item.id===task?.id ? task.status : item.status] ?? item.status}
        </button>)}
      </div></details>}
    </div>

    <input ref={fileInput} type="file" accept="application/pdf,.pdf" aria-label="上传论文 PDF" hidden
      disabled={uploadBusy} onChange={event=>{void onUpload(event.target.files?.[0]);event.target.value='';}}/>
    {!document ? <div className={`auto-dropzone${dragging ? ' is-dragging' : ''}`}
      onDragOver={event=>{event.preventDefault();setDragging(true);}}
      onDragLeave={()=>setDragging(false)}
      onDrop={event=>{event.preventDefault();setDragging(false);if(!uploadBusy)void onUpload(event.dataTransfer.files[0]);}}>
      <h2>上传 PDF 论文</h2>
      <p>拖拽文件到此处，或选择本地 PDF。最大 50 MB。</p>
      <button className="auto-primary" disabled={uploadBusy} onClick={()=>fileInput.current?.click()}>{uploadBusy?'正在解析…':'选择 PDF'}</button>
    </div> : <div className="auto-file">
      <span className="auto-file-type">PDF</span><div><strong>{document.filename}</strong><small>{document.page_count} 页 · 中文内部报告</small></div>
      {report && <button className="auto-secondary" onClick={onShowSource} aria-expanded={sourceOpen}>{sourceOpen?'收起原文':'查看原文'}</button>}
      {(!progress || task?.status !== 'running') && <button className="auto-secondary" disabled={uploadBusy} onClick={()=>fileInput.current?.click()}>{uploadBusy?'正在解析…':report || progress?'上传新论文':'更换文件'}</button>}
    </div>}
    {loading && <p role="status">正在读取审稿任务…</p>}
    {!loading && document && !task && <>
      <details className="auto-settings">
        <summary>审稿要求与预算设置</summary>
        <div className="auto-fields">
          <label>目标期刊（可选）<input value={target} maxLength={200} onChange={e=>setTarget(e.target.value)}/></label>
          <label>论文理解特殊要求（P2-A）<textarea maxLength={2000} value={specialRequirements} onChange={e=>setSpecialRequirements(e.target.value)}/></label>
          <label>审稿要求（可选）<textarea placeholder="例如：重点关注实验设计与可复现性" value={requirements} maxLength={2000} onChange={e=>setRequirements(e.target.value)}/></label>
        </div>
        {shortLimits && <div className="auto-fields"><label>预算配置
          <select value={preset} onChange={e=>{
            setPreset(e.target.value);
            setLimits(e.target.value==='short' ? shortLimits : defaults());
          }}>
            <option value="short">短论文预算</option>
            <option value="standard">常规预算</option>
          </select>
        </label></div>}
        <details className="auto-stage-budget"><summary>执行并发与本地速率限制</summary>
          <div className="auto-budget-fields">
            <label>最大并发<select value={executionPolicy.max_concurrency} onChange={e=>setExecutionPolicy(p=>({...p,max_concurrency:Number(e.target.value)}))}>
              <option value={1}>1 · 串行</option><option value={2}>2 · 有界并行</option>
            </select></label>
            <label>每分钟请求上限<input type="number" min="1" max="120" value={executionPolicy.requests_per_minute} onChange={e=>setExecutionPolicy(p=>({...p,requests_per_minute:Number(e.target.value)}))}/></label>
            <label>每分钟输入预留上限<input type="number" min="1" max="2000000" value={executionPolicy.input_reservation_per_minute} onChange={e=>setExecutionPolicy(p=>({...p,input_reservation_per_minute:Number(e.target.value)}))}/></label>
          </div>
          <small>以上为本地限制。输入按 UTF-8 字节保守预留，并非实测 Token；供应商速率与账户限额尚未核实，应按账户限制调低。5分钟目标尚未经真实运行验证。</small>
        </details>
        {Object.entries(limits).map(([stage,values])=><details className="auto-stage-budget" key={stage}>
          <summary>{stage==='total_budget'?'整份任务总预算':names[stage.replace('_budget','')]}</summary>
          <div className="auto-budget-fields">{Object.entries(values).map(([field,value])=><label key={field}>
            {fields[field as keyof Budget]}
            <input aria-label={`${stage} ${field}`} type="number" min="0" step={typeof value==='number'?1:'any'} value={value}
              onChange={e=>setLimits(previous=>({...previous,[stage]:{...previous[stage as keyof typeof previous],[field]:typeof value==='number'?Number(e.target.value):e.target.value}}))}/>
          </label>)}</div>
        </details>)}
      </details>
      <div className="auto-start-card"><BudgetSummary value={limits.total_budget}/>
        {preset === 'short' && <small>按已解析篇幅采用短论文预算，可在设置中切换。</small>}
        <button className="auto-primary" disabled={busy || uploadBusy} onClick={create}>{busy?'正在保存…':'创建待启动任务'}</button>
      </div>
    </>}
    {task && <>
      <p className={`auto-task-state state-${task.status}`} role="status" data-testid="auto-task-status">
        {report?'报告已生成':task.status==='pending'?'待确认启动':statusNames[task.status] ?? task.status}
      </p>
      {task.status==='pending' && <div className="auto-start-card">
        {!configurationValid && <p className="auto-alert" role="alert">保存的任务配置与提交内容不一致，已阻止启动。</p>}
        <BudgetSummary value={task.total_budget}/>
        <details className="auto-frozen"><summary>查看预算明细</summary>
          {[['总预算',task.total_budget] as const,...Object.entries(task.stage_budgets).map(([key,value])=>[names[key],value] as const)].map(([label,value])=>
            <details key={label}><summary>{label}</summary><dl>{Object.entries(value).map(([field,amount])=>
              <div key={field}><dt>{fields[field as keyof Budget]}</dt><dd>{typeof amount === 'number' ? number(amount) : amount}</dd></div>)}</dl></details>)}
        </details>
        <label className="auto-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>
          我确认总预算和各阶段限额，并同意启动审稿</label>
        <button className="auto-primary" disabled={!confirmed || !configurationValid || busy || uploadBusy} onClick={start}>{busy?'正在启动…':'确认预算并开始完整审稿'}</button>
      </div>}
      {progress && <ol className="auto-timeline" aria-label="完整审稿阶段进度">
        {task.stages.map(stage=><li key={stage.stage} className={`state-${stage.status}`}>
          <div className="auto-stage-text"><strong>{names[stage.stage]}</strong><small>{stageNotes[stage.stage]}</small></div>
          <span className="auto-stage-status">{statusNames[stage.status] ?? stage.status}</span>
        </li>)}
      </ol>}
      {task.unknown_usage_calls>0 && task.status!=='running' && <p className="auto-alert" role="alert">任务已停止。有 {task.unknown_usage_calls} 次请求用量未知，可能已计费。下方估算仅包含已有用量记录的请求。</p>}
      {task.error && <details className="auto-alert" open={task.unknown_usage_calls===0}><summary>停止详情</summary><code>{task.error}</code></details>}
      {report && <P4ReportPane key={task.p4_project_id} documentId={documentId!} initialProjectId={task.p4_project_id!}
        initialDraftId={task.p4_draft_id!} allowGeneration={false} onEvidence={onEvidence}/>}
      {task.status!=='pending' && <section className="auto-usage" aria-label="审稿用量">
        <div><span>已派发请求</span><strong data-testid="auto-request-count">{task.request_count}</strong></div>
        <div><span>已知输入 / 输出 Token</span><strong>{showKnownTotals
          ? `${number(task.known_input_tokens)} / ${number(task.known_output_tokens)}` : '—'}</strong></div>
        <div><span>本地费用估算（USD）</span><strong>{showKnownTotals ? task.known_cost_usd : '暂无可核算用量'}</strong></div>
        {awaitingUsage > 0 && <small>{awaitingUsage} 次请求等待用量回报。</small>}
        <small>供应商实际扣费未核实。</small>
      </section>}
      {task.status!=='pending' && <details className="auto-frozen"><summary>阶段状态与运行记录</summary>
        <p>最大并发 {task.execution_policy?.max_concurrency ?? 1}；5分钟目标未验证。以下为本地派发至核销计时，含网络等待。</p><dl>
        {task.stages.map(item=><div key={item.stage}><dt>{names[item.stage]} · {statusNames[item.status] ?? item.status}</dt><dd><code>{item.run_id ?? '—'}</code>
          {task.performance?.stages[item.stage] && <small>{task.performance.stages[item.stage].stage_wall_ms != null && <> 阶段耗时 {(task.performance.stages[item.stage].stage_wall_ms!/1000).toFixed(1)} 秒 ·</>} 请求跨度 {task.performance.stages[item.stage].wall_span_ms == null ? '未记录' : (task.performance.stages[item.stage].wall_span_ms!/1000).toFixed(1)+' 秒'} · 累计等待 {task.performance.stages[item.stage].model_wait_sum_ms == null ? '未记录' : (task.performance.stages[item.stage].model_wait_sum_ms!/1000).toFixed(1)+' 秒'} · 实测峰值并发 {task.performance.stages[item.stage].peak_concurrency ?? '未记录'}{!task.performance.stages[item.stage].timing_complete && '（计时不完整）'}</small>}
        </dd></div>)}
      </dl></details>}
    </>}
    {(error || uploadError) && <p className="auto-alert" role="alert">{error || uploadError}</p>}
  </section>;
}
