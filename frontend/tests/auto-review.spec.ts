import {test, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test('complete review uses real backend and fake SDK, refresh never dispatches', async ({page}) => {
  test.setTimeout(90000);
  await page.goto('/');
  await page.getByRole('button', {name:'完整AI审稿',exact:true}).click();
  if(process.env.AUTO_REVIEW_UPLOAD_PDF) await page.locator('input[type=file]').setInputFiles(process.env.AUTO_REVIEW_UPLOAD_PDF);
  await expect(page.getByRole('heading', {name:'完整AI审稿',exact:true})).toBeVisible();
  await expect(page.getByText('本地规划费用上限')).toBeVisible();
  await expect(page.getByText('1.45 USD', {exact:true})).toBeVisible();
  await expect(page.getByText(/最多 60 次请求/)).toBeHidden();
  await page.getByText('审稿要求与预算设置').click();
  const profile = page.getByLabel('预算配置');
  await profile.selectOption('standard');
  await expect(page.getByText('3.20 USD', {exact:true})).toBeVisible();
  await profile.selectOption('short');
  await page.getByText('整份任务总预算', {exact:true}).click();
  await page.getByLabel('total_budget max_cost_usd', {exact:true}).fill('1.40');
  await page.getByText('审稿要求与预算设置').click();
  expect((await page.locator('.auto-review-pane').boundingBox())!.width).toBeGreaterThan(500);
  await expect(page.getByRole('region',{name:'原始论文'})).toBeHidden();
  if(process.env.AUTO_REVIEW_SCREENSHOT) await page.screenshot({path:process.env.AUTO_REVIEW_SCREENSHOT.replace('.png','-setup.png')});
  await page.getByRole('button', {name:'创建待启动任务'}).click();
  await expect(page.getByTestId('auto-task-status')).toHaveText('待确认启动');
  await page.reload();
  await expect(page.getByText('1.40 USD', {exact:true})).toBeVisible();
  const limits = page.locator('.auto-start-card dl');
  await expect(limits.first().getByText('60', {exact:true})).toBeHidden();
  await page.getByText('查看预算明细').click();
  await page.getByText('总预算', {exact:true}).click();
  await expect(limits.first().getByText('60', {exact:true})).toBeVisible();
  await expect(limits.first().getByText('900,000', {exact:true})).toBeVisible();
  await page.getByText('查看预算明细').click();
  await expect(page.getByRole('button',{name:'确认预算并开始完整审稿'})).toBeDisabled();
  await expect(page.getByTestId('auto-request-count')).toBeHidden();
  await page.getByRole('checkbox',{name:/我确认总预算和各阶段限额/}).check();
  await page.getByRole('button',{name:'确认预算并开始完整审稿'}).click();
  if(process.env.AUTO_REVIEW_CAPTURE_PROGRESS && process.env.AUTO_REVIEW_SCREENSHOT) {
    const stages = page.getByRole('list',{name:'完整审稿阶段进度'});
    await expect(stages.getByRole('listitem')).toHaveCount(6);
    await page.screenshot({path:process.env.AUTO_REVIEW_SCREENSHOT.replace('.png','-progress.png')});
  }
  await expect(page.getByTestId('auto-task-status')).toHaveText('报告已生成',{timeout:60000});
  await expect(page.locator('.draft-preview .markdown-body')).toContainText('方法适用范围的澄清');
  await page.locator('.analysis').evaluate(el=>{el.scrollTop=0;});

  await expect(page.getByRole('region',{name:'审稿用量'})).toContainText('本地费用估算（USD）');
  await expect(page.getByText('供应商实际扣费未核实。', {exact:true})).toBeVisible();
  const requests = await page.getByTestId('auto-request-count').textContent();
  await page.reload();
  await expect(page.getByTestId('auto-task-status')).toHaveText('报告已生成');
  await expect(page.getByTestId('auto-request-count')).toHaveText(requests!);
  await expect(page.getByRole('heading',{name:'中文审稿报告'})).toBeVisible();
  await page.getByRole('button',{name:/原文第 1 页/}).filter({visible:true}).first().click();
  await expect(page.getByRole('region',{name:'原始论文'})).toBeVisible();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.getByRole('button',{name:'关闭原文'}).click();
  await expect(page.getByRole('region',{name:'原始论文'})).toBeHidden();
  const mdEvent=page.waitForEvent('download');
  await page.getByRole('link',{name:'下载 Markdown',exact:true}).click();
  expect((await readFile((await (await mdEvent).path())!)).toString()).toContain('DRAFT');
  const pdfEvent=page.waitForEvent('download');
  await page.getByRole('link',{name:'下载 PDF',exact:true}).click();
  expect((await readFile((await (await pdfEvent).path())!)).subarray(0,5).toString()).toBe('%PDF-');
  await page.locator('.analysis').evaluate(el=>{el.scrollTop=0;});
  const screenshot = process.env.AUTO_REVIEW_SCREENSHOT || '/tmp/auto-review-completed.png';
  await page.screenshot({path:screenshot});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'中文审稿报告'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.getByRole('link',{name:'下载 PDF'})).toBeVisible();
  if(process.env.AUTO_REVIEW_SCREENSHOT) await page.screenshot({path:screenshot.replace('.png','-mobile.png')});
  await page.getByRole('button',{name:'原文工作台'}).click();
  await expect(page.getByRole('button',{name:'证据检索'})).toBeVisible();
});

test('cost display separates pending usage from an unknown charge after stopping', async ({page}) => {
  let stopped = false;
  const taskId = 'cost-display-fixture';
  await page.route('**/api/documents/*/auto-review-tasks', route => route.fulfill({
    json: [{id: taskId, status:'running', created_at:''}],
  }));
  await page.route(`**/api/auto-review-tasks/${taskId}`, route => route.fulfill({
    json: {
      id: taskId, status: stopped ? 'unknown_usage' : 'running', request_count:1,
      known_input_tokens:0, known_output_tokens:0, known_cost_usd:'0', unknown_usage_calls:1,
      calls:[{status:stopped ? 'unknown_usage' : 'dispatch_pending'}], stages:[],
    },
  }));
  await page.goto('/?view=auto');
  const usage = page.getByRole('region',{name:'审稿用量'});
  await expect(usage).toContainText('1 次请求等待用量回报');
  await expect(page.getByText(/任务已停止。有 1 次请求/)).toBeHidden();
  stopped = true;
  await expect(page.getByRole('alert')).toContainText('1 次请求用量未知');
  await expect(usage).toContainText('暂无可核算用量');
  await expect(usage).toContainText('供应商实际扣费未核实');
  await expect(usage).not.toContainText('等待用量回报');
});
