import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('review modes request the correct PDF materials', async ({page}) => {
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '审稿分析'}).click();
  const mode = page.getByRole('combobox', {name: '审稿类型'});
  await mode.selectOption('own_rereview');
  await expect(page.getByRole('combobox', {name: '修订稿'})).toBeVisible();
  await expect(page.getByRole('combobox', {name: '作者回复'})).toBeVisible();
  await expect(page.getByText('请先上传修订稿和作者回复的 PDF。')).toBeVisible();
  await mode.selectOption('new_reviewer');
  await expect(page.getByRole('combobox', {name: '修订稿'})).toBeVisible();
  await expect(page.getByRole('combobox', {name: '作者回复'})).toHaveCount(0);
  await mode.selectOption('first_review');
  await expect(page.getByRole('combobox', {name: '修订稿'})).toHaveCount(0);
  await page.getByRole('button', {name: '创建任务'}).click();
  await expect(page.getByRole('button', {name: '开始 AI 分析 · 消耗 Token'})).toBeVisible();
});
