import {test, expect} from '@playwright/test';

const realPdf = process.env.REAL_PDF_PATH || 'tests/fixtures/Self-Attention with Relative Position Representations.pdf';

test('real AI answer streams, cites the paper and survives refresh', async ({page}) => {
  test.skip(process.env.RUN_REAL_AI !== '1', 'Requires a configured live model endpoint');
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(realPdf);
  await page.getByRole('button', {name: '论文对话'}).click();
  await page.getByRole('textbox', {name: '向论文提问'}).fill('What is the central contribution? Answer briefly with a source citation.');
  await page.getByRole('button', {name: '发送'}).click();
  await expect(page.locator('.message.assistant')).toBeVisible({timeout: 120000});
  await expect(page.locator('.citation-list button').first()).toBeVisible({timeout: 120000});
  const conversationId = await page.locator('.conversation-list button.selected').getAttribute('data-conversation-id');
  expect(conversationId).toBeTruthy();
  await page.locator('.citation-list button').first().click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.getByRole('textbox', {name: '向论文提问'}).fill('Which translation results support that claim? Cite the table.');
  await page.getByRole('button', {name: '发送'}).click();
  await expect(page.locator('.message.assistant')).toHaveCount(2, {timeout: 120000});
  await expect(page.locator('.message.assistant .message-role').last()).toHaveText('论文助手', {timeout: 120000});
  await page.getByRole('textbox', {name: '向论文提问'}).fill('Explain Equation (4) from the paper. Show the equation in display LaTeX using $$ delimiters, not a code block, and cite the source.');
  await page.getByRole('button', {name: '发送'}).click();
  await expect(page.locator('.message.assistant')).toHaveCount(3, {timeout: 120000});
  await expect(page.locator('.message.assistant .message-role').last()).toHaveText('论文助手', {timeout: 120000});
  await expect(page.locator('.message.assistant').last().locator('.katex').first()).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('回答已保存');
  await page.reload();
  await page.getByRole('button', {name: '论文对话'}).click();
  await expect(page.locator('.conversation-list button').first()).toBeVisible();
  await page.locator(`.conversation-list button[data-conversation-id="${conversationId}"]`).click();
  await expect(page.locator('.message.assistant')).toHaveCount(3);
});
