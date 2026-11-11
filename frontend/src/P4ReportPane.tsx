import {useEffect, useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import './P4ReportPane.css';

type Run = {id: string; status: string; consolidation_run_id: string};
type ProjectSummary = {id: string; p3_run_id: string};
type Source = {source_id: string; source_type: string; page_index: number | null;
  evidence_ids: string[]; source_verification_status: string; relevant_excerpt: string};
type Issue = {consolidated_issue_id: string; verification_task_id: string; title: string;
  scientific_question: string; verdict: string; report_eligibility: string;
  human_review_status: string; processing_status: string; source_refs: string[];
  counterevidence_refs: string[]; outstanding_checks: string[]};
type Decision = {consolidated_issue_id: string; decision: string; reviewer_approval_status: string;
  reviewer_rationale: string; report_order: number};
type EvidenceReview = {record_id: string; consolidated_issue_id: string; source_ref_id: string;
  confirmation_status: string};
type ManualIssue = {issue_id: string; scientific_question: string; origin: string;
  source_refs: string[]; evidence_basis: string; category: string};
type BundleSource = {evidence_ids: string[]; page_index: number; object_type: string;
  verification_status: string};
type CoverageGap = {gap_id: string; gap_type: string; description: string;
  next_action: string; status: string};
type DraftSummary = {id: string; kind: string; language: string; revision_number: number;
  approval_status: string};
type Project = {id: string; document_id: string; source_p3_run_id: string; execution_status: string;
  source: {snapshot_id: string; p3_execution_mode: string; pdf_sha256: string};
  issues: Issue[]; sources: Source[]; decisions: Decision[]; evidence_reviews: EvidenceReview[];
  reviewer_added: ManualIssue[];
  bundle_source_refs: Record<string, BundleSource>; coverage_gaps: CoverageGap[];
  drafts: DraftSummary[]};
type Draft = {id: string; kind: string; language: string; body: string; revision_number: number;
  approval_status: string; selected_issue_ids: string[];
  model_calls: {input_tokens: number | null; output_tokens: number | null; status: string}[];
  revisions: {revision_number: number; approval_status: string}[]};
type VisualForm = {checked_rows_columns: string; checked_values_and_labels: string;
  metric_names_and_order: string; unresolved_configurations: string; reviewer_note: string};

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}
const json = (data: unknown): RequestInit => ({headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(data)});
const statusName: Record<string, string> = {
  auto_draft_candidate: '可进入待审核草稿', human_review_required: '需人工核验',
  internal_only: '仅内部保留', excluded: '正式意见排除',
  concern_narrowed: '限定性问题', inconclusive: '尚无定论', blocked: '来源受阻',
  concern_refuted: '已被反证', concern_supported: '有证据支持',
  requires_human_review: '需人工复核', eligible_with_qualification: '限定条件准入',
  pending: '待确认',
};
const blankVisual = (): VisualForm => ({checked_rows_columns: '', checked_values_and_labels: '',
  metric_names_and_order: '', unresolved_configurations: '', reviewer_note: ''});

