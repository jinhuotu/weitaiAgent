// Run the real SFC setup with Vue reactivity; external services and lifecycle I/O are stubbed.
// This catches initialization-order failures that a production build cannot execute.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const path=require('node:path');
const frontendRoot=path.resolve(__dirname,'..');
const frontendRequire=createRequire(path.join(frontendRoot,'package.json'));
const vue=frontendRequire('vue');
const compiler=frontendRequire('@vue/compiler-sfc');
const ts=frontendRequire('typescript');
const filename=process.argv[2] || path.join(frontendRoot,'src/views/TendersView.vue');
const source=fs.readFileSync(filename,'utf8');
const hooks=[];
const apiCalls=[];
const auth=vue.reactive({isAdmin:true,menus:['/tender-library'],user:{id:1,username:'test'}});
const route=vue.reactive({path:'/tenders',query:{},params:{}});
const genericModule=new Proxy({}, {get:(_target,key)=>key==='__esModule'?true:key==='default'?{}:async()=>({})});
const modules={
 'vue':{...vue,onMounted:fn=>hooks.push(fn),onBeforeUnmount:()=>{}},
 'vue-router':{useRoute:()=>route,useRouter:()=>({push:()=>Promise.resolve(),replace:()=>Promise.resolve()})},
 '@/stores/auth':{useAuthStore:()=>auth},
 '@/lib/auth':{getAccessToken:()=> 'smoke-token'},
 './auth':{getAccessToken:()=> 'smoke-token'},
 './api':{apiRequest:async(...args)=>{apiCalls.push(args);return {}},apiDownload:async()=>{},apiFetchBlob:async()=>{}},
 '@/lib/api':{ApiError:class ApiError extends Error{}},
 '@/lib/read-file-smart':{fmtSize:n=>String(n),KB_UPLOAD_MAX_BYTES:40*1024*1024},
};
function loadTs(content,name){
 const output=ts.transpileModule(content,{fileName:name,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const mod={exports:{}};
 vm.runInNewContext(output,{exports:mod.exports,module:mod,require:requireMock,URLSearchParams,FormData,Blob,File:globalThis.File,console,setTimeout,clearTimeout,setInterval,clearInterval,window:{addEventListener(){},removeEventListener(){},location:{},localStorage:{getItem:()=>null}},document:{},localStorage:{getItem:()=>null,setItem(){},removeItem(){}}},{filename:name});
 return mod.exports;
}
function requireMock(name){return modules[name] || genericModule;}
modules['@/lib/tenders-api']=loadTs(fs.readFileSync(path.join(frontendRoot,'src/lib/tenders-api.ts'),'utf8'),'tenders-api.ts');
const descriptor=compiler.parse(source,{filename:'TendersView.vue'}).descriptor;
const script=compiler.compileScript(descriptor,{id:'tenders-view-runtime-check'});
const component=loadTs(script.content,'TendersView.setup.ts').default;
let bindings;
const scope=vue.effectScope();
try {
 bindings=scope.run(()=>component.setup({}, {expose(){}}));
 assert.ok(bindings.form,'setup must return a reactive form');
 assert.equal(bindings.form.bidderName,'');
 bindings.form.bidderName='Alpha Test Ltd';
 bindings.form.bidderAddress='A address';
 bindings.form.legalPersonName='A legal';
 bindings.form.legalPersonIdNo='A id';
 bindings.form.performanceLines=[{projectName:'A contract',bidderName:'Alpha Test Ltd'}];
 bindings.form.materialSelections={personnel:{enabled:true}};
 bindings.form.bidderName='Beta Test Ltd';
 assert.equal(bindings.form.bidderAddress,'');
 assert.equal(bindings.form.legalPersonName,'');
 assert.equal(bindings.form.legalPersonIdNo,'');
 assert.equal(bindings.form.performanceLines.length,0);
 assert.equal(Object.keys(bindings.form.materialSelections).length,0);
 bindings.form.bidderAddress='B address';
 bindings.form.bidderName=' Beta Test Ltd ';
 assert.equal(bindings.form.bidderAddress,'B address');
 console.log('PASS: actual TendersView setup completes; company changes still clear prior company data');
} finally { scope.stop(); }

