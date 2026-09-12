// 仅使用视觉夹具：拦截全部 API 流量，不调用提供商，也不读取用户数据。
const {chromium}=require('playwright');const fs=require('node:fs/promises');const path=require('node:path');
(async()=>{
 const output=path.resolve(process.argv[2]||'.local-plans/phase2-review/themes');await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});const results=[];
 for(const theme of ['light','dark','sepia','paper-moments','ocean-blue','midnight-purple']){
  const page=await browser.newPage({viewport:{width:1280,height:1400}});let populated=false;const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const run=(id,status,progress)=>({run_id:id,kind:'rag',dataset_id:'rag-demo-v1',status,progress,error_code:status==='failed'?'BENCHMARK_RUN_FAILED':null,config_snapshot:{}});
  await page.route('**/api/**',async route=>{
   const request=route.request();const url=new URL(request.url());let body={items:[]};
   if(url.pathname==='/api/benchmarks/datasets')body={items:[{dataset_id:'rag-demo-v1',description:'视觉测试数据集',case_count:24}]};
   else if(url.pathname==='/api/providers')body={items:[]};
   else if(url.pathname==='/api/benchmarks/rag/runs'&&request.method()==='POST'){populated=true;body=run('visual-completed','completed',1)}
   else if(url.pathname==='/api/benchmarks/runs')body={items:populated?[run('visual-completed','completed',1),run('visual-running','running',.5),run('visual-failed','failed',.25)]:[]};
   else if(url.pathname.endsWith('/report'))body={metrics:{fts:{total_cases:24,hit_at_1:.875,hit_at_5:1,mrr:.9235,citation_hit_rate:.75,p50_latency_ms:12.43,p95_latency_ms:22.16,failed_cases:0}},cases:[],config_snapshot:{fixture:true}};
   else if(url.pathname==='/status')body={status:'ready'};
   await route.fulfill({json:body});
  });
  await page.goto('http://127.0.0.1:5189/#/benchmarks');await page.getByText('还没有评测记录',{exact:true}).waitFor();
  await page.evaluate(async id=>{const{useThemeStore}=await import('/src/stores/theme.ts');const store=useThemeStore();if(!['light','dark','sepia'].includes(id))await store.installCommunityTheme(id);if(!store.applyTheme(id,{persist:false}))throw Error('theme failed')},theme);
  await page.screenshot({path:path.join(output,`${theme}-empty.png`)});
  await page.getByRole('button',{name:'运行评测',exact:true}).click();
  await page.getByRole('button',{name:'查看报告',exact:true}).first().waitFor();await page.getByRole('button',{name:'查看报告',exact:true}).first().click();
  await page.getByText('87.5%',{exact:true}).waitFor();
  await page.locator('#benchmark-topk').focus();
  await page.screenshot({path:path.join(output,`${theme}-report.png`)});
  const colors=await page.evaluate(()=>{const read=selector=>{const s=getComputedStyle(document.querySelector(selector));return {background:s.backgroundColor,color:s.color,border:s.borderColor}};return {panel:read('.benchmark-config'),input:read('#benchmark-topk'),button:read('.benchmark-config .button-primary'),metric:read('.metric-card')}});
  await page.setViewportSize({width:390,height:1100});await page.screenshot({path:path.join(output,`${theme}-narrow.png`)});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  await page.locator('.benchmark-report').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,`${theme}-narrow-report.png`)});
  if(errors.length||overflow)throw Error(JSON.stringify({theme,errors,overflow}));results.push({theme,errors,overflow,colors});await page.close();
 }
 await fs.writeFile(path.join(output,'results.json'),JSON.stringify(results,null,2));await browser.close();console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exit(1)});