export default function P4ReportPane({documentId, onEvidence}: {
  documentId: string; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState('');
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState<Project | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [body, setBody] = useState('');
  const [kind, setKind] = useState<'internal' | 'author' | 'editor'>('author');
  const [language, setLanguage] = useState<'zh' | 'en'>('zh');
  const [rationales, setRationales] = useState<Record<string, string>>({});
  const [orders, setOrders] = useState<Record<string, number>>({});
  const [visual, setVisual] = useState<Record<string, VisualForm>>({});
  const [manualQuestion, setManualQuestion] = useState('');
  const [manualBasis, setManualBasis] = useState('');
  const [manualRefs, setManualRefs] = useState('');
  const [confirmContent, setConfirmContent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api<Run[]>(`/api/documents/${documentId}/evidence-verification-runs`),
      api<ProjectSummary[]>(`/api/documents/${documentId}/review-report-projects`),
    ]).then(([nextRuns, nextProjects]) => {
      if (cancelled) return;
      setRuns(nextRuns); setProjects(nextProjects);
      setRunId(nextRuns.find(item => ['completed', 'limited'].includes(item.status))?.id || '');
      setProjectId(nextProjects[0]?.id || '');
    }).catch(cause => {if (!cancelled) setError(String(cause));});
    return () => {cancelled = true;};
  }, [documentId]);

  useEffect(() => {
    let cancelled = false;
    setProject(null); setDraft(null); setBody('');
    setConfirmContent(false); setRationales({}); setOrders({}); setVisual({});
    setManualQuestion(''); setManualBasis(''); setManualRefs('');
    setKind('author'); setLanguage('zh');
    if (projectId) api<Project>(`/api/review-report-projects/${projectId}`).then(async value => {
      if (cancelled || value.document_id !== documentId) return;
      setProject(value);
      if (value.drafts.length) {
        const latest = await api<Draft>(`/api/review-report-drafts/${value.drafts[0].id}`);
        if (!cancelled) {setDraft(latest); setBody(latest.body);}
      }
    }).catch(cause => {if (!cancelled) setError(String(cause));});
    return () => {cancelled = true;};
  }, [projectId, documentId]);

  async function act(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try {await work();} catch (cause) {setError(cause instanceof Error ? cause.message : String(cause));}
    finally {setBusy(false);}
  }
  async function refreshProject(id: string) {
    const loaded = await api<Project>(`/api/review-report-projects/${id}`);
    if (loaded.document_id !== documentId) throw new Error('报告项目不属于当前论文');
    setProject(loaded);
  }
  async function createProject() {
    if (!runId) return;
    await act(async () => {
      const created = await api<Project>(`/api/evidence-verification-runs/${runId}/report-projects`,
        {method: 'POST'});
      setProjects(previous => [{id: created.id, p3_run_id: runId}, ...previous]);
      setProjectId(created.id); setProject(created); setNotice('已建立冻结来源的报告项目');
    });
  }
  async function decide(issue: Issue, decision: string) {
    if (!project) return;
    await act(async () => {
      const reviews = project.evidence_reviews.filter(item => item.consolidated_issue_id === issue.consolidated_issue_id &&
        item.confirmation_status === 'confirmed').map(item => item.record_id);
      await api(`/api/review-report-projects/${project.id}/issues/${issue.consolidated_issue_id}/decision`,
        {method: 'PATCH', ...json({decision, reviewer_rationale: rationales[issue.consolidated_issue_id] ??
          project.decisions.find(item => item.consolidated_issue_id === issue.consolidated_issue_id)?.reviewer_rationale ?? '',
          selected_source_refs: issue.source_refs, evidence_review_record_ids: reviews,
          report_order: orders[issue.consolidated_issue_id] || 0})});
      await refreshProject(project.id); setNotice('审核决定已保存；P3 原判定未改变');
    });
  }
  async function recordVisual(issue: Issue, source: Source) {
    if (!project || source.page_index === null) return;
    const form = visual[source.source_id] || blankVisual();
    await act(async () => {
      await api(`/api/review-report-projects/${project.id}/issues/${issue.consolidated_issue_id}/evidence-reviews`,
        {method: 'POST', ...json({source_ref_id: source.source_id, page_index: source.page_index,
          object_label: source.relevant_excerpt.slice(0, 160) || source.source_type,
          checked_rows_columns: form.checked_rows_columns,
          checked_values_and_labels: form.checked_values_and_labels,
          metric_names_and_order: form.metric_names_and_order,
          unresolved_configurations: form.unresolved_configurations.split('\n').filter(Boolean),
          reviewer_note: form.reviewer_note, confirmation_status: 'confirmed'})});
      await refreshProject(project.id); setNotice('人工证据复核记录已保存；实验配置仍按记录保持未确认');
    });
  }
  async function decideManual(item: ManualIssue, decision: string) {
    if (!project) return;
    await act(async () => {
      await api(`/api/review-report-projects/${project.id}/issues/${item.issue_id}/decision`,
        {method: 'PATCH', ...json({decision,
          reviewer_rationale: rationales[item.issue_id] || item.evidence_basis,
          selected_source_refs: item.source_refs, report_order: orders[item.issue_id] || 0})});
      await refreshProject(project.id); setNotice('人工意见的审核决定已保存');
    });
  }
  async function addManual() {
    if (!project) return;
    await act(async () => {
      const response = await fetch(`/api/review-report-projects/${project.id}/reviewer-issues`, {method: 'POST',
        ...json({scientific_question: manualQuestion, evidence_basis: manualBasis,
          source_refs: manualRefs.split(/[\s,]+/).filter(Boolean), comment_body: '',
          category: 'clarification'})});
      if (!response.ok) {const detail = await response.json().catch(() => ({})); throw new Error(detail.detail || `请求失败 (${response.status})`);}
      setManualQuestion(''); setManualBasis(''); setManualRefs('');
      await refreshProject(project.id); setNotice(response.status === 200
        ? '相同人工意见已存在，已返回原记录；审核和草稿版本未改变'
        : '人工意见已记录，请单独审核后生成新草稿');
    });
  }
  async function createDraft() {
    if (!project) return;
    await act(async () => {
      const created = await api<Draft>(`/api/review-report-projects/${project.id}/drafts`,
        {method: 'POST', ...json({kind, language})});
      setDraft(created); setBody(created.body); setConfirmContent(false);
      await refreshProject(project.id); setNotice('新草稿已创建；已有版本仍可打开');
    });
  }
  async function openDraft(id: string) {
    await act(async () => {
      const opened = await api<Draft>(`/api/review-report-drafts/${id}`);
      setDraft(opened); setBody(opened.body); setConfirmContent(false);
      setNotice('已打开保存的草稿，未调用模型');
    });
  }
  async function saveDraft() {
    if (!draft) return;
    await act(async () => {
      const saved = await api<Draft>(`/api/review-report-drafts/${draft.id}`,
        {method: 'PUT', ...json({body, expected_revision: draft.revision_number})});
      setDraft(saved); setBody(saved.body); setConfirmContent(false);
      if (project) await refreshProject(project.id);
      setNotice('修改已保存；此版本需要重新审核');
    });
  }
  async function approveDraft() {
    if (!draft) return;
    await act(async () => {
      const approved = await api<Draft>(`/api/review-report-drafts/${draft.id}/approve`,
        {method: 'POST', ...json({expected_revision: draft.revision_number, confirm_content: confirmContent})});
      setDraft(approved); if (project) await refreshProject(project.id);
      setNotice('当前确切版本已审核；这不等同于完成独立原文核验');
    });
  }
  function updateVisual(sourceId: string, field: keyof VisualForm, value: string) {
    setVisual(previous => ({...previous, [sourceId]: {...(previous[sourceId] || blankVisual()), [field]: value}}));
  }

  const sourceById = new Map(project?.sources.map(item => [item.source_id, item]) || []);
  const dirty = Boolean(draft && draft.body !== body);
  const unknownUsage = draft?.model_calls.filter(call => call.input_tokens === null || call.output_tokens === null).length || 0;
  return <div className="p4-pane draft-pane">
    <div className="eyebrow">专业同行评审报告 · P4</div>
    <h2>从已核验的问题形成审稿意见</h2>
    <p className="draft-intro">P3 判定、报告准入与人工批准分别记录。未审核稿下载时带 Draft 标识；正式导出只针对已批准的确切版本。</p>
    <section className="p4-card">
      <h3>1. 选择来源</h3>
      <label>P3 核验运行<select aria-label="P3 核验运行" value={runId} onChange={event => setRunId(event.target.value)}>
        <option value="">请选择</option>{runs.map(item => <option key={item.id} value={item.id} disabled={!['completed', 'limited'].includes(item.status)}>{item.id.slice(0, 8)} · {item.status}</option>)}
      </select></label>
      <button disabled={!runId || busy} onClick={createProject}>从所选 P3 运行建立新报告项目</button>
      <label>已有报告项目<select aria-label="已有 P4 报告项目" value={projectId} onChange={event => setProjectId(event.target.value)}>
        <option value="">请选择</option>{projects.map(item => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · P3 {item.p3_run_id.slice(0, 8)}</option>)}
      </select></label>
      {project && <p className="p4-meta">P3 {project.source_p3_run_id.slice(0, 8)} · {project.execution_status} · 来源模式 {project.source.p3_execution_mode} · PDF {project.source.pdf_sha256.slice(0, 12)}…</p>}
      {!runs.length && <p className="draft-intro">当前论文还没有可用的 P3 核验运行。</p>}
    </section>
    {project && <>
      <section className="p4-card p4-report"><h3>报告正文与导出</h3>
        {project.drafts.length === 0 && <p className="draft-intro">当前项目尚无已保存报告。可在下方选择报告类型并明确生成新草稿。</p>}
        {draft && <div className="draft-editor"><div className="draft-editor-head"><strong>{draft.kind} · {draft.language} · 修订 {draft.revision_number} · {draft.approval_status} · {draft.revision_number > 1 ? '含人工修订' : '保存的报告草稿'}</strong></div>
          <div className="draft-preview"><strong>报告阅读视图</strong><div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{body}</ReactMarkdown></div></div>
          {draft.kind === 'author' && draft.selected_issue_ids.length === 0 && <p className="draft-intro">当前没有可进入作者意见的问题。请先新增并审核有依据的人工意见，再生成新草稿。</p>}
          <details className="p4-edit"><summary>编辑正文</summary>
            <textarea aria-label="P4 报告正文" rows={20} value={body} readOnly={draft.kind === 'author' && draft.selected_issue_ids.length === 0}
              onChange={event => setBody(event.target.value)} />
            <button disabled={busy || !dirty} onClick={saveDraft}>保存当前编辑</button>
          </details>
          <p className="p4-meta">历史版本：{draft.revisions.map(item => `${item.revision_number} (${item.approval_status})`).join('、')}</p>
          <label className="p4-confirm"><input type="checkbox" checked={confirmContent} onChange={event => setConfirmContent(event.target.checked)} />我已审核当前确切版本的正文；这不等于独立确认所有科学事实。</label>
          <button disabled={busy || dirty || !confirmContent} onClick={approveDraft}>批准当前版本</button>
          <div className="draft-export"><span>{dirty ? '请先保存修改' : draft.approval_status === 'approved' ? '正式版本：' : '待审核草稿（带 Draft 标识）：'}</span>
            {!dirty && (['md', 'docx', 'pdf'] as const).map(format => <a key={format} href={`/api/review-report-drafts/${draft.id}/export?format=${format}${draft.approval_status === 'approved' ? '&final=true' : ''}`} download>{format.toUpperCase()}</a>)}</div>
          <small className="draft-usage">P4 模型调用 {draft.model_calls.length} 次 · 已知输入 {draft.model_calls.reduce((sum, call) => sum + (call.input_tokens ?? 0), 0)} Token · 已知输出 {draft.model_calls.reduce((sum, call) => sum + (call.output_tokens ?? 0), 0)} Token{unknownUsage > 0 && ` · ${unknownUsage} 次 Usage 未知，费用不完整`}</small>
        </div>}
        <div className="p4-controls"><label>报告类型<select aria-label="P4 报告类型" value={kind} onChange={event => setKind(event.target.value as typeof kind)}>
          <option value="author">作者意见草稿</option><option value="internal">内部完整报告</option><option value="editor">编辑保密说明</option></select></label>
          <label>语言<select aria-label="P4 报告语言" value={language} onChange={event => setLanguage(event.target.value as typeof language)}><option value="zh">中文</option><option value="en">English</option></select></label>
          <button disabled={busy} onClick={createDraft}>明确生成新草稿</button></div>
        {project.drafts.length > 0 && <div className="p4-drafts"><strong>打开已有草稿</strong>{project.drafts.map(item => <button key={item.id} onClick={() => openDraft(item.id)}>
          {item.kind} · {item.language} · 修订 {item.revision_number} · {item.approval_status}</button>)}</div>}
      </section>
      <details className="p4-card p4-audit"><summary>技术核验与人工复核 · {project.issues.length} 项问题 · {project.coverage_gaps.length} 项覆盖缺口</summary>
        <p className="p4-meta">自动问题保持 P3 原裁定；人工补充意见单独标记。AI 辅助定位不等于人工确认。</p>
        {project.issues.map(issue => {
          const decision = project.decisions.find(item => item.consolidated_issue_id === issue.consolidated_issue_id);
          const visualSources = [...issue.source_refs, ...issue.counterevidence_refs].map(id => sourceById.get(id))
            .filter((item): item is Source => Boolean(item && item.source_verification_status === 'visual_check_required'));
          return <article className="p4-issue" key={issue.consolidated_issue_id}>
            <strong>自动核验问题 · {issue.title}</strong><p>{issue.scientific_question}</p>
            <div className="p4-badges"><span>{statusName[issue.verdict] || issue.verdict}</span><span>{statusName[issue.report_eligibility] || issue.report_eligibility}</span>
              <span>人工原文状态 {statusName[issue.human_review_status] || issue.human_review_status}</span><span>{statusName[issue.processing_status]}</span>
              <span>报告决定 {statusName[decision?.reviewer_approval_status || 'pending'] || decision?.reviewer_approval_status}</span></div>
            {issue.outstanding_checks.length > 0 && <details><summary>未完成检查 · {issue.outstanding_checks.length}</summary><ul>{issue.outstanding_checks.map((text, i) => <li key={i}>{text}</li>)}</ul></details>}
            <div className="p4-sources">{[...new Set([...issue.source_refs, ...issue.counterevidence_refs])].map(id => {
              const source = sourceById.get(id);
              return source && <button key={id} title={`Source Ref: ${id}`} onClick={() => source.evidence_ids[0] && onEvidence(documentId, source.evidence_ids[0])}
                disabled={!source.evidence_ids.length}>原文第 {(source.page_index ?? 0) + 1} 页 · {source.source_type} · {source.source_verification_status} · {id}</button>;
            })}</div>
            {visualSources.map(source => <details className="p4-visual" key={source.source_id}>
              <summary>人工复核 {source.source_type} · PDF 第 {(source.page_index ?? 0) + 1} 页</summary>
              <p>请先点击上方原文定位，在 PDF 中确认对象；采纳意见不会代替这项核查。</p>
              <input aria-label={`${source.source_id} 行列`} placeholder="核对的行、列和模型标签" value={visual[source.source_id]?.checked_rows_columns || ''} onChange={event => updateVisual(source.source_id, 'checked_rows_columns', event.target.value)} />
              <input aria-label={`${source.source_id} 数值`} placeholder="实际数值及其对应标签" value={visual[source.source_id]?.checked_values_and_labels || ''} onChange={event => updateVisual(source.source_id, 'checked_values_and_labels', event.target.value)} />
              <input aria-label={`${source.source_id} 指标`} placeholder="指标名称与顺序" value={visual[source.source_id]?.metric_names_and_order || ''} onChange={event => updateVisual(source.source_id, 'metric_names_and_order', event.target.value)} />
              <textarea aria-label={`${source.source_id} 未确认配置`} placeholder="仍未确认的实验配置（每行一项）" value={visual[source.source_id]?.unresolved_configurations || ''} onChange={event => updateVisual(source.source_id, 'unresolved_configurations', event.target.value)} />
              <textarea aria-label={`${source.source_id} 复核说明`} placeholder="记录实际检查过程" value={visual[source.source_id]?.reviewer_note || ''} onChange={event => updateVisual(source.source_id, 'reviewer_note', event.target.value)} />
              <button disabled={busy} onClick={() => recordVisual(issue, source)}>保存人工原文复核</button>
            </details>)}
            <label>审核理由<textarea aria-label={`${issue.consolidated_issue_id} 审核理由`} value={rationales[issue.consolidated_issue_id] ?? decision?.reviewer_rationale ?? ''}
              onChange={event => setRationales(previous => ({...previous, [issue.consolidated_issue_id]: event.target.value}))} /></label>
            <label>报告顺序<input aria-label={`${issue.consolidated_issue_id} 报告顺序`} type="number" min="0" value={orders[issue.consolidated_issue_id] ?? decision?.report_order ?? 0}
              onChange={event => setOrders(previous => ({...previous, [issue.consolidated_issue_id]: Number(event.target.value)}))} /></label>
            <div className="p4-actions"><button disabled={busy || issue.processing_status === 'internal_only' || issue.processing_status === 'excluded'} onClick={() => decide(issue, 'approve_for_draft')}>采纳到待审核草稿</button>
              <button disabled={busy || issue.processing_status === 'internal_only' || issue.processing_status === 'excluded'} onClick={() => decide(issue, 'approve_for_final')}>批准此条进入最终稿</button>
              <button disabled={busy} onClick={() => decide(issue, 'request_more_verification')}>要求进一步核查</button>
              <button disabled={busy} onClick={() => decide(issue, 'exclude')}>排除</button></div>
          </article>;
        })}
        <details className="p4-manual"><summary>新增人工审稿意见</summary>
          <p>人工补充意见与 P3 自动问题分开记录，需要原文证据或明确的核验依据。</p>
          <input aria-label="人工科学问题" placeholder="具体科学问题" value={manualQuestion} onChange={event => setManualQuestion(event.target.value)} />
          <textarea aria-label="人工证据依据" placeholder="实际核验依据" value={manualBasis} onChange={event => setManualBasis(event.target.value)} />
          <input aria-label="人工来源 ID" placeholder="Source Ref，可用逗号分隔" value={manualRefs} onChange={event => setManualRefs(event.target.value)} />
          <button disabled={busy} onClick={addManual}>保存人工问题</button>
        </details>
        {project.reviewer_added.map(item => <article className="p4-issue" key={item.issue_id}>
          <strong>人工补充 · {item.scientific_question}</strong><p>{item.evidence_basis}</p>
          <p className="p4-meta">来源 reviewer_added · {statusName[project.decisions.find(decision => decision.consolidated_issue_id === item.issue_id)?.reviewer_approval_status || 'pending'] || '待确认'}</p>
          <div className="p4-sources">{item.source_refs.map(id => {
            const source = project.bundle_source_refs[id];
            return source && <button key={id} title={`Source Ref: ${id}`} onClick={() => onEvidence(documentId, source.evidence_ids[0])}
              disabled={!source.evidence_ids.length}>原文第 {source.page_index + 1} 页 · {source.object_type} · {source.verification_status} · {id}</button>;
          })}</div>
          <label>人工意见审核理由<textarea aria-label={`${item.issue_id} 审核理由`} value={rationales[item.issue_id] || ''}
            onChange={event => setRationales(previous => ({...previous, [item.issue_id]: event.target.value}))} /></label>
          <div className="p4-actions"><button disabled={busy} onClick={() => decideManual(item, 'approve_for_draft')}>采纳到待审核草稿</button>
            <button disabled={busy} onClick={() => decideManual(item, 'approve_for_final')}>批准进入最终稿</button>
            <button disabled={busy} onClick={() => decideManual(item, 'exclude')}>排除</button></div>
        </article>)}
        <details className="p4-coverage"><summary>CoverageGap 原始审计 · {project.coverage_gaps.length} 项</summary>
          <ul>{project.coverage_gaps.map(gap => <li key={gap.gap_id}>
            <strong>{gap.gap_type} · {gap.status} · {gap.gap_id}</strong><p>{gap.description}</p><p>下一步：{gap.next_action}</p>
          </li>)}</ul>
        </details>
      </details>
    </>}
    {notice && <p role="status" className="draft-notice">{notice}</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}
