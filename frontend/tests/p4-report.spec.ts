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
  const firstConfirmation = page.getByRole('checkbox', {name: /我已审核当前确切版本的正文/});
  await firstConfirmation.check();
  const editor = page.getByLabel('P4 报告正文');
  await expect(editor).toHaveValue(/Scope of the method claim/);
  await editor.fill((await editor.inputValue()) + '\nThe wording remains a request for clarification.\n');
  await page.getByRole('button', {name: '保存当前编辑'}).click();
  await expect(page.getByText('修改已保存；此版本需要重新审核')).toBeVisible();
  await expect(firstConfirmation).not.toBeChecked();
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

test('switching projects clears unsubmitted approval and review forms', async ({page}) => {
  const projectsResponse = await page.request.get('http://127.0.0.1:8000/api/documents/doc1/review-report-projects');
  const projects = await projectsResponse.json() as {id: string}[];
  expect(projects).toHaveLength(2);
  const [projectB, projectA] = projects;
  const projectBResponse = await page.request.get(`http://127.0.0.1:8000/api/review-report-projects/${projectB.id}`);
  const projectBView = await projectBResponse.json();
  const projectBDraftId = projectBView.drafts[0].id as string;
  const projectAView = await (await page.request.get(
    `http://127.0.0.1:8000/api/review-report-projects/${projectA.id}`)).json();
  expect(projectAView.issues[0].consolidated_issue_id).toBe(projectBView.issues[0].consolidated_issue_id);

  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', {name: '审稿文档'}).click();
  const projectPicker = page.getByLabel('已有 P4 报告项目');
  await expect(projectPicker).not.toHaveValue('');
  await projectPicker.selectOption(projectA.id);
  await expect(page.locator('.draft-preview .markdown-body')).toContainText('Scope of the method claim');
  const confirmation = page.getByRole('checkbox', {name: /我已审核当前确切版本的正文/});
  await confirmation.check();
  await expect(page.getByRole('button', {name: '批准当前版本'})).toBeEnabled();

  await page.getByText('技术核验与人工复核').click();
  const issue = page.locator('.p4-issue').first();
  const rationale = issue.getByLabel(/审核理由/);
  const order = issue.getByLabel(/报告顺序/);
  await rationale.fill('Only project A reviewed this wording.');
  await order.fill('7');
  await issue.getByText(/人工复核 reading_unit/).click();
  await issue.getByLabel('ru1 行列').fill('Only project A checked these rows.');
  await page.getByText('新增人工审稿意见').click();
  await page.getByLabel('人工科学问题').fill('Only project A drafted this question.');
  await page.getByLabel('人工证据依据').fill('Only project A inspected this source.');
  await page.getByLabel('人工来源 ID').fill('ru1');
  await page.getByLabel('P4 报告类型').selectOption('internal');
  await page.getByLabel('P4 报告语言').selectOption('en');

  const loadedB = page.waitForResponse(response => response.url().endsWith(`/api/review-report-drafts/${projectBDraftId}`));
  await projectPicker.selectOption(projectB.id);
  await loadedB;
  await expect(page.locator('.draft-preview .markdown-body')).toContainText('Scope of the method claim');
  await expect(confirmation).not.toBeChecked();
  await expect(page.getByRole('button', {name: '批准当前版本'})).toBeDisabled();
  await expect(rationale).toHaveValue('');
  await expect(order).toHaveValue('0');
  await expect(issue.getByLabel('ru1 行列')).toHaveValue('');
  await expect(page.getByLabel('人工科学问题')).toHaveValue('');
  await expect(page.getByLabel('人工证据依据')).toHaveValue('');
  await expect(page.getByLabel('人工来源 ID')).toHaveValue('');
  await expect(page.getByLabel('P4 报告类型')).toHaveValue('author');
  await expect(page.getByLabel('P4 报告语言')).toHaveValue('zh');
  await confirmation.scrollIntoViewIfNeeded();
  await page.screenshot({path: 'test-results/p4-project-switch.png'});

  await confirmation.check();
  await page.getByRole('button', {name: /author · en · 修订/}).click();
  await expect(confirmation).not.toBeChecked();
  await confirmation.check();
  await page.getByRole('button', {name: '批准当前版本'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  const currentB = await page.request.get(`http://127.0.0.1:8000/api/review-report-drafts/${projectBDraftId}`);
  expect((await currentB.json()).approval_status).toBe('pending');

  await page.getByRole('button', {name: '从所选 P3 运行建立新报告项目'}).click();
  await expect(page.getByText('当前项目尚无已保存报告。可在下方选择报告类型并明确生成新草稿。')).toBeVisible();
  await expect(page.locator('.draft-editor')).toHaveCount(0);
});
