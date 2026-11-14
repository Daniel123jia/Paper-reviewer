import {useEffect, useRef, useState} from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import ChatPane from './ChatPane';
import ReviewPane from './ReviewPane';
import DocumentsPane from './DocumentsPane';
import AutoReviewPane from './AutoReviewPane';
import UsagePane from './UsagePane';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Section = {title: string; page_index: number; level: number; evidence_id?: string};
type PageInfo = {page_index: number; width: number; height: number; rotation: number; needs_ocr: boolean};
type Document = {id: string; filename: string; sha256: string; snapshot_id: string; page_count: number; sections: Section[]; pages: PageInfo[]};
type Evidence = {evidence_id: string; page_index: number; excerpt: string; bbox: number[]; precision: string};

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

function Reader({document, pageIndex, zoom, highlight}: {document: Document; pageIndex: number; zoom: number; highlight: Evidence | null}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const markRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<pdfjs.PageViewport | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const task = pdfjs.getDocument({url: `/api/documents/${document.id}/pdf`});
    async function render() {
      try {
        const pdf = await task.promise;
        const page = await pdf.getPage(pageIndex + 1);
        const view = page.getViewport({scale: zoom});
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        const context = canvas.getContext('2d');
        if (!context) return;
        const pixelRatio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(view.width * pixelRatio);
        canvas.height = Math.floor(view.height * pixelRatio);
        canvas.style.width = `${view.width}px`;
        canvas.style.height = `${view.height}px`;
        await page.render({canvas, canvasContext: context, viewport: view, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0]}).promise;
        if (!cancelled) {setViewport(view); setError('');}
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'PDF 页面加载失败');
      }
    }
    setViewport(null);
    render();
    return () => {cancelled = true; task.destroy();};
  }, [document.id, pageIndex, zoom]);

  const page = document.pages[pageIndex];
  let rectangle: {left: number; top: number; width: number; height: number} | null = null;
  if (viewport && highlight && highlight.page_index === pageIndex && page) {
    const [x0, y0, x1, y1] = highlight.bbox;
    const [vx0, vy0] = viewport.convertToViewportPoint(viewport.viewBox[0] + x0, viewport.viewBox[3] - y1);
    const [vx1, vy1] = viewport.convertToViewportPoint(viewport.viewBox[0] + x1, viewport.viewBox[3] - y0);
    rectangle = {left: Math.min(vx0, vx1), top: Math.min(vy0, vy1), width: Math.abs(vx1 - vx0), height: Math.abs(vy1 - vy0)};
  }

  useEffect(() => {
    if (viewport && highlight?.page_index === pageIndex) {
      markRef.current?.scrollIntoView({block: 'center', inline: 'nearest'});
    }
  }, [viewport, highlight?.evidence_id, pageIndex]);

  return <div className="reader-scroll"><div className="paper" style={{width: viewport?.width || undefined, height: viewport?.height || undefined}}>
    <canvas ref={canvasRef} aria-label={`PDF 第 ${pageIndex + 1} 页`} />
    {rectangle && <div ref={markRef} className="evidence-mark" data-testid="evidence-highlight" style={rectangle} title={`定位精度：${highlight?.precision}`} />}
  </div>{page?.needs_ocr && <p className="reader-notice">这一页需要 OCR。可以阅读原页，暂无法建立文字证据。</p>}{error && <p role="alert">{error}</p>}</div>;
}

