import {test, expect} from '@playwright/test';

test('a newly created review appears only once if it is already in the initial list', async ({page}) => {
  const review = {id: 'race-case-123', mode: 'first_review', original_document_id: 'paper', revised_document_id: null, response_document_id: null, latest_run: null, findings: [], model_calls: []};
  await page.route('**/api/documents', route => route.fulfill({json: [{id: 'paper', filename: 'paper.pdf', sha256: 'abcdef0123456789', snapshot_id: 'snapshot', page_count: 1, sections: [], pages: []}]}));
  await page.route('**/api/review-cases', async route => {
    if (route.request().method() === 'POST') {
      await route.fulfill({json: review});
    } else {
      await route.fulfill({json: [review]});
    }
  });
  await page.goto('/');
  await page.getByRole('button', {name: '审稿分析'}).click();
  await expect(page.locator('.review-history button')).toHaveCount(1);
  await page.getByRole('button', {name: '创建任务'}).click();
  await expect(page.locator('.review-history button.selected')).toHaveCount(1);
});
