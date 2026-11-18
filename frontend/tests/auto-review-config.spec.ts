import {test, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test('frozen configuration is posted and persisted without starting', async ({page, request}) => {
  const candidatePath = process.env.AUTO_REVIEW_CANDIDATE_PATH;
  const pdf = process.env.AUTO_REVIEW_UPLOAD_PDF;
  if (!candidatePath || !pdf) throw new Error('Offline candidate and PDF paths are required');
  const candidate = JSON.parse(await readFile(candidatePath, 'utf8'));
  await page.goto('/');
  await page.getByRole('button', {name:'完整AI审稿',exact:true}).click();
  await page.locator('input[type=file]').setInputFiles(pdf);
  await expect(page.getByRole('button',{name:'创建待启动任务'})).toBeVisible({timeout:30000});
  await page.getByText('审稿要求与预算设置').click();
  await page.getByLabel('目标期刊（可选）').fill(candidate.target_journal);
  await page.getByLabel('论文理解特殊要求（P2-A）').fill(candidate.special_requirements);
  await page.getByLabel('审稿要求（可选）').fill(candidate.review_requirements);
  await page.getByLabel('预算配置').selectOption('standard');
  await page.getByText('执行并发与本地速率限制').click();
  await page.getByLabel('最大并发').selectOption('1');
  await page.getByLabel('每分钟请求上限').fill('10');
  await page.getByLabel('每分钟输入预留上限').fill('500000');
  const names:Record<string,string> = {total:'整份任务总预算',p2a:'论文理解',p2b:'六维专业审稿',p2c:'候选问题整合',p3:'证据核验',p4:'中文报告生成'};
  for (const stage of Object.keys(names)) {
    await page.getByText(names[stage], {exact:true}).first().click();
    for (const [key,value] of Object.entries(candidate[`${stage}_budget`]))
      await page.getByLabel(`${stage}_budget ${key}`, {exact:true}).fill(String(value));
  }
  const post = page.waitForRequest(req => req.method()==='POST' && req.url().endsWith('/api/auto-review-tasks'));
  await page.getByRole('button',{name:'创建待启动任务'}).click();
  const sent = (await post).postDataJSON();
  expect(sent).toEqual(candidate);
  await expect(page.getByTestId('auto-task-status')).toHaveText('待确认启动');
  const taskId = (await (await page.request.get(`/api/documents/${candidate.document_id}/auto-review-tasks`)).json())[0].id;
  const saved = await (await request.get(`/api/auto-review-tasks/${taskId}`)).json();
  for (const key of ['document_id','target_journal','special_requirements','review_requirements','execution_policy','total_budget'])
    expect(saved[key]).toEqual(candidate[key]);
  for (const stage of ['p2a','p2b','p2c','p3','p4']) expect(saved.stage_budgets[stage]).toEqual(candidate[`${stage}_budget`]);
  await page.reload();
  await expect(page.getByTestId('auto-task-status')).toHaveText('待确认启动');
  expect(await (await request.get(`/api/auto-review-tasks/${taskId}`)).json()).toEqual(saved);
  await expect(page.getByRole('button',{name:'确认预算并开始完整审稿'})).toBeDisabled();
  expect((await request.post(`/api/auto-review-tasks/${taskId}/start`,{data:{confirm_budget:false}})).status()).toBe(409);
  let starts = 0;
  page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/start')) starts++; });
  await page.route(`**/api/auto-review-tasks/${taskId}`, async route => {
    const response = await route.fetch();
    const altered = await response.json();
    altered.special_requirements = 'mismatched';
    await route.fulfill({response, json:altered});
  });
  await page.getByRole('checkbox',{name:/我确认总预算和各阶段限额/}).check();
  await page.getByRole('button',{name:'确认预算并开始完整审稿'}).click();
  await expect(page.getByRole('alert').first()).toContainText('不一致');
  await expect(page.getByRole('button',{name:'确认预算并开始完整审稿'})).toBeDisabled();
  expect(starts).toBe(0);
});