export default function App({onLogout}: {onLogout?: () => void}) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [current, setCurrent] = useState<Document | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Evidence[]>([]);
  const [highlight, setHighlight] = useState<Evidence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [view, setView] = useState<'search' | 'chat' | 'review' | 'draft' | 'usage' | 'auto'>('search');

  useEffect(() => {
    request<Document[]>('/api/documents').then(loaded => {
      setDocuments(loaded);
      const lastId = window.localStorage.getItem('paper-review-current-document');
      const selected = loaded.find(document => document.id === lastId) || loaded[0] || null;
      if (selected) window.localStorage.setItem('paper-review-current-document', selected.id);
      setCurrent(selected);
    }).catch(cause => setError(cause.message));
  }, []);

  async function upload(file?: File) {
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    setBusy(true); setError('');
    try {
      const document = await request<Document>('/api/documents', {method: 'POST', body});
      setDocuments(previous => [document, ...previous]);
      window.localStorage.setItem('paper-review-current-document', document.id);
      setCurrent(document); setPageIndex(0); setResults([]); setHighlight(null);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '上传失败');}
    finally {setBusy(false);}
  }

  async function search(event: React.FormEvent) {
    event.preventDefault();
    if (!current || !query.trim()) return;
    setError('');
    try {setResults(await request<Evidence[]>(`/api/documents/${current.id}/search?q=${encodeURIComponent(query)}`));}
    catch (cause) {setError(cause instanceof Error ? cause.message : '搜索失败');}
  }

  async function jumpToEvidence(evidenceId: string, result?: Evidence) {
    if (!current) return;
    try {
      const evidence = result || await request<Evidence>(`/api/documents/${current.id}/evidence/${evidenceId}`);
      setPageIndex(evidence.page_index); setHighlight(evidence);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '证据定位失败');}
  }

  async function jumpToReviewEvidence(documentId: string, evidenceId: string) {
    const document = documents.find(item => item.id === documentId);
    if (!document) {setError('证据对应的 PDF 不在工作区'); return;}
    try {
      const evidence = await request<Evidence>(`/api/documents/${documentId}/evidence/${evidenceId}`);
      setCurrent(document);
      window.localStorage.setItem('paper-review-current-document', document.id);
      setPageIndex(evidence.page_index);
      setHighlight(evidence);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '证据定位失败');}
  }

  return <div className="workspace">
    <header className="topbar"><div><span className="brand-mark">P / R</span><span className="brand">论文审稿工作台</span><span className="phase">PDF · 证据 · 对话 · 审稿分析</span></div><div className="topbar-actions"><button onClick={() => setView('usage')}>模型用量</button><label className="upload">{busy ? '正在解析…' : '上传论文 PDF'}<input type="file" accept="application/pdf,.pdf" disabled={busy} onChange={event => {upload(event.target.files?.[0]); event.target.value = '';}} /></label>{onLogout && <button onClick={onLogout}>退出登录</button>}</div></header>
    <aside className="sidebar"><div className="eyebrow">论文工作区</div><h1>从原文开始审稿</h1><p className="intro">上传论文，检索全文，并核对每条证据在原始 PDF 中的位置。</p><div className="section-label">已上传论文 <span>{documents.length}</span></div><div className="document-list">{documents.map(document => <button key={document.id} className={`document-item ${current?.id === document.id ? 'active' : ''}`} onClick={() => {window.localStorage.setItem('paper-review-current-document', document.id); setCurrent(document); setPageIndex(0); setHighlight(null); setResults([]);}}><span className="document-icon">PDF</span><span className="document-name">{document.filename}</span><small>{document.page_count} 页</small></button>)}</div>{current && <><div className="section-label">章节 <span>{current.sections.length}</span></div><nav className="section-list">{current.sections.map((section, index) => <button key={index} onClick={() => {setPageIndex(section.page_index); setHighlight(null); if (section.evidence_id) jumpToEvidence(section.evidence_id);}}>{section.title}<small>第 {section.page_index + 1} 页</small></button>)}</nav></>}</aside>
    <main className="analysis">{current && <div className="view-tabs"><button className={view === 'auto' ? 'selected' : ''} onClick={() => setView('auto')}>完整AI审稿</button><button className={view === 'search' ? 'selected' : ''} onClick={() => setView('search')}>证据检索</button><button className={view === 'chat' ? 'selected' : ''} onClick={() => setView('chat')}>论文对话</button><button className={view === 'review' ? 'selected' : ''} onClick={() => setView('review')}>审稿分析</button><button className={view === 'draft' ? 'selected' : ''} onClick={() => setView('draft')}>审稿文档</button></div>}{view === 'usage' ? <UsagePane /> : current && view === 'auto' ? <AutoReviewPane key={current.id} documentId={current.id} onEvidence={jumpToReviewEvidence} /> : current && view === 'chat' ? <ChatPane key={current.id} documentId={current.id} onEvidence={jumpToEvidence} /> : current && view === 'review' ? <ReviewPane current={current} documents={documents} onEvidence={jumpToReviewEvidence} /> : current && view === 'draft' ? <DocumentsPane key={current.id} documentId={current.id} onEvidence={jumpToReviewEvidence} /> : <><div className="eyebrow">证据检索</div><h2>{current ? current.filename : '等待上传论文'}</h2>{current ? <><p className="document-meta">{current.page_count} 页 · 文件指纹 {current.sha256.slice(0, 12)}…</p><form onSubmit={search} className="search-form"><input aria-label="检索论文全文" placeholder="搜索章节、术语或公式附近文字" value={query} onChange={event => setQuery(event.target.value)} /><button type="submit">检索</button></form><div className="result-heading">搜索结果 <span>{results.length}</span></div>{results.map(result => <button className="result-card" key={result.evidence_id} onClick={() => jumpToEvidence(result.evidence_id, result)}><span className="result-page">第 {result.page_index + 1} 页 · {result.precision === 'match' ? '文字定位' : '段落定位'}</span><span className="result-text">{result.excerpt}</span><code>{result.evidence_id}</code></button>)}{results.length === 0 && query && <p className="empty-note">输入关键词后点击检索，结果会显示在这里。</p>}</> : <div className="empty"><div className="empty-symbol">⌁</div><p>上传真实论文 PDF，开始建立可追溯的原文证据。</p></div>}{error && <p className="error" role="alert">{error}</p>}</>}</main>
    <section className="reader-panel"><div className="reader-header"><div><div className="eyebrow">原始论文</div><strong>{current ? `第 ${pageIndex + 1} / ${current.page_count} 页` : 'PDF 阅读器'}</strong></div>{current && <div className="reader-controls"><button aria-label="上一页" disabled={pageIndex === 0} onClick={() => {setPageIndex(pageIndex - 1); setHighlight(null);}}>‹</button><button aria-label="下一页" disabled={pageIndex + 1 >= current.page_count} onClick={() => {setPageIndex(pageIndex + 1); setHighlight(null);}}>›</button><button aria-label="缩小" onClick={() => setZoom(Math.max(0.5, zoom - 0.2))}>−</button><span>{Math.round(zoom * 100)}%</span><button aria-label="放大" onClick={() => setZoom(Math.min(2.4, zoom + 0.2))}>＋</button></div>}</div>{current ? <Reader document={current} pageIndex={pageIndex} zoom={zoom} highlight={highlight} /> : <div className="reader-empty">原始 PDF 将在这里显示</div>}</section>
  </div>;
}
