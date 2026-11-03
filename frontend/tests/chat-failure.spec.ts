import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('network failure does not leave an unsaved answer in chat', async ({page}) => {
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '论文对话'}).click();
  await page.route('**/api/conversations/*/messages/stream', route => route.abort());
  await page.getByRole('textbox', {name: '向论文提问'}).fill('What is the paper about?');
  await page.getByRole('button', {name: '发送'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('回答未完成，请重试');
  await expect(page.locator('.message')).toHaveCount(0);
});
