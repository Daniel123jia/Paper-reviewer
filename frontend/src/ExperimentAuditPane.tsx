import {useEffect, useState} from 'react';

type Source = {evidence_id: string; document_id: string; page_index: number};
type Audit = {id: string; source_status: string; table_source: Source; claim_source: Source;
  result: {status: string; limitation: string; checks: {metric: string; group: string;
    baseline: string; relative: string; calculated: string; claimed: string; matches: boolean}[]}};

async function getJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

export default function ExperimentAuditPane({caseId, onEvidence}: {
  caseId: string; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [audits, setAudits] = useState<Audit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getJson<Audit[]>(`/api/review-cases/${caseId}/experiment-audits`).then(setAudits)
      .catch(cause => setError(cause instanceof Error ? cause.message : '实验核查记录加载失败'));
  }, [caseId]);

  async function run() {
    setBusy(true); setError('');
    try {
      const created = await getJson<Audit>(`/api/review-cases/${caseId}/experiment-audits`, {method: 'POST'});
      setAudits(previous => [created, ...previous.filter(item => item.id !== created.id)]);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '实验核查失败');}
    finally {setBusy(false);}
  }

  return <section className="experiment-audit" aria-label="实验数值核查">
    <h3>实验数值核查</h3>
    <p className="review-hint">自动对照可识别的成对 BLEU 表格与正文提升值，核对 base / big 两组结果。当前仅支持明确标注 Absolute / Relative、EN-DE / EN-FR 的表格布局；无法解析时会说明原因。</p>
    <button onClick={run} disabled={busy}>运行实验数值核查 · 不消耗 Token</button>
    {audits.map(audit => <article key={audit.id} className="experiment-result">
      <strong>{audit.result.status === 'consistent' ? '四项数值与正文一致' : '发现数值不一致，需核对 PDF 原文'}</strong>
      <table><thead><tr><th>指标</th><th>模型</th><th>表格差值</th><th>正文声称</th><th>核对</th></tr></thead>
        <tbody>{audit.result.checks.map(item => <tr key={`${item.metric}-${item.group}`}>
          <td>{item.metric}</td><td>{item.group}</td><td>{item.relative} − {item.baseline} = {item.calculated}</td>
          <td>{item.claimed}</td><td>{item.matches ? '一致' : '不一致'}</td>
        </tr>)}</tbody></table>
      <p>{audit.result.limitation}</p>
      <div className="experiment-sources">
        <button onClick={() => onEvidence(audit.table_source.document_id, audit.table_source.evidence_id)}>表格原文 · 第 {audit.table_source.page_index + 1} 页</button>
        <button onClick={() => onEvidence(audit.claim_source.document_id, audit.claim_source.evidence_id)}>结论原文 · 第 {audit.claim_source.page_index + 1} 页</button>
      </div>
    </article>)}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
