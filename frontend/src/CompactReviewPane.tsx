import {useEffect, useState} from 'react';
import Markdown from 'react-markdown';
import './CompactReviewPane.css';

type Job = {
  status: string; body?: string; error?: string; planned_limit?: string;
  usage?: {request_count: number; known_input_tokens: number; known_output_tokens: number; known_cost_usd: string};
  verification?: {report: {comments: {evidence: {evidence_id: string}[]}[]}};
};
const labels: Record<string,string> = {
  drafting:'正在通读全文并撰写初稿', checking:'正在逐项回查原文',
  failed:'审稿已停止', interrupted:'审稿已中断', budget_exhausted:'已达到预算限制',
  unknown_usage:'请求用量未知，审稿已停止'
};

export default function CompactReviewPane({paper, upload, busy, error, onEvidence}: {
  paper: {id:string;filename:string;page_count:number}|null; upload:(file?:File)=>void;
  busy:boolean; error:string; onEvidence:(documentId:string,id:string)=>void;
}) {
  const [job,setJob] = useState<Job|null>(null);
  const [problem,setProblem] = useState('');
  const [starting,setStarting] = useState(false);
  const [refresh,setRefresh] = useState(0);
  const url = paper ? '/api/documents/'+paper.id+'/compact-review' : '';
  useEffect(()=>{
    if (!url) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const r = await fetch(url,{signal:abort.signal});
        if (!r.ok) throw Error('读取任务失败');
        const j: Job = await r.json();
        if (abort.signal.aborted) return;
        setJob(j);
        if (['drafting','checking'].includes(j.status)) timer=setTimeout(read,1200);
      } catch { if (!abort.signal.aborted) setProblem('读取任务失败，可刷新页面查看已保存结果。'); }
    }
    void read();
    return ()=>{abort.abort(); clearTimeout(timer);};
  },[url,refresh]);
  async function start(){
    setStarting(true); setProblem('');
    try {
      const r=await fetch(url+'/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_budget:true})});
      if(!r.ok){const data=await r.json(); throw Error(typeof data.detail==='string'?data.detail:'请检查后端模型配置或服务状态');}
      setJob(await r.json()); setRefresh(n=>n+1);
    } catch(e){setProblem(e instanceof Error?e.message:'启动失败');}
    finally {setStarting(false);}
  }
  const refs=new Set(job?.verification?.report.comments.flatMap(i=>i.evidence.map(e=>e.evidence_id)));
  return <section className="compact-pane">
    <header><h1>全文审稿</h1><p>通读论文形成中文初稿，再逐条回查意见与原文。</p></header>
    <div className="compact-file"><div><strong>{paper?.filename || '选择论文 PDF'}</strong>
      {paper && <small>{paper.page_count} 页 · 中文内部草稿</small>}</div>
      <label className="compact-picker">{busy?'正在解析…':paper?'更换论文':'上传 PDF'}
        <input type="file" accept="application/pdf,.pdf" disabled={busy} onChange={e=>{upload(e.target.files?.[0]);e.target.value='';}}/></label></div>
    {paper && job?.status==='ready' && <div className="compact-card"><h2>开始全文审稿</h2>
      <p>将向已配置的模型服务发送论文全文，最多两次请求。本地规划上限 {job.planned_limit} 站内额度，实际扣费以供应商账单为准。</p>
      <button className="compact-start" disabled={starting||busy} onClick={start}>确认费用并开始审稿</button></div>}
    {job && labels[job.status] && <div className="compact-card" role="status"><h2>{labels[job.status]}</h2>
      {job.error && <p>{job.error}</p>}{job.status==='unknown_usage' && <p>供应商可能已计费。请核对账单。</p>}</div>}
    {job?.usage && <p className="compact-cost">已派发 {job.usage.request_count} 次 · 已知输入 / 输出 {job.usage.known_input_tokens} / {job.usage.known_output_tokens} Token · 本地已知费用估算 {job.usage.known_cost_usd}</p>}
    {job?.body && <><div className="compact-exports"><a href={url+'/export?format=md'}>导出 Markdown</a><a href={url+'/export?format=pdf'}>导出 PDF</a></div>
      <article className="compact-report"><Markdown components={{code:({children})=>refs.has(String(children)) && paper?
        <button className="compact-ref" title="定位原文" onClick={()=>onEvidence(paper.id,String(children))}>{children}</button>:<code>{children}</code>}}>{job.body}</Markdown></article></>}
    {(error||problem) && <p role="alert">{error||problem}</p>}
  </section>;
}
