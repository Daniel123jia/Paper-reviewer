import {useEffect, useState} from 'react';
import CrossFormulaPane from './CrossFormulaPane';
import ExperimentAuditPane from './ExperimentAuditPane';
import AlgorithmAuditPane from './AlgorithmAuditPane';
import DeepVerificationPane from './DeepVerificationPane';
import './ReviewPane.css';

type Paper = {id: string; filename: string; sha256: string};
type Anchor = {evidence_id: string; document_id: string; page_index: number};
type Finding = {id: string; title: string; concern: string; evidence: Anchor[];
  evidence_level: string; verification_status: string; support_assessment: string; counterargument: string};
type ModelCall = {stage: string; model: string; input_tokens: number | null;
  output_tokens: number | null; duration_ms: number; cost_usd: number | null};
type ReviewCase = {id: string; mode: string; original_document_id: string;
  revised_document_id: string | null; response_document_id: string | null;
  latest_run: {id: string; status: string; error: string | null} | null;
  findings: Finding[]; model_calls: ModelCall[]};

const labels: Record<string, string> = {
  first_review: '一审', own_rereview: '二审 · 本人复审', new_reviewer: '二审 · 新增审稿人',
};
const verification: Record<string, string> = {
  needs_independent_verification: '待独立验证', uncertain: '证据尚不充分', rejected: '已排除',
};

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

