const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../frontend/analyses.js'), 'utf8'), context);
const records = {messages: [
  {id:'q', role:'user', content:'请分析当前辨识结果的误差，并给出有依据的修改计划。'},
  {id:'a', role:'assistant', content:'这是一条没有分析标记的旧消息'},
  {id:'q2', role:'user', content:'任意表述'},
  {id:'a2', role:'assistant', content:'明确保存的结果分析', response:{analysis:{request_id:'q2', method:'pann'}}},
]};
assert.equal(context.findHistoricalAnalyses(records).length, 1);
assert.equal(context.findHistoricalAnalyses(records)[0].id, 'a2');
const corpus = context.conversationMessages(records);
assert.equal(corpus.length, 4);
assert.equal(corpus[1].analysis, undefined);
assert.equal(corpus[3].analysis.kind, 'identification_analysis');
console.log('PASS: all messages retained; analysis filters use stored metadata only');
