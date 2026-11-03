import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('opens chat for a real PDF and explains missing server key', async ({page}) => {
  test.skip(process.env.TEST_MISSING_KEY !== '1', 'Only run against an unconfigured local backend');
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '论文对话'}).click();
  await page.getByRole('textbox', {name: '向论文提问'}).fill('What is the main contribution?');
  await page.getByRole('button', {name: '发送'}).click();
  await expect(page.getByRole('alert')).toContainText('OPENAI_API_KEY');
});