export default function ReviewPane({current, documents, onEvidence}: {
  current: Paper; documents: Paper[]; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [active, setActive] = useState<ReviewCase | null>(null);
  const [mode, setMode] = useState('first_review');
  const [revision, setRevision] = useState('');
  const [response, setResponse] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api<ReviewCase[]>('/api/review-cases').then(setCases)
      .catch(cause => setError(cause instanceof Error ? cause.message : '任务加载失败'));
  }, []);

  useEffect(() => {
    if (!active?.latest_run || !['queued', 'running'].includes(active.latest_run.status)) return;
    const timer = window.setInterval(() => {
      api<ReviewCase>(`/api/review-cases/${active.id}`).then(updated => {
        setActive(updated);
        setCases(previous => previous.map(item => item.id === updated.id ? updated : item));
      }).catch(cause => setError(cause instanceof Error ? cause.message : '分析状态加载失败'));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [active?.id, active?.latest_run?.status]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      const payload = {mode, original_document_id: current.id,
        revised_document_id: mode === 'first_review' ? null : revision || null,
        response_document_id: mode === 'own_rereview' ? response || null : null};
      const created = await api<ReviewCase>('/api/review-cases', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
      setCases(previous => [created, ...previous]);
      setActive(created);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '创建审稿任务失败');}
    finally {setBusy(false);}
  }

  async function analyze() {
    if (!active) return;
    setBusy(true); setError('');
    try {
      await api(`/api/review-cases/${active.id}/analyze`, {method: 'POST'});
      const updated = await api<ReviewCase>(`/api/review-cases/${active.id}`);
      setActive(updated);
      setCases(previous => previous.map(item => item.id === updated.id ? updated : item));
    } catch (cause) {setError(cause instanceof Error ? cause.message : '启动分析失败');}
    finally {setBusy(false);}
  }

  async function refreshActiveCase() {
    if (!active) return;
    const updated = await api<ReviewCase>(`/api/review-cases/${active.id}`);
    setActive(updated);
    setCases(previous => previous.map(item => item.id === updated.id ? updated : item));
  }

  const otherPapers = documents.filter(item => item.id !== current.id);
  const running = active?.latest_run && ['queued', 'running'].includes(active.latest_run.status);

  return <div className="review-pane">
    <div className="eyebrow">审稿分析 · 候选问题</div>
    <h2>建立可复核的审稿任务</h2>
    <p className="review-intro">AI 从每份材料抽样原文（合计最多 30 处），提出少量问题并寻找反证。每次分析会调用中转站并消耗 Token。当前只形成候选问题，数学、算法与实验判断仍需独立验证。</p>
    <form className="review-form" onSubmit={create}>
      <label>审稿类型<select aria-label="审稿类型" value={mode} onChange={event => setMode(event.target.value)}>
        {Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
      </select></label>
      <div className="review-source">原稿：{current.filename} · {current.sha256.slice(0, 10)}…</div>
      {mode !== 'first_review' && <label>修订稿<select aria-label="修订稿" required value={revision} onChange={event => {setRevision(event.target.value); setResponse('');}}>
        <option value="">选择已上传 PDF</option>{otherPapers.map(item => <option value={item.id} key={item.id}>{item.filename} · {item.sha256.slice(0, 8)}</option>)}
      </select></label>}
      {mode === 'own_rereview' && <label>作者回复<select aria-label="作者回复" required value={response} onChange={event => setResponse(event.target.value)}>
        <option value="">选择已上传 PDF</option>{otherPapers.filter(item => item.id !== revision).map(item =>
          <option value={item.id} key={item.id}>{item.filename} · {item.sha256.slice(0, 8)}</option>)}</select></label>}
      {mode !== 'first_review' && otherPapers.length === 0 && <p className="review-hint">请先上传修订稿{mode === 'own_rereview' ? '和作者回复' : ''}的 PDF。</p>}
      <button disabled={busy}>创建任务</button>
    </form>
    {cases.length > 0 && <div className="review-history"><strong>已有任务</strong><div>{cases.map(item =>
      <button key={item.id} className={active?.id === item.id ? 'selected' : ''} onClick={() => setActive(item)}>
        {labels[item.mode]} · {item.id.slice(0, 8)}</button>)}</div></div>}
    {active && <section className="review-detail">
      <div className="review-detail-head"><div><strong>{labels[active.mode]}</strong><small>任务 {active.id.slice(0, 12)}</small></div>
        <div className="review-detail-actions"><button type="button" className="review-jump" onClick={() => document.getElementById('cross-formula')?.scrollIntoView({block: 'start'})}>跳到离线核查</button>
          <button disabled={busy || Boolean(running)} onClick={analyze}>{active.latest_run ? '重新分析 · 消耗 Token' : '开始 AI 分析 · 消耗 Token'}</button></div></div>
      {active.latest_run && <p className="review-run-status" role="status">{active.latest_run.status === 'completed'
        ? '分析完成 · 结果仍需独立验证' : active.latest_run.status === 'failed'
          ? active.latest_run.error || '分析失败' : '正在分析原文并复核候选问题…'}</p>}
      {active.latest_run?.status === 'completed' && active.findings.length === 0 && <p className="review-hint">本次抽样未找到有充分证据的候选问题；这不等于论文没有问题。</p>}
      {active.findings.map(finding => <article className="finding-card" key={finding.id}>
        <div className="finding-head"><strong>{finding.title}</strong><span>{verification[finding.verification_status] || finding.verification_status}</span></div>
        <p>{finding.concern}</p>
        <div className="finding-evidence"><small>证据级别：{finding.evidence_level === 'source_located' ? '已定位原文' : '未定位'}</small>
          {finding.evidence.map(anchor => <button key={anchor.evidence_id} onClick={() => onEvidence(anchor.document_id, anchor.evidence_id)}>
            {anchor.document_id === active.original_document_id ? '原稿' : anchor.document_id === active.revised_document_id ? '修订稿' : '作者回复'}
            · 第 {anchor.page_index + 1} 页 · {anchor.evidence_id}</button>)}</div>
        <details><summary>查看证据支持关系与反证</summary><p><strong>证据支持关系：</strong>{finding.support_assessment || '本次旧分析未记录支持关系说明，建议重新分析。'}</p><p><strong>反证与局限：</strong>{finding.counterargument}</p></details>
        <DeepVerificationPane findingId={finding.id} onEvidence={onEvidence} onCompleted={refreshActiveCase} />
      </article>)}
      {active.model_calls.length > 0 && <p className="review-usage">模型调用 {active.model_calls.length} 次 · 输入 {active.model_calls.reduce((sum, item) => sum + (item.input_tokens || 0), 0)} Token · 输出 {active.model_calls.reduce((sum, item) => sum + (item.output_tokens || 0), 0)} Token · 费用 {active.model_calls.some(item => item.cost_usd === null) ? '未知' : `$${active.model_calls.reduce((sum, item) => sum + (item.cost_usd || 0), 0).toFixed(4)}`}</p>}
      <CrossFormulaPane key={active.id} caseId={active.id}
        papers={documents.filter(item => [active.original_document_id, active.revised_document_id, active.response_document_id].includes(item.id))}
        onEvidence={onEvidence} />
      <ExperimentAuditPane key={`experiment-${active.id}`} caseId={active.id} onEvidence={onEvidence} />
      <AlgorithmAuditPane key={`algorithm-${active.id}`} caseId={active.id} onEvidence={onEvidence} />
    </section>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
