import {test, expect} from '@playwright/test';

test('real model deep challenge cites the paper and records usage', async ({page}) => {
  test.skip(process.env.RUN_REAL_DEEP !== '1', 'Requires live relay model and existing real review case');
  test.setTimeout(180000);
  await page.goto('/');
  await page.getByRole('button', {name: /Self-Attention with Relative Position Representations\.pdf 5 页/}).click();
  await page.getByRole('button', {name: '审稿分析'}).click();
  await page.getByRole('button', {name: '一审 · ec2b53cf'}).click();
  await expect(page.locator('.finding-card').first()).toContainText('空间复杂度');
  await expect(page.locator('.deep-verification > button')).toBeEnabled();
  if (await page.getByRole('button', {name: '扩展原文反证 · 消耗 Token', exact: true}).count()) {
    await page.getByRole('button', {name: '扩展原文反证 · 消耗 Token'}).click();
  }
  await expect(page.locator('.deep-verification-result strong')).toBeVisible({timeout: 180000});
  await expect(page.locator('.deep-verification-result')).toContainText('最强反证');
  await expect(page.locator('.deep-verification-result button').first()).toBeVisible();
  await expect(page.locator('.review-usage')).toContainText(/模型调用 [3-9] 次/);
  await page.locator('.deep-verification-result button').first().click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
});
