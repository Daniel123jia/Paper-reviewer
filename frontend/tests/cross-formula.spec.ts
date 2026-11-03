import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('real PDF equations can be linked and checked offline', async ({page}) => {
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '审稿分析'}).click();
  await page.getByRole('button', {name: '创建任务'}).click();
  const caseLabel = await page.locator('.review-history button.selected').innerText();
  await expect(page.getByRole('region', {name: '跨证据离线核查'})).toBeVisible();
  await page.getByRole('button', {name: '跳到离线核查'}).click();
  await expect(page.getByRole('heading', {name: '跨证据离线核查'})).toBeInViewport();

  const search = page.getByRole('textbox', {name: '检索核查原文'});
  await page.getByRole('button', {name: '选取公式 (4) 与 (5)'}).click();
  await expect(page.getByText('已选原文 2/2')).toBeVisible();

  await page.getByRole('textbox', {name: '左侧标量表达式'}).fill('q*(k+a)/sqrt(d)');
  await page.getByRole('textbox', {name: '右侧标量表达式'}).fill('(q*k+q*a)/sqrt(d)');
  await page.getByRole('textbox', {name: '假设符号'}).fill('d');
  await page.getByRole('button', {name: '运行离线核查 · 不消耗 Token'}).click();
  await expect(page.locator('.formula-checks article').first()).toContainText('输入表达式等价');
  await expect(page.locator('.formula-checks article').first()).toContainText('原文转录状态：未确认');
  await expect(page.locator('.formula-checks article').first().locator('button')).toHaveCount(2);

  await search.fill('BLEU');
  await page.getByRole('button', {name: '检索原文'}).click();
  await page.locator('.formula-matches button').filter({hasText: '26.5'}).first().click();
  await search.fill('For English-to-German');
  await page.getByRole('button', {name: '检索原文'}).click();
  await page.locator('.formula-matches button').filter({hasText: 'improved'}).first().click();
  await page.getByRole('textbox', {name: '左侧标量表达式'}).fill('26.8-26.5');
  await page.getByRole('textbox', {name: '右侧标量表达式'}).fill('0.3');
  await page.getByRole('textbox', {name: '假设符号'}).fill('');
  await page.getByRole('button', {name: '运行离线核查 · 不消耗 Token'}).click();
  await expect(page.locator('.formula-checks article').first()).toContainText('输入表达式等价');
  await expect(page.locator('.formula-checks article').first()).toContainText('26.8-26.5');
  await expect(page.locator('.formula-checks article').first()).toContainText('输入数字均出现在对应原文');
  await expect(page.locator('.formula-checks article')).toHaveCount(2);

  await page.getByRole('button', {name: '运行实验数值核查 · 不消耗 Token'}).click();
  await expect(page.getByRole('region', {name: '实验数值核查'})).toContainText('四项数值与正文一致');
  await expect(page.locator('.experiment-result tbody tr')).toHaveCount(4);
  await page.locator('.experiment-audit').screenshot({path: 'test-results/experiment-audit.png'});
  await page.getByRole('button', {name: '表格原文 · 第 4 页'}).click();
  await expect(page.getByText('第 4 / 5 页')).toBeVisible();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.getByRole('button', {name: '运行算法维度核查 · 不消耗 Token'}).click();
  await expect(page.getByRole('region', {name: '算法维度核查'})).toContainText('已解析步骤的输出规模一致');
  await expect(page.locator('.algorithm-check')).toHaveCount(2);
  await expect(page.locator('.algorithm-check').first()).toContainText('b*h*n**2');
  await page.locator('.algorithm-audit').screenshot({path: 'test-results/algorithm-audit.png'});
  await page.locator('.algorithm-check button').first().click();
  await expect(page.getByText('第 3 / 5 页')).toBeVisible();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name: '审稿分析'}).click();
  await page.getByRole('button', {name: caseLabel}).click();
  await expect(page.locator('.formula-checks article').first()).toContainText('26.8-26.5');
  await expect(page.locator('.experiment-result tbody tr')).toHaveCount(4);
  await expect(page.locator('.algorithm-check')).toHaveCount(2);
});

test('wrong scalar transcription yields a witness without accusing the paper', async ({page}) => {
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '审稿分析'}).click();
  await page.getByRole('button', {name: '创建任务'}).click();
  await page.getByRole('button', {name: '选取公式 (4) 与 (5)'}).click();
  await page.getByRole('textbox', {name: '左侧标量表达式'}).fill('q*(k+a)/sqrt(d)');
  await page.getByRole('textbox', {name: '右侧标量表达式'}).fill('(q*k+a)/sqrt(d)');
  await page.getByRole('textbox', {name: '假设符号'}).fill('d');
  await page.getByRole('button', {name: '运行离线核查 · 不消耗 Token'}).click();
  await expect(page.locator('.formula-checks article').first()).toContainText('输入表达式存在数值反例');
  await expect(page.locator('.formula-checks article').first()).toContainText('原文转录状态：未确认；此结果不能直接认定论文有错');
});
