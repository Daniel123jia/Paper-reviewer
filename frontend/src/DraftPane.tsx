import {useEffect, useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import './DraftPane.css';

type Anchor = {evidence_id: string; document_id: string; page_index: number};
type ReviewCase = {id: string; mode: string; original_document_id: string;
  revised_document_id: string | null; response_document_id: string | null;
  findings: {evidence: Anchor[]}[]};
type Draft = {id: string; case_id: string; kind: string; language: string; body: string;
  model_calls: {input_tokens: number | null; output_tokens: number | null}[]};

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

const kinds: Record<string, string> = {report: '内部完整审稿报告', opinion: '正式审稿意见'};
const modes: Record<string, string> = {first_review: '一审', own_rereview: '本人复审', new_reviewer: '新增审稿人二审'};

export default function DraftPane({documentId, onEvidence}: {
  documentId: string; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [caseId, setCaseId] = useState('');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [active, setActive] = useState<Draft | null>(null);
  const [body, setBody] = useState('');
  const [kind, setKind] = useState('report');
  const [language, setLanguage] = useState('zh');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;
    json<ReviewCase[]>('/api/review-cases').then(all => {
      if (cancelled) return;
      const matching = all.filter(item => [item.original_document_id, item.revised_document_id,
        item.response_document_id].includes(documentId));
      setCases(matching);
      setCaseId(current => matching.some(item => item.id === current) ? current : matching[0]?.id || '');
    }).catch(cause => {if (!cancelled) setError(cause instanceof Error ? cause.message : '审稿任务加载失败');});
    return () => {cancelled = true;};
  }, [documentId]);

  useEffect(() => {
    let cancelled = false;
    setDrafts([]); setActive(null); setBody(''); setError(''); setNotice('');
    if (caseId) {
      json<Draft[]>(`/api/review-cases/${caseId}/drafts`).then(items => {if (!cancelled) setDrafts(items);})
        .catch(cause => {if (!cancelled) setError(cause instanceof Error ? cause.message : '草稿加载失败');});
    }
    return () => {cancelled = true;};
  }, [caseId]);

  async function create() {
    if (!caseId) return;
    setBusy(true); setError(''); setNotice(language === 'en' ? '正在翻译英文草稿…' : '正在创建草稿…');
    try {
      const draft = await json<Draft>(`/api/review-cases/${caseId}/drafts`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind, language}),
      });
      setDrafts(previous => [draft, ...previous.filter(item => item.id !== draft.id)]);
      setActive(draft); setBody(draft.body); setNotice('草稿已保存');
    } catch (cause) {setError(cause instanceof Error ? cause.message : '创建草稿失败'); setNotice('');}
    finally {setBusy(false);}
  }

  async function save() {
    if (!active) return;
    setBusy(true); setError('');
    try {
      const updated = await json<Draft>(`/api/review-drafts/${active.id}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({body}),
      });
      setActive(updated);
      setDrafts(previous => previous.map(item => item.id === updated.id ? updated : item));
      setNotice('修改已保存，可下载当前版本');
    } catch (cause) {setError(cause instanceof Error ? cause.message : '保存失败');}
    finally {setBusy(false);}
  }

  const selectedCase = cases.find(item => item.id === caseId);
  const anchors = selectedCase?.findings.flatMap(item => item.evidence) || [];
  const cited = [...new Set(body.match(/ev_[A-Za-z0-9_]+/g) || [])];
  const dirty = Boolean(active && body !== active.body);

  return <div className="draft-pane">
    <div className="eyebrow">审稿文档 · 编辑与导出</div>
    <h2>从可复核记录生成文档</h2>
    <p className="draft-intro">完整报告保留候选问题、技术检查和证据附录；正式意见只纳入扩展反证支持的澄清问题。英文草稿会调用中转站并消耗 Token，保存与导出本身不消耗。</p>
    <div className="draft-controls">
      <label>审稿任务<select aria-label="文档审稿任务" value={caseId} onChange={event => setCaseId(event.target.value)}>
        {cases.map(item => <option value={item.id} key={item.id}>{modes[item.mode]} · {item.id.slice(0, 8)}</option>)}
      </select></label>
      <label>文档类型<select aria-label="文档类型" value={kind} onChange={event => setKind(event.target.value)}>
        <option value="report">内部完整审稿报告</option><option value="opinion">正式审稿意见</option>
      </select></label>
      <label>语言<select aria-label="文档语言" value={language} onChange={event => setLanguage(event.target.value)}>
        <option value="zh">中文</option><option value="en">English</option>
      </select></label>
      <button disabled={!caseId || busy} onClick={create}>生成或打开草稿{language === 'en' ? ' · 消耗 Token' : ''}</button>
    </div>
    {!caseId && <p className="draft-intro">请先在“审稿分析”建立任务。</p>}
    {drafts.length > 0 && <div className="draft-list"><strong>已有草稿</strong><div>{drafts.map(item =>
      <button key={item.id} className={active?.id === item.id ? 'selected' : ''} onClick={() => {setActive(item); setBody(item.body); setNotice('');}}>
        {kinds[item.kind]} · {item.language === 'zh' ? '中文' : 'English'}</button>)}</div></div>}
    {active && <section className="draft-editor">
      <div className="draft-editor-head"><strong>{kinds[active.kind]} · {active.language === 'zh' ? '中文' : 'English'}</strong>
        <button onClick={save} disabled={busy || !dirty}>保存修改</button></div>
      <textarea aria-label="审稿文档正文" value={body} onChange={event => {setBody(event.target.value); setNotice('有未保存的修改');}} rows={18} />
      <div className="draft-export"><span>{dirty ? '请先保存修改再下载。' : '下载已保存版本：'}</span>
        {!dirty && (['md', 'docx', 'pdf'] as const).map(format => <a key={format}
          href={`/api/review-drafts/${active.id}/export?format=${format}`} download>{format.toUpperCase()}</a>)}
      </div>
      {active.model_calls.length > 0 && <small className="draft-usage">草稿模型调用：输入 {active.model_calls.reduce((sum, item) => sum + (item.input_tokens || 0), 0)} Token · 输出 {active.model_calls.reduce((sum, item) => sum + (item.output_tokens || 0), 0)} Token</small>}
      {cited.length > 0 && <div className="draft-evidence"><strong>文档中的原文证据</strong><div>{cited.map(id => {
        const anchor = anchors.find(item => item.evidence_id === id);
        return anchor ? <button key={id} onClick={() => onEvidence(anchor.document_id, id)}>第 {anchor.page_index + 1} 页 · {id}</button> : <span key={id}>{id}</span>;
      })}</div></div>}
      <div className="draft-preview"><strong>排版预览</strong><div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{body}</ReactMarkdown></div></div>
    </section>}
    {notice && <p role="status" className="draft-notice">{notice}</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}
