import {test, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test('P4 report opens saved draft, locates PDF evidence, edits, reloads and downloads', async ({page}) => {
  test.setTimeout(90000);
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', {name: '审稿文档'}).click();
  await expect(page.getByRole('heading', {name: '从已核验的问题形成审稿意见'})).toBeVisible();
  await expect(page.getByLabel('已有 P4 报告项目')).not.toHaveValue('');
  await expect(page.getByRole('heading', {name: '报告正文与导出'})).toBeVisible();
  await expect(page.locator('.draft-preview .markdown-body')).toContainText('Scope of the method claim');
  await expect(page.getByText('技术核验与人工复核')).toBeVisible();
  await page.getByText('技术核验与人工复核').click();
  await expect(page.locator('.p4-issue').first()).toBeVisible();
  await page.getByRole('button', {name: /原文第 1 页/}).first().click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.getByRole('button', {name: /author · en · 修订 1/}).click();
  await page.getByText('编辑正文').click();
  const editor = page.getByLabel('P4 报告正文');
  await expect(editor).toHaveValue(/Scope of the method claim/);
  await editor.fill((await editor.inputValue()) + '\nThe wording remains a request for clarification.\n');
  await page.getByRole('button', {name: '保存当前编辑'}).click();
  await expect(page.getByText('修改已保存；此版本需要重新审核')).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name: '审稿文档'}).click();
  await page.getByRole('button', {name: /author · en · 修订 2/}).click();
  await page.getByText('编辑正文').click();
  await expect(page.getByLabel('P4 报告正文')).toHaveValue(/The wording remains/);
  await expect(page.getByText('待审核草稿（带 Draft 标识）：')).toBeVisible();
  const markdownDownload = page.waitForEvent('download');
  await page.getByRole('link', {name: 'MD'}).click();
  const markdown = await markdownDownload;
  const markdownBytes = await readFile(await markdown.path());
  expect(markdownBytes.toString('utf8')).toContain('DRAFT — NOT APPROVED');
  const pdfDownload = page.waitForEvent('download');
  await page.getByRole('link', {name: 'PDF'}).click();
  const pdf = await pdfDownload;
  expect((await readFile(await pdf.path())).subarray(0, 5).toString()).toBe('%PDF-');
  await page.getByText('技术核验与人工复核').click();
  await page.getByText(/CoverageGap 原始审计/).click();
  await expect(page.locator('.p4-coverage li').first()).toBeVisible();
  await page.getByText('新增人工审稿意见').click();
  await page.getByLabel('人工科学问题').fill('Could the method scope be specified?');
  await page.getByLabel('人工证据依据').fill('Reviewer inspected the cited PDF source.');
  await page.getByLabel('人工来源 ID').fill('ru1');
  await page.getByRole('button', {name: '保存人工问题'}).dblclick();
  await expect(page.locator('.p4-issue').filter({hasText: '人工补充 · Could the method scope be specified?'})).toHaveCount(1);
  const projectId = await page.getByLabel('已有 P4 报告项目').inputValue();
  const retry = await page.request.post(`http://127.0.0.1:8000/api/review-report-projects/${projectId}/reviewer-issues`, {
    data: {scientific_question: 'Could the method scope be specified?',
      evidence_basis: 'Reviewer inspected the cited PDF source.', source_refs: ['ru1'],
      comment_body: '', category: 'clarification'},
  });
  expect(retry.status()).toBe(200);
  await page.reload();
  await page.getByRole('button', {name: '审稿文档'}).click();
  await page.getByText('技术核验与人工复核').click();
  const manual = page.locator('.p4-issue').filter({hasText: '人工补充 · Could the method scope be specified?'});
  await expect(manual).toHaveCount(1);
  await manual.getByRole('button', {name: /原文第 1 页/}).click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
});
