const {chromium}=require('playwright');const fs=require('node:fs/promises');const path=require('node:path');
(async()=>{
 if(!process.argv.includes('--execute')&&!process.argv.includes('--reuse'))throw Error('--execute required; four real Agent cases use existing quota');
 const output=path.resolve('.local-plans/phase2-completion/browser');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1300,height:1000}});
 await page.goto('http://127.0.0.1:5187/#/benchmarks');
 await page.getByLabel(/^类型/).selectOption('agent');
 await page.getByLabel(/^数据集/).selectOption('agent-core-v1');
 if(!process.argv.includes('--reuse')) await page.getByRole('button',{name:'运行评测',exact:true}).click();
 const row=page.locator('tbody tr').filter({hasText:'agent-core-v1'}).first();
 await row.getByRole('button',{name:'查看报告'}).waitFor({timeout:180000});
 await row.getByRole('button',{name:'查看报告'}).click();
 await page.getByRole('heading',{name:'评测报告'}).waitFor();
 await page.screenshot({path:path.join(output,'benchmark-report.png'),fullPage:true});
 const pending=page.waitForEvent('download');await page.getByRole('button',{name:'下载完整 JSON'}).click();
 await(await pending).saveAs(path.join(output,'agent-ui-report.json'));
 await row.getByRole('link',{name:'Agent Trace'}).click();
 await page.waitForSelector('.agent-page .trace-visualization',{timeout:20000});
 await page.screenshot({path:path.join(output,'benchmark-trace.png'),fullPage:true});
 await browser.close();console.log('Benchmark UI start/report/download/Trace completed');
})().catch(e=>{console.error(e);process.exit(1)})
