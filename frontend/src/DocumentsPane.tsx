import {useState} from 'react';
import DraftPane from './DraftPane';
import P4ReportPane from './P4ReportPane';

export default function DocumentsPane({documentId, onEvidence}: {
  documentId: string; onEvidence: (documentId: string, evidenceId: string) => void;
}) {
  const [mode, setMode] = useState<'p4' | 'legacy'>('p4');
  return <div>
    <div className="view-tabs" aria-label="审稿文档模式">
      <button className={mode === 'p4' ? 'selected' : ''} onClick={() => setMode('p4')}>专业审稿报告（P4）</button>
      <button className={mode === 'legacy' ? 'selected' : ''} onClick={() => setMode('legacy')}>旧版任务草稿</button>
    </div>
    {mode === 'p4' ? <P4ReportPane documentId={documentId} onEvidence={onEvidence} /> :
      <DraftPane documentId={documentId} onEvidence={onEvidence} />}
  </div>;
}
