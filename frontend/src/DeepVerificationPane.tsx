import {useEffect, useState} from 'react';

type Anchor = {evidence_id: string; document_id: string; page_index: number};
type Verification = {id: string; status: string; error: string | null; result: {
  verdict: string; support_assessment: string; rebuttal: string; missing_checks: string;
  scope: {selected_blocks: number; total_blocks: number}; limitation: string; evidence: Anchor[]} | null};

async function getJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

export default function DeepVerificationPane({findingId, onEvidence, onCompleted}: {
  findingId: string; onEvidence: (documentId: string, evidenceId: string) => void;
  onCompleted: () => void;
}) {
  const [records, setRecords] = useState<Verification[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const latest = records[0];

  useEffect(() => {
    setLoading(true);
    getJson<Verification[]>(`/api/review-findings/${findingId}/deep-verifications`).then(setRecords)
      .catch(cause => setError(cause instanceof Error ? cause.message : '深化复核记录加载失败'))
      .finally(() => setLoading(false));
  }, [findingId]);

  useEffect(() => {
    if (!latest || !['queued', 'running'].includes(latest.status)) return;
    const timer = window.setInterval(() => {
      getJson<Verification[]>(`/api/review-findings/${findingId}/deep-verifications`).then(updated => {
        setRecords(updated);
        if (['completed', 'failed'].includes(updated[0]?.status || '')) onCompleted();
      }).catch(cause => setError(cause instanceof Error ? cause.message : '深化复核状态加载失败'));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [findingId, latest?.status]);

  async function run() {
    setBusy(true); setError('');
    try {
      await getJson(`/api/review-findings/${findingId}/deep-verifications`, {method: 'POST'});
      const updated = await getJson<Verification[]>(`/api/review-findings/${findingId}/deep-verifications`);
      setRecords(updated);
      if (['completed', 'failed'].includes(updated[0]?.status || '')) onCompleted();
    } catch (cause) {setError(cause instanceof Error ? cause.message : '深化复核失败');}
    finally {setBusy(false);}
  }

  return <div className="deep-verification">
    <button onClick={run} disabled={loading || busy || ['queued', 'running'].includes(latest?.status || '')}>
      {loading ? '加载复核记录…' : latest ? '再次扩展原文反证 · 消耗 Token' : '扩展原文反证 · 消耗 Token'}</button>
    {latest && <div className="deep-verification-result">
      {latest.status !== 'completed' ? <p role="status">{latest.status === 'failed' ? latest.error : '正在检索整篇论文并独立寻找反证…'}</p>
        : latest.result && <>
          <strong>{latest.result.verdict === 'not_supported' ? '扩展反证认为原候选问题缺乏支持' :
            latest.result.verdict === 'supported_clarification' ? '扩展反证认为可作为待澄清问题' : '扩展反证结果未定'}</strong>
          <p><b>支持关系：</b>{latest.result.support_assessment}</p>
          <p><b>最强反证：</b>{latest.result.rebuttal}</p>
          <p><b>仍需检查：</b>{latest.result.missing_checks}</p>
          <small>从 {latest.result.scope.total_blocks} 个原文块中检索 {latest.result.scope.selected_blocks} 个 · {latest.result.limitation}</small>
          <div>{latest.result.evidence.map(item => <button key={item.evidence_id} onClick={() => onEvidence(item.document_id, item.evidence_id)}>
            原文第 {item.page_index + 1} 页 · {item.evidence_id}</button>)}</div>
        </>}
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
