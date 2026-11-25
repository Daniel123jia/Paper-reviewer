import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [base,pdf,out]=process.argv.slice(2);
assert.equal(new URL(base).hostname,'127.0.0.1');
const browser=await chromium.launch();
try {
  const page=await browser.newPage({viewport:{width:1360,height:1000}});
  await page.route(/^https?:\/\//,route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  await page.goto(base);
  await page.getByRole('button',{name:'全文审稿',exact:true}).click();
  await page.locator('.compact-picker input').setInputFiles(pdf);
  const start=page.getByRole('button',{name:'确认费用并开始审稿'});
  await expect(start).toBeVisible();
  await page.screenshot({path:path.join(out,'01-start.png')});
  await start.click();
  await expect(page.getByRole('heading',{name:'中文学术审稿报告 · internal DRAFT'})).toBeVisible({timeout:45000});
  await page.screenshot({path:path.join(out,'02-review.png'),fullPage:true});
  await page.locator('button.compact-ref').first().click();
  await expect(page.getByTestId('evidence-highlight')).toBeVisible();
  await page.screenshot({path:path.join(out,'03-evidence.png')});
  const url=base+'/api/documents/a996da85e0abf6bb1a16cebbb82137af5ca8c16cadc81bc41358829a3687a6cf/compact-review';
  const job=await (await page.request.get(url)).json();
  assert.equal(job.status,'completed'); assert.equal(job.usage.request_count,2);
  assert.equal(job.usage.unknown_usage_calls,0); assert.equal(job.usage.calls[0].actual_model,'offline-fixture');
  await fs.writeFile(path.join(out,'review-and-audit.json'),JSON.stringify(job,null,2));
  await page.getByRole('button',{name:'关闭原文'}).click();
  for(const [name,ext] of [['导出 Markdown','md'],['导出 PDF','pdf']]){
    const download=page.waitForEvent('download');
    await page.getByRole('link',{name}).click();
    const file=path.join(out,'Kim2014-FakeSDK-DRAFT.'+ext);
    await (await download).saveAs(file);
    if(ext==='md')assert.equal(await fs.readFile(file,'utf8'),job.body);
    else assert.equal((await fs.readFile(file)).subarray(0,5).toString(),'%PDF-');
  }
  await page.reload();
  await expect(page.getByRole('heading',{name:'综合审稿意见'})).toBeVisible();
  console.log('PASS: upload, two-pass review, PDF evidence highlight, MD/PDF downloads, reload.');
} finally {await browser.close();}
