import {test, expect} from '@playwright/test';

test('model usage view shows recorded calls and unknown pricing', async ({page}) => {
  test.skip(process.env.RUN_USAGE_E2E !== '1', 'Requires a local dataset with prior model calls');
  await page.goto('/');
  await page.getByRole('button', {name: '模型用量'}).click();
  await expect(page.getByRole('heading', {name: '模型调用与成本'})).toBeVisible();
  await expect(page.getByText('已记录调用')).toBeVisible();
  await expect(page.getByText('费用未知调用')).toBeVisible();
  await expect(page.getByText(/已知费用合计：未配置/)).toBeVisible();
  await expect(page.locator('.usage-model-row').first()).toBeVisible();
});
