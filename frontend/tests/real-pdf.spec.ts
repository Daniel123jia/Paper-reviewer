import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('opens an existing paper for a fresh browser without a saved selection', async ({page, browser}) => {
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await expect(page.getByLabel('PDF 第 1 页')).toBeVisible();

  const freshContext = await browser.newContext();
  try {
    const freshPage = await freshContext.newPage();
    await freshPage.goto('/');
    await expect(freshPage.getByRole('button', {name: /Self-Attention with Relative Position Representations\.pdf 5 页/})).toHaveClass(/active/);
    await expect(freshPage.getByRole('button', {name: '证据检索'})).toBeVisible();
    await expect(freshPage.getByLabel('PDF 第 1 页')).toBeVisible();
    await expect(freshPage.getByRole('button', {name: '1 Introduction 第 1 页'})).toBeVisible();
  } finally {
    await freshContext.close();
  }
});

test('uploads a real paper, searches, jumps and highlights at different zoom levels', async ({page}) => {
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await expect(page.getByText('5 页 · 文件指纹')).toBeVisible();
  await expect(page.getByLabel('PDF 第 1 页')).toBeVisible();
  await expect(page.getByRole('button', {name: '1 Introduction 第 1 页'})).toBeVisible();
  await page.getByRole('textbox', {name: '检索论文全文'}).fill('Relative Position Representations');
  await page.getByRole('button', {name: '检索', exact: true}).click();
  await expect(page.locator('.result-card').first()).toBeVisible();
  await page.locator('.result-card').first().click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  const before = await page.getByTestId('evidence-highlight').boundingBox();
  await page.getByRole('button', {name: '放大'}).click();
  await expect.poll(async () => (await page.getByTestId('evidence-highlight').boundingBox())?.width || 0).toBeGreaterThan(before!.width);
  await page.getByRole('button', {name: '下一页'}).click();
  await expect(page.getByText('第 2 / 5 页')).toBeVisible();
  await page.getByRole('button', {name: '2 Background 第 2 页'}).click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.getByRole('textbox', {name: '检索论文全文'}).fill('space complexity');
  await page.getByRole('button', {name: '检索', exact: true}).click();
  const complexityResult = page.locator('.result-card').filter({hasText: /space complexity/i}).first();
  await expect(complexityResult).toBeVisible();
  await complexityResult.click();
  await expect(page.getByText('第 3 / 5 页')).toBeVisible();
  await expect.poll(async () => {
    const mark = await page.getByTestId('evidence-highlight').boundingBox();
    const reader = await page.locator('.reader-scroll').boundingBox();
    return Boolean(mark && reader && mark.y >= reader.y && mark.y + mark.height <= reader.y + reader.height);
  }).toBe(true);
  await page.screenshot({path: 'test-results/real-pdf-evidence.png', fullPage: true});
});
