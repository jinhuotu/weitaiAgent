import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import ts from 'file:///E:/code/python/weitaiAgentui/node_modules/typescript/lib/typescript.js'
const source=fs.readFileSync('E:/code/python/weitaiAgentui/src/lib/tender-package-wizard.ts','utf8')
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const exports={};vm.runInNewContext(code,{exports,File,structuredClone})
const {validate,initialValue,mapArguments}=exports
let checks=0
function check(test){assert.ok(test);checks++}
const schema={type:'object',required:['decision','amount','basis','files'],properties:{
 decision:{type:'string',enum:['yes','no'],title:'结论'},amount:{type:'number',minimum:0,maximum:1000,title:'金额'},
 basis:{type:'string',minLength:1,pattern:'\\S',title:'依据'},files:{type:'array',minItems:1,uniqueItems:true,items:{type:'string',enum:['a','b']},title:'资料'}}}
check(validate(schema,{}).length===4)
check(validate(schema,{decision:'yes',amount:0,basis:'原文依据',files:['a']}).length===0)
const invalid=validate(schema,{decision:'other',amount:-1,basis:' ',files:['a','a']})
check(['结论','金额','依据','资料'].every(label=>invalid.some(message=>message.includes(label))))
check(validate({type:'number'},Infinity).length===1)
check(validate({type:'integer'},1.2).length===1)
check(validate({type:'boolean',const:true},false).length===1)
check(validate({type:'string',format:'binary'},new File(['fixture'],'fixture.docx')).length===0)
check(validate({type:'string',format:'binary'},'file-path').length===1)
check(initialValue({type:'string',enum:['first','second']})===undefined)
check(initialValue({type:'number',minimum:0})===undefined)
check(JSON.stringify(mapArguments({operation:'review',arguments:{amount:{$answer:'amount'},decisions:[{value:{$answer:'decision'}}]}},{amount:0,decision:'no'}))===JSON.stringify({operation:'review',arguments:{amount:0,decisions:[{value:'no'}]}}))
assert.throws(()=>mapArguments({value:{$answer:'missing'}},{}));checks++
check(validate({type:'array',minItems:1,items:{type:'object',required:['code'],properties:{code:{type:'string',pattern:'^[a-z]+$',title:'编号'}}}},[{}]).length===1)
console.log(JSON.stringify({checks,passed:true,scope:'only new wizard form validation and declarative argument mapping'}))
