import {test, expect} from '@playwright/test';

const caseId = 'ec2b53cf04894f47870d545112c5d0e0';

test('real paper yields editable report and formal opinion downloads', async ({page}) => {
  test.skip(process.env.RUN_REAL_EXPORT !== '1', 'Requires the existing real PDF review case');
  test.setTimeout(120000);
  await page.goto('/');
  await page.getByRole('button', {name: /Self-Attention with Relative Position Representations\.pdf 5 页/}).click();
  await page.getByRole('button', {name: '审稿分析'}).click();
  await page.getByRole('button', {name: '一审 · ec2b53cf'}).click();
  await page.getByRole('button', {name: '运行实验数值核查 · 不消耗 Token'}).click();
  await expect(page.locator('.experiment-result tbody tr')).toHaveCount(4);
  await page.getByRole('button', {name: '运行算法维度核查 · 不消耗 Token'}).click();
  await expect(page.locator('.algorithm-check')).toHaveCount(2);
  if (await page.locator('.formula-checks article').count() === 0) {
    await page.getByRole('button', {name: '选取公式 (4) 与 (5)'}).click();
    await page.getByRole('textbox', {name: '左侧标量表达式'}).fill('q*(k+a)/sqrt(d)');
    await page.getByRole('textbox', {name: '右侧标量表达式'}).fill('(q*k+q*a)/sqrt(d)');
    await page.getByRole('textbox', {name: '假设符号'}).fill('d');
    await page.getByRole('button', {name: '运行离线核查 · 不消耗 Token'}).click();
    await expect(page.locator('.formula-checks article').first()).toContainText('输入表达式等价');
  }

  await page.getByRole('button', {name: '审稿文档'}).click();
  await page.getByRole('combobox', {name: '文档审稿任务'}).selectOption(caseId);
  await expect(page.getByRole('combobox', {name: '文档审稿任务'})).toHaveValue(caseId);
  await page.getByRole('combobox', {name: '文档类型'}).selectOption('report');
  await page.getByRole('button', {name: '生成或打开草稿'}).click();
  const editor = page.getByRole('textbox', {name: '审稿文档正文'});
  await expect(editor).toHaveValue(/内部完整审稿报告/);
  await expect(editor).toHaveValue(/ev_c1c08dc5e672_2_28/);
  await expect(editor).toHaveValue(/\| 指标 \| 模型 \|/);
  await expect(editor).toHaveValue(/\$\$/);
  const originalBody = (await editor.inputValue()).split('\n\n## 本地编辑验收')[0].trimEnd() + '\n';
  await editor.fill(originalBody + `\n\n## 本地编辑验收\n\n此段由浏览器修改并保存。${Date.now()}\n`);
  await page.getByRole('button', {name: '保存修改'}).click();
  await expect(page.getByRole('status')).toContainText('修改已保存');
  const reportWord = page.waitForEvent('download');
  await page.getByRole('link', {name: 'DOCX'}).click();
  await (await reportWord).saveAs('test-results/real-report-zh.docx');
  const reportPdf = page.waitForEvent('download');
  await page.getByRole('link', {name: 'PDF'}).click();
  await (await reportPdf).saveAs('test-results/real-report-zh.pdf');

  await page.getByRole('combobox', {name: '文档类型'}).selectOption('opinion');
  await page.getByRole('button', {name: '生成或打开草稿'}).click();
  await expect(editor).toHaveValue(/正式审稿意见/);
  await expect(editor).toHaveValue(/空间复杂度/);
  await expect(editor).not.toHaveValue(/已证实数学错误/);
  const opinionPdf = page.waitForEvent('download');
  await page.getByRole('link', {name: 'PDF'}).click();
  await (await opinionPdf).saveAs('test-results/real-opinion-zh.pdf');
  const opinionMd = page.waitForEvent('download');
  await page.getByRole('link', {name: 'MD'}).click();
  await (await opinionMd).saveAs('test-results/real-opinion-zh.md');
  await page.reload();
  await page.getByRole('button', {name: '审稿文档'}).click();
  await page.getByRole('combobox', {name: '文档审稿任务'}).selectOption(caseId);
  await page.getByRole('button', {name: '内部完整审稿报告 · 中文'}).click();
  await expect(page.getByRole('textbox', {name: '审稿文档正文'})).toHaveValue(/此段由浏览器修改并保存/);
  await page.getByRole('textbox', {name: '审稿文档正文'}).fill(originalBody);
  if (await page.getByRole('button', {name: '保存修改'}).isEnabled()) {
    await page.getByRole('button', {name: '保存修改'}).click();
    await expect(page.getByRole('status')).toContainText('修改已保存');
  }

  await page.getByRole('combobox', {name: '文档语言'}).selectOption('en');
  await page.getByRole('combobox', {name: '文档类型'}).selectOption('opinion');
  await page.getByRole('button', {name: '生成或打开草稿 · 消耗 Token'}).click();
  await expect(editor).toHaveValue(/Formal Review/);
  await expect(page.locator('.draft-usage')).toContainText('输入');
  const englishPdf = page.waitForEvent('download');
  await page.getByRole('link', {name: 'PDF'}).click();
  await (await englishPdf).saveAs('test-results/real-opinion-en.pdf');
});
