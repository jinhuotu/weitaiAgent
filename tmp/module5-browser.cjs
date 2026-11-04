const fs=require('node:fs');
const {chromium}=require('C:/Users/mouzh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const api=JSON.parse(fs.readFileSync('tmp/module5-smoke-ready.json')).url;
 const ui=JSON.parse(fs.readFileSync('tmp/module5-ui-ready.json')).url;
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{if(window===window.top)localStorage.setItem('youqi_access_token','isolated-verification-token')});
 await page.route('**/api/v1/**',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.pathname.startsWith('/api/v1/tender-package'))await route.continue();
   else if(url.pathname==='/api/v1/auth/me')await route.fulfill({json:{code:0,msg:'ok',data:{id:987654,username:'isolated-page-test',roles:[],menus:['/','/tenders'],is_superuser:false}}});
   else await route.fulfill({json:{code:0,msg:'ok',data:[]}});
 });
 await page.goto(ui+'/tender-package');await page.getByRole('heading',{name:'压缩包标书',exact:true}).waitFor();
 await page.locator('input[type=file]').first().setInputFiles('tmp/module5-upload.zip');
 try{await page.getByRole('button',{name:'安全解包',exact:true}).waitFor()}catch(e){console.log((await page.locator('body').innerText()).slice(-5000));console.log(errors);throw e}
 await page.getByRole('button',{name:'安全解包',exact:true}).click();
 await page.locator('.package-tree').first().waitFor();
 await page.getByRole('button',{name:'识别角色与物料'}).click();
 await page.getByRole('heading',{name:'角色与物料核验'}).waitFor();
 await page.getByPlaceholder('原文、报告或人工书面依据').fill('仅隔离合成样本页面核验');
 const roleSelect=page.locator('tbody select').last();await roleSelect.selectOption('tech_spec');
 await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('汇聚 SSOT')).disabled);
 await page.getByRole('button',{name:'汇聚 SSOT',exact:true}).click();
 await page.getByRole('heading',{name:'正式人工核验与生成'}).waitFor();
 await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('按已确认数据生成草稿')).disabled);
 const review=page.frameLocator('iframe[title="正式压缩包标书人工核验"]');
 try{await review.locator('#summary').filter({hasText:'待核对 4 项'}).waitFor()}catch(e){console.log('FRAME',await review.locator('body').innerText());console.log('ERRORS',errors);throw e}
 await review.locator('#actor').fill('测试身份（服务器应覆盖）');await review.locator('#basis').fill('隔离测试参数来源');
 await review.locator('#rows input[type=checkbox]').first().check();
 await review.locator('#rows td:nth-child(4) textarea').first().fill('YJV22');
 await review.locator('#rows td:nth-child(5) select').first().selectOption('响应');
 await review.locator('#saveOffers').click();await review.locator('#summary').filter({hasText:'待核对 3 项'}).waitFor();
 await page.getByRole('button',{name:'按已确认数据生成草稿'}).click();
 await page.getByRole('heading',{name:'按标包 / 固化ID查看产物'}).waitFor();
 await page.screenshot({path:'tmp/module5-formal-page.png',fullPage:false});
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:/下载 .*technical/}).first().click()]);
 await download.saveAs('tmp/module5-downloaded-tech.docx');
 await page.reload();await page.frameLocator('iframe').locator('#summary').filter({hasText:'待核对 3 项'}).waitFor();
 if(errors.length)throw Error(errors.join(';'));
 console.log(JSON.stringify({route:true,upload:true,nested_tree:true,role_correction:true,ssot:true,authenticated_review_bridge:true,manual_fill:true,generation:true,download:true,reload_persistence:true,visual_status:'not_accepted',page_errors:errors}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

