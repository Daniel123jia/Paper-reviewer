import {useEffect, useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

type Citation = {evidence_id: string; page_index: number};
type Message = {id: string; role: string; content: string; citations: Citation[]};
type Conversation = {id: string; document_id: string; created_at: string; messages?: Message[]};
type Source = {evidence_id: string; page_index: number; excerpt: string};

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

function renderableMath(content: string): string {
  return content.replace(/\\\[/g, '\n$$\n').replace(/\\\]/g, '\n$$\n')
    .replace(/\\\(/g, '$').replace(/\\\)/g, '$');
}

export default function ChatPane({documentId, onEvidence}: {documentId: string; onEvidence: (id: string) => void}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [message, setMessage] = useState('');
  const [streaming, setStreaming] = useState('');
  const [sources, setSources] = useState<Source[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [taskStatus, setTaskStatus] = useState('');

  useEffect(() => {
    setActive(null); setConversations([]); setSources([]); setError(''); setTaskStatus('');
    json<Conversation[]>(`/api/documents/${documentId}/conversations`).then(setConversations)
      .catch(cause => setError(cause.message));
  }, [documentId]);

  async function choose(id: string) {
    try {setActive(await json<Conversation>(`/api/conversations/${id}`)); setSources([]); setError(''); setTaskStatus('');}
    catch (cause) {setError(cause instanceof Error ? cause.message : '加载对话失败');}
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const question = message.trim();
    if (!question || busy) return;
    setBusy(true); setError(''); setStreaming(''); setSources([]); setTaskStatus('正在连接模型…');
    let conversationId = active?.id;
    let completed = false;
    try {
      let conversation = active;
      if (!conversation) {
        conversation = await json<Conversation>(`/api/documents/${documentId}/conversations`, {method: 'POST'});
        conversationId = conversation.id;
        setConversations(previous => [conversation!, ...previous]);
        setActive({...conversation, messages: []});
      }
      const response = await fetch(`/api/conversations/${conversation.id}/messages/stream`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: question}),
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || '对话连接失败');
      }
      setMessage('');
      setActive(previous => previous ? {...previous, messages: [...(previous.messages || []),
        {id: crypto.randomUUID(), role: 'user', content: question, citations: []}]} : previous);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let failed = false;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value, {stream: true});
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';
        for (const rawEvent of events) {
          const eventType = rawEvent.match(/^event: (.+)$/m)?.[1];
          const payload = rawEvent.match(/^data: (.+)$/m)?.[1];
          if (!payload) continue;
          const data = JSON.parse(payload);
          if (eventType === 'sources') {
            setSources(data.evidence);
            setTaskStatus(data.evidence.length ? `已检索 ${data.evidence.length} 处原文证据` : '未检索到匹配的原文证据');
          }
          if (eventType === 'delta') {setStreaming(previous => previous + data.text); setTaskStatus('正在生成回答…');}
          if (eventType === 'error') {setError(data.message); setTaskStatus('回答未完成，请重试'); failed = true;}
          if (eventType === 'done') completed = true;
        }
      }
      if (!completed && !failed) {setError('对话连接中断，请重试'); setTaskStatus('回答未完成，请重试');}
      setActive(await json<Conversation>(`/api/conversations/${conversation.id}`));
      setStreaming('');
      if (completed && !failed) setTaskStatus('回答已保存');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '模型调用失败');
      setTaskStatus(completed ? '历史加载失败，请刷新检查回答' : '回答未完成，请重试');
      if (conversationId) {
        try {setActive(await json<Conversation>(`/api/conversations/${conversationId}`));} catch { /* Keep current state. */ }
      }
      setStreaming('');
    }
    finally {setBusy(false);}
  }

  return <div className="chat-pane">
    <div className="chat-head"><div><div className="eyebrow">论文对话</div><h2>围绕原文追问</h2></div><button onClick={() => {setActive(null); setStreaming(''); setSources([]); setTaskStatus('');}}>新建对话</button></div>
    {conversations.length > 0 && <div className="conversation-list">{conversations.map((conversation, index) =>
      <button key={conversation.id} data-conversation-id={conversation.id} className={active?.id === conversation.id ? 'selected' : ''} onClick={() => choose(conversation.id)}>对话 {conversations.length - index}</button>)}</div>}
    <div className="message-list">{(active?.messages || []).map(item => <div className={`message ${item.role}`} key={item.id}>
      <div className="message-role">{item.role === 'user' ? '你' : '论文助手'}</div>
      <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{renderableMath(item.content)}</ReactMarkdown></div>
      {item.citations.length > 0 && <div className="citation-list">{item.citations.map(citation => <button key={citation.evidence_id} onClick={() => onEvidence(citation.evidence_id)}>第 {citation.page_index + 1} 页 · {citation.evidence_id}</button>)}</div>}
    </div>)}{streaming && <div className="message assistant"><div className="message-role">论文助手 · 正在回答</div><div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{renderableMath(streaming)}</ReactMarkdown></div></div>}{!active?.messages?.length && !streaming && <p className="chat-empty">提出关于方法、公式或实验的问题。回答会标出可打开的原文位置。</p>}</div>
    {sources.length > 0 && <div className="source-strip"><strong>本轮检索的原文</strong>{sources.map(source => <button key={source.evidence_id} onClick={() => onEvidence(source.evidence_id)}>第 {source.page_index + 1} 页</button>)}</div>}
    {taskStatus && <p className="chat-status" role="status">{taskStatus}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <form className="chat-compose" onSubmit={send}><textarea aria-label="向论文提问" value={message} onChange={event => setMessage(event.target.value)} placeholder="例如：论文如何证明相对位置表示有效？" rows={3} /><button disabled={busy || !message.trim()}>{busy ? '回答中…' : '发送'}</button></form>
  </div>;
}
