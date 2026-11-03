import {useEffect, useState} from 'react';

type Paper = {id: string; filename: string};
type Source = {evidence_id: string; document_id: string; page_index: number; excerpt: string};
type Check = {id: string; lhs: string; rhs: string; evidence_ids: string[]; sources: Source[];
  source_status: string; result: {status: string; difference: string; source_number_matches?: {all_present: boolean | null}; counterexample: {
    symbols: Record<string, number>; lhs: number; rhs: number} | null}};
type FormulaLink = {from_equation: number; to_equation: number; left: Source; right: Source; relation: Source};

async function getJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

export default function CrossFormulaPane({caseId, papers, onEvidence}: {
  caseId: string; papers: Paper[]; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [documentId, setDocumentId] = useState(papers[0]?.id || '');
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<Source[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [lhs, setLhs] = useState('');
  const [rhs, setRhs] = useState('');
  const [symbol, setSymbol] = useState('');
  const [assumption, setAssumption] = useState('positive');
  const [checks, setChecks] = useState<Check[]>([]);
  const [links, setLinks] = useState<FormulaLink[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getJson<Check[]>(`/api/review-cases/${caseId}/cross-formula-checks`).then(setChecks)
      .catch(cause => setError(cause instanceof Error ? cause.message : '核查记录加载失败'));
    getJson<FormulaLink[]>(`/api/review-cases/${caseId}/formula-links`).then(setLinks)
      .catch(cause => setError(cause instanceof Error ? cause.message : '公式关联加载失败'));
  }, [caseId]);

  async function search(event: React.FormEvent) {
    event.preventDefault(); setError(''); setMatches([]);
    try {
      const found = await getJson<Source[]>(`/api/documents/${documentId}/search?q=${encodeURIComponent(query)}`);
      setMatches(found);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '原文检索失败');}
  }

  function select(source: Source) {
    if (sources.some(item => item.evidence_id === source.evidence_id)) return;
    setSources(previous => [...previous.slice(-1), source]);
  }

  async function check(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const assumptions = symbol.trim() ? {[symbol.trim()]: assumption} : {};
      const created = await getJson<Check>(`/api/review-cases/${caseId}/cross-formula-checks`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lhs, rhs, assumptions, evidence_ids: sources.map(item => item.evidence_id)}),
      });
      setChecks(previous => [created, ...previous]);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '公式核查失败');}
    finally {setBusy(false);}
  }

  return <section id="cross-formula" className="cross-formula-pane" aria-label="跨证据离线核查">
    <h3>跨证据离线核查</h3>
    <p className="review-hint">从两处原文选择证据，再将公式关系或实验数值写成可比较的表达式。SymPy / NumPy 只核查输入的表达式；PDF 转录、矩阵维度与论文结论仍需另行确认。本操作不消耗中转站 Token。</p>
    {links.length > 0 && <div className="formula-links"><strong>原文中的公式关联线索</strong>
      {links.map(link => <div key={`${link.left.evidence_id}-${link.right.evidence_id}`}>
        <button onClick={() => setSources([link.left, link.right])}>选取公式 ({link.from_equation}) 与 ({link.to_equation})</button>
        <button onClick={() => onEvidence(link.relation.document_id, link.relation.evidence_id)}>查看关联描述 · 第 {link.relation.page_index + 1} 页</button>
        <small>由原文“拆分/改写”等描述推断关联；是否数学等价仍需验证。</small>
      </div>)}
    </div>}
    <form className="formula-search" onSubmit={search}>
      <select aria-label="检索材料" value={documentId} onChange={event => {setDocumentId(event.target.value); setMatches([]);}}>
        {papers.map(paper => <option value={paper.id} key={paper.id}>{paper.filename}</option>)}
      </select>
      <input aria-label="检索核查原文" placeholder="例如：(4) 或 BLEU" value={query} onChange={event => setQuery(event.target.value)} required />
      <button>检索原文</button>
    </form>
    {matches.length > 0 && <div className="formula-matches" aria-label="公式检索结果">
      {matches.map(item => <button key={item.evidence_id} onClick={() => select(item)}>
        第 {item.page_index + 1} 页 · {item.excerpt.slice(0, 180)}
      </button>)}
    </div>}
    <div className="formula-sources"><strong>已选原文 {sources.length}/2</strong>
      {sources.map(item => <div key={item.evidence_id}>
        <button onClick={() => onEvidence(item.document_id, item.evidence_id)}>第 {item.page_index + 1} 页 · {item.evidence_id}</button>
        <span>{item.excerpt.slice(0, 240)}</span>
        <button aria-label={`移除 ${item.evidence_id}`} onClick={() => setSources(previous => previous.filter(value => value.evidence_id !== item.evidence_id))}>移除</button>
      </div>)}
    </div>
    <form className="formula-check-form" onSubmit={check}>
      <label>左侧标量表达式<input aria-label="左侧标量表达式" value={lhs} onChange={event => setLhs(event.target.value)} placeholder="q*(k+a)/sqrt(d)" required /></label>
      <label>右侧标量表达式<input aria-label="右侧标量表达式" value={rhs} onChange={event => setRhs(event.target.value)} placeholder="(q*k+q*a)/sqrt(d)" required /></label>
      <div className="formula-assumption"><label>可选符号假设<input aria-label="假设符号" value={symbol} onChange={event => setSymbol(event.target.value)} placeholder="例如 d" /></label>
        <label>类型<select aria-label="假设类型" value={assumption} onChange={event => setAssumption(event.target.value)}>
          <option value="positive">大于 0</option><option value="real">实数</option><option value="nonzero">非零</option><option value="integer">整数</option>
        </select></label></div>
      <button disabled={busy || sources.length !== 2}>运行离线核查 · 不消耗 Token</button>
    </form>
    {checks.length > 0 && <div className="formula-checks"><strong>核查记录</strong>
      {checks.map(item => <article key={item.id}>
        <b>{item.result.status === 'equivalent' ? '输入表达式等价' : item.result.status === 'counterexample' ? '输入表达式存在数值反例' : '结果未定'}</b>
        <p>{item.lhs}  ↔  {item.rhs}</p><small>化简差：{item.result.difference}</small>
        {item.result.source_number_matches?.all_present !== null && item.result.source_number_matches?.all_present !== undefined &&
          <small>{item.result.source_number_matches.all_present ? '输入数字均出现在对应原文；行列与语义仍需核对。' : '部分输入数字未在对应原文找到，请检查转录。'}</small>}
        {item.result.counterexample && <pre>反例：{JSON.stringify(item.result.counterexample)}</pre>}
        <small>原文转录状态：未确认；此结果不能直接认定论文有错。</small>
        <div>{item.sources.map(source => <button key={source.evidence_id} onClick={() => onEvidence(source.document_id, source.evidence_id)}>
          原文第 {source.page_index + 1} 页 · {source.evidence_id}</button>)}</div>
      </article>)}
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
