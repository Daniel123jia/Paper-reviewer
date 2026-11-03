import {useEffect, useState} from 'react';
import App from './App';

type AuthStatus = {required: boolean; authenticated: boolean};

export default function AuthGate() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetch('/api/auth/status').then(async response => {
      if (!response.ok) throw new Error(`状态检查失败 (${response.status})`);
      return response.json() as Promise<AuthStatus>;
    }).then(value => {if (!cancelled) setStatus(value);})
      .catch(cause => {if (!cancelled) setError(cause instanceof Error ? cause.message : '无法连接后端');});
    return () => {cancelled = true;};
  }, []);

  async function login(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const response = await fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({access_token: token})});
      if (!response.ok) throw new Error('访问口令无效');
      setToken(''); setStatus({required: true, authenticated: true});
    } catch (cause) {setError(cause instanceof Error ? cause.message : '登录失败');}
    finally {setBusy(false);}
  }

  async function logout() {
    try {
      const response = await fetch('/api/auth/logout', {method: 'POST'});
      if (!response.ok) throw new Error('退出失败');
      setStatus({required: true, authenticated: false});
    } catch (cause) {setError(cause instanceof Error ? cause.message : '退出失败');}
  }

  if (!status) return <div className="auth-screen" role="status">{error || '正在连接审稿工作台…'}</div>;
  if (!status.required || status.authenticated) return <App onLogout={status.required ? logout : undefined} />;
  return <main className="auth-screen"><form className="auth-card" onSubmit={login}>
    <div className="eyebrow">AI 论文深度审稿工作台</div>
    <h1>登录工作台</h1>
    <p>请输入本地配置的访问口令，以查看论文和审稿记录。</p>
    <label>访问口令<input type="password" autoComplete="current-password" value={token}
      onChange={event => setToken(event.target.value)} /></label>
    <button type="submit" disabled={busy || !token}>{busy ? '正在登录…' : '登录'}</button>
    {error && <p role="alert" className="error">{error}</p>}
  </form></main>;
}
