// 使用项目已有 TypeScript 转译器，不依赖额外测试库。
const ts = require('typescript')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
function load(relative, imports = {}) {
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText
  const exports = {}
  new Function('exports', 'require', code)(exports, name => {
    if (name in imports) return imports[name]
    throw new Error(`Unexpected import: ${name}`)
  })
  return exports
}
const { sourceLink, researchReference } = load('src/utils/source-link.ts')
const kb = '11111111-1111-4111-8111-111111111111'
const doc = '22222222-2222-4222-8222-222222222222'
assert.equal(sourceLink(`local://kb/${kb}/${doc}`), `/knowledge?kb_id=${kb}&doc_id=${doc}`)
assert.equal(sourceLink('javascript:alert(1)'), undefined)
assert.equal(sourceLink('local://kb/invalid/id'), undefined)
assert.equal(sourceLink('https://example.com/report'), 'https://example.com/report')
assert.equal(researchReference({link:`local://kb/${kb}/${doc}`,source:'knowledge'},0).source,'knowledge')
assert.equal(researchReference({link:`local://kb/${kb}/${doc}`},0).link,`local://kb/${kb}/${doc}`)
assert.equal(researchReference({url:`local://kb/${kb}/${doc}`},0).source,'knowledge')
let onResponse
class ResponseError extends Error {}
load('src/api/request/plugins/service.ts', { '../error': { ResponseError } }).servicePlugin.install({
  interceptors: { response: { use: success => { onResponse = success } } },
})
async function main() {
  for (const status of ['pending', 'processing', 'completed', 'failed']) {
    const response = { data: { id: 'document', status } }
    assert.equal(await onResponse(response), response)
  }
  await assert.rejects(onResponse({ data: { status: 'error', message: '真实接口错误' } }), /真实接口错误/)
  console.log('PASS source links, restored references and attachment lifecycle responses (12 assertions)')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
