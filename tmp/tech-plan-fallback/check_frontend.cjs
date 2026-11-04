const fs = require('node:fs')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const req = createRequire('E:/code/python/weitaiAgentui/package.json')
const ts = req('typescript')
const source = fs.readFileSync('E:/code/python/weitaiAgentui/src/lib/api.ts', 'utf8')
const fn = source.slice(source.indexOf('function friendlyMessage('), source.indexOf('function envelopeMessage('))
const js = ts.transpileModule(fn, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const context = vm.createContext({})
vm.runInContext(js, context)
for (const message of ['技术方案缺少章节列表', '技术方案未返回任何既有章节正文', '技术方案未完整返回既有章节正文：质量保证']) {
  const hint = context.friendlyMessage(422, message)
  assert.ok(hint.includes('【待补充】') && hint.includes('重新生成投标文件'))
}
assert.ok(context.friendlyMessage(422, '商务响应缺少条款或章节').includes('商务响应暂未完整生成'))
assert.equal(context.friendlyMessage(422, '项目名称不能为空'), '项目名称不能为空')
assert.equal(context.friendlyMessage(500), '服务暂时不可用，请稍后重试')
console.log('All three technical fallback hints verified; business fallback and unrelated errors preserved.')
