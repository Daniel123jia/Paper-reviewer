import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('single account login protects the real PDF reader and survives refresh', async ({page}) => {
  test.skip(process.env.RUN_AUTH_E2E !== '1', 'Requires a separate authenticated local backend');
  const token = process.env.PAPER_REVIEW_TEST_ACCESS_TOKEN;
  if (!token) throw new Error('PAPER_REVIEW_TEST_ACCESS_TOKEN is required');
  await page.goto('/');
  await expect(page.getByLabel('访问口令')).toBeVisible();
  await expect(page.getByText('已上传论文')).toHaveCount(0);
  await page.getByLabel('访问口令').fill(token);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByText('已上传论文')).toBeVisible();
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await expect(page.getByLabel('PDF 第 1 页')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('PDF 第 1 页')).toBeVisible();
  await page.getByRole('button', {name: '退出登录'}).click();
  await expect(page.getByLabel('访问口令')).toBeVisible();
});
