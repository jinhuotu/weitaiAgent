// Exercise the actual library SFC and API adapter with Vue reactivity.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const frontend = 'E:/code/python/weitaiAgentui';
const req = createRequire(path.join(frontend, 'package.json'));
const vue = req('vue'), compiler = req('@vue/compiler-sfc'), ts = req('typescript');
const calls = [];
const virtual = {key:'material_unseen', title:'待上传凭证', fileCount:0, files:[], docId:''};
const real = {key:'license', parentKey:'moriginal', title:'营业执照', fileCount:1, files:[{fileId:'original'}], docId:'parent'};
const generic = new Proxy({}, {get:(_t,key)=>key==='__esModule'?true:key==='default'?{}:async()=>({})});
const modules = {
  vue:{...vue,onMounted:()=>{}},
  'vue-router':{useRouter:()=>({push:()=>Promise.resolve()})},
  '@/lib/auth':{getAccessToken:()=> 'test'},
  './auth':{getAccessToken:()=> 'test'},
  '@/lib/api':{ApiError:class extends Error{}},
  './api':{apiRequest:async(url, options)=>{calls.push({url,...options});return url.endsWith('/library') ? {slots:[real]} : {}; }},
};
function load(content, name) {
  const js = ts.transpileModule(content, {fileName:name, compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const module = {exports:{}};
  vm.runInNewContext(js, {module,exports:module.exports,require:name=>modules[name]||generic,
    console,URLSearchParams,FormData,Blob,File:globalThis.File,
    localStorage:{getItem:()=>null,setItem(){}},window:{},document:{}}, {filename:name});
  return module.exports;
}
modules['@/lib/tenders-api'] = load(fs.readFileSync(path.join(frontend,'src/lib/tenders-api.ts'),'utf8'),'tenders-api.ts');
const descriptor = compiler.parse(fs.readFileSync(path.join(frontend,'src/views/TenderLibraryView.vue'),'utf8')).descriptor;
const compiled = compiler.compileScript(descriptor, {id:'library-test'});
const component = load(compiled.content,'TenderLibraryView.ts').default;
(async()=>{
  const scope = vue.effectScope();
  try {
    const ui = scope.run(()=>component.setup({}, {expose(){}}));
    ui.slots.value = [virtual, real];
    assert.equal(ui.isVirtualSlot(virtual), true);
    assert.equal(ui.isVirtualSlot(real), false);
    ui.askDeleteItem(virtual);
    await ui.confirmDeleteItem();
    assert.equal(calls[0].url, '/api/v1/tenders/library/dismissed');
    assert.equal(calls[0].method, 'POST');
    assert.equal(calls[0].body.key, virtual.key);
    assert.equal(ui.slots.value.length,1);
    ui.askDeleteItem(real);
    await ui.confirmDeleteItem();
    assert.ok(calls.some(c=>c.url.endsWith('/library/items/moriginal') && c.method==='DELETE'));
    assert.ok(!calls.some(c=>c.url.endsWith('/library/items/license')));
    assert.equal(ui.error.value,'');
    console.log('PASS: actual library setup, virtual POST dismissal, real original-parent DELETE, refresh');
  } finally {scope.stop();}
})().catch(error=>{console.error(error);process.exitCode=1;});
