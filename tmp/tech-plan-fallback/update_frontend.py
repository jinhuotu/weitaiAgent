from pathlib import Path

target = Path('E:/code/python/weitaiAgentui/src/lib/api.ts')
content = target.read_text(encoding='utf-8')
old = """    return detail || '提交内容未通过校验，请检查必填项'
"""
new = """    if (
      detail === '技术方案缺少章节列表' ||
      detail === '技术方案未返回任何既有章节正文' ||
      detail.startsWith('技术方案未完整返回既有章节正文')
    ) {
      return '技术方案暂未完整生成。可先关闭「起草技术方案」后重新生成投标文件，缺失章节请按【待补充】人工完善。'
    }
    return detail || '提交内容未通过校验，请检查必填项'
"""
assert content.count(old) == 1
target.write_text(content.replace(old, new), encoding='utf-8', newline='\n')
print('Updated technical-plan fallback hint; business fallback and other validation messages preserved.')
