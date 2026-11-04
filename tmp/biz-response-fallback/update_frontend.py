from pathlib import Path

target = Path('E:/code/python/weitaiAgentui/src/lib/api.ts')
content = target.read_text(encoding='utf-8')
old = """  if (status === 422) {
    return detail || '提交内容未通过校验，请检查必填项'
  }"""
new = """  if (status === 422) {
    if (detail === '商务响应缺少条款或章节') {
      return '商务响应暂未完整生成。可先关闭「起草商务响应」后重新生成投标文件，缺失内容请按【待补充】人工完善。'
    }
    return detail || '提交内容未通过校验，请检查必填项'
  }"""
assert content.count(old) == 1
target.write_text(content.replace(old, new), encoding='utf-8', newline='\n')
print('Updated business-response fallback hint; other 422 messages preserved.')
