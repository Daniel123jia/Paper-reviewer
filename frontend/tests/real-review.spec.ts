import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('real review analysis preserves evidence and countercheck after refresh', async ({page}) => {
  test.skip(process.env.RUN_REAL_REVIEW !== '1', 'Requires a configured live model endpoint');
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '审稿分析'}).click();
  await expect(page.getByRole('combobox', {name: '审稿类型'})).toHaveValue('first_review');
  await page.getByRole('button', {name: '创建任务'}).click();
  await expect(page.getByRole('button', {name: '开始 AI 分析 · 消耗 Token'})).toBeVisible();
  const caseButton = page.locator('.review-history button.selected');
  const caseLabel = await caseButton.innerText();
  await page.getByRole('button', {name: '开始 AI 分析 · 消耗 Token'}).click();
  await expect(page.getByRole('status')).toContainText('分析完成', {timeout: 180000});
  await expect(page.locator('.finding-card').first()).toBeVisible();
  await expect(page.locator('.finding-card').first()).toContainText('待独立验证');
  await page.locator('.finding-card details summary').first().click();
  await expect(page.locator('.finding-card details p').first()).toBeVisible();
  await expect(page.locator('.finding-card details p').first()).not.toContainText('旧分析未记录');
  await page.locator('.finding-evidence button').first().click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name: '审稿分析'}).click();
  await page.getByRole('button', {name: caseLabel}).click();
  await expect(page.locator('.finding-card').first()).toBeVisible();
});
