import {chromium} from '@playwright/test';
import {pathToFileURL} from 'node:url';

const [htmlPath, pdfPath, kind, language] = process.argv.slice(2);
if (!htmlPath || !pdfPath || !['report', 'opinion'].includes(kind) || !['zh', 'en'].includes(language)) {
  throw new Error('Usage: node render_pdf.mjs input.html output.pdf report|opinion zh|en');
}

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  await page.route('**/*', route => {
    const protocol = new URL(route.request().url()).protocol;
    return ['file:', 'data:', 'about:'].includes(protocol) ? route.continue() : route.abort();
  });
  await page.goto(pathToFileURL(htmlPath).href, {waitUntil: 'load'});
  const font = language === 'zh'
    ? '"Source Han Sans SC", "Arial Unicode MS", "Hiragino Sans GB", sans-serif'
    : '"Times New Roman", Georgia, serif';
  const margin = kind === 'report' ? '22mm' : '25mm';
  await page.addStyleTag({content: `
    @page { size: A4; }
    html, body { margin: 0; padding: 0; }
    body { font-family: ${font}; color: #233944; font-size: ${kind === 'report' ? '10.5pt' : '11pt'};
      line-height: 1.55; overflow-wrap: anywhere; }
    h1 { font-size: ${kind === 'report' ? '19pt' : '17pt'}; color: #233e54; text-align: center;
      margin: 0 0 18pt; font-weight: 700; }
    h2 { font-size: 13pt; color: #284b62; border-bottom: 0.5pt solid #b9ccd3;
      padding-bottom: 4pt; margin: 16pt 0 7pt; }
    h3 { font-size: 11.5pt; color: #35546a; margin: 12pt 0 5pt; }
    h1, h2, h3 { break-after: avoid; }
    p { margin: 0 0 7pt; orphans: 2; widows: 2; }
    table { border-collapse: collapse; width: 100%; margin: 10pt 0; font-size: 9.2pt; break-inside: avoid; }
    th, td { border-bottom: 0.5pt solid #bacbd1; padding: 5pt 4pt; text-align: left; }
    th { border-top: 1pt solid #557380; color: #233e54; }
    tr:last-child td { border-bottom: 1pt solid #557380; }
    thead { display: table-header-group; }
    ul, ol { padding-left: 1.5em; }
    li { margin-bottom: 4pt; }
    code { font-family: Menlo, monospace; font-size: 9pt; }
    math[display="block"] { display: block; text-align: center; margin: 10pt 0; overflow: visible; }
    blockquote { margin: 8pt 0; padding: 4pt 10pt; border-left: 2pt solid #6e9da7; background: #f4f8f9; }
    a { color: #286b6d; text-decoration: none; }
  `});
  await page.pdf({path: pdfPath, format: 'A4', printBackground: true,
    margin: {top: margin, bottom: margin, left: margin, right: margin},
    displayHeaderFooter: true,
    headerTemplate: `<div style="font-family:${font};font-size:8pt;color:#70878c;width:100%;text-align:right;margin-right:${margin}">${kind === 'report' ? (language === 'zh' ? '内部完整审稿报告' : 'Internal Peer Review Report') : (language === 'zh' ? '正式审稿意见' : 'Formal Review Comments')}</div>`,
    footerTemplate: '<div style="font-size:8pt;color:#70878c;width:100%;text-align:center"><span class="pageNumber"></span></div>'});
} finally {
  await browser.close();
}
