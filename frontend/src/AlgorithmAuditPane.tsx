import {useEffect, useState} from 'react';

type Check = {parallel_count: string; left_shape: string[]; right_shape: string[];
  output_shape: string[] | null; total_output_elements: string | null; status: string;
  source: {evidence_id: string; document_id: string; page_index: number}};
type Audit = {id: string; result: {status: string; limitation: string; checks: Check[]}};

async function getJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

export default function AlgorithmAuditPane({caseId, onEvidence}: {
  caseId: string; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [audits, setAudits] = useState<Audit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getJson<Audit[]>(`/api/review-cases/${caseId}/algorithm-audits`).then(setAudits)
      .catch(cause => setError(cause instanceof Error ? cause.message : '算法核查记录加载失败'));
  }, [caseId]);

  async function run() {
    setBusy(true); setError('');
    try {
      const created = await getJson<Audit>(`/api/review-cases/${caseId}/algorithm-audits`, {method: 'POST'});
      setAudits(previous => [created, ...previous.filter(item => item.id !== created.id)]);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '算法核查失败');}
    finally {setBusy(false);}
  }

  return <section className="algorithm-audit" aria-label="算法维度核查">
    <h3>算法维度核查</h3>
    <p className="review-hint">从论文中明确描述的并行矩阵乘法推导输出形状和元素数量。只接受能解析的维度形式，无法确认的步骤保持未定。</p>
    <button onClick={run} disabled={busy}>运行算法维度核查 · 不消耗 Token</button>
    {audits.map(audit => <article key={audit.id} className="algorithm-result">
      <strong>{audit.result.status === 'shape_consistent' ? '已解析步骤的输出规模一致' :
        audit.result.status === 'shape_mismatch' ? '发现矩阵内维度不匹配，需核对原文' : '维度结果未定'}</strong>
      {audit.result.checks.map(check => <div key={check.source.evidence_id} className="algorithm-check">
        <span>{check.parallel_count} 次并行：({check.left_shape.join(' × ')}) × ({check.right_shape.join(' × ')})
          {' → '}{check.output_shape ? check.output_shape.join(' × ') : '内维度不匹配'}
          {check.total_output_elements ? `；总输出元素 ${check.total_output_elements}` : ''}</span>
        <button onClick={() => onEvidence(check.source.document_id, check.source.evidence_id)}>
          原文第 {check.source.page_index + 1} 页 · {check.source.evidence_id}</button>
      </div>)}
      <p>{audit.result.limitation}</p>
    </article>)}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
