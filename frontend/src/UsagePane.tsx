import {useEffect, useState} from 'react';

type Counts = {calls: number; input_tokens: number; output_tokens: number; duration_ms: number;
  known_cost_usd: number; unpriced_calls: number; failed_calls: number};
type Usage = {total: Counts; by_category: ({category: string} & Counts)[]; by_model: ({model: string} & Counts)[]};

export default function UsagePane() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState('');

  async function refresh() {
    try {
      const response = await fetch('/api/usage/summary');
      if (!response.ok) throw new Error(`用量读取失败 (${response.status})`);
      setUsage(await response.json() as Usage); setError('');
    } catch (cause) {setError(cause instanceof Error ? cause.message : '用量读取失败');}
  }

  useEffect(() => {void refresh();}, []);

  return <div className="usage-pane">
    <div className="eyebrow">本地运行记录</div>
    <div className="usage-title"><h2>模型调用与成本</h2><button onClick={refresh}>刷新</button></div>
    <p>聊天、审稿分析、英文草稿的调用均计入。已知费用仅汇总已配置单价的调用，不能代表全部费用。</p>
    {error && <p role="alert" className="error">{error}</p>}
    {usage && <><div className="usage-stats">
      <div><strong>{usage.total.calls}</strong><span>已记录调用</span></div>
      <div><strong>{usage.total.input_tokens.toLocaleString()} / {usage.total.output_tokens.toLocaleString()}</strong><span>输入 / 输出 Token</span></div>
      <div><strong>{(usage.total.duration_ms / 1000).toFixed(1)} 秒</strong><span>累计执行时间</span></div>
      <div><strong>{usage.total.unpriced_calls}</strong><span>费用未知调用</span></div>
    </div><p>已知费用合计：{usage.total.unpriced_calls === usage.total.calls ? '未配置' : `$${usage.total.known_cost_usd.toFixed(4)}`} · 失败调用：{usage.total.failed_calls}</p>
      <h3>按模型</h3><table><thead><tr><th>模型</th><th>调用</th><th>输入 Token</th><th>输出 Token</th><th>已知费用</th><th>费用未知</th></tr></thead>
        <tbody>{usage.by_model.map(item => <tr className="usage-model-row" key={item.model}><td>{item.model}</td><td>{item.calls}</td>
          <td>{item.input_tokens.toLocaleString()}</td><td>{item.output_tokens.toLocaleString()}</td>
          <td>{item.unpriced_calls === item.calls ? '未配置' : `$${item.known_cost_usd.toFixed(4)}`}</td><td>{item.unpriced_calls}</td></tr>)}</tbody></table>
      <h3>按环节</h3><ul>{usage.by_category.map(item => <li key={item.category}>{item.category}：{item.calls} 次，输入 {item.input_tokens.toLocaleString()} / 输出 {item.output_tokens.toLocaleString()} Token</li>)}</ul>
    </>}
  </div>;
}
