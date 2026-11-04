from pathlib import Path
import difflib

root = Path(__file__).resolve().parent
target = Path('E:/code/python/weitaiAgentui/src/lib/api.ts')
before = target.read_text(encoding='utf-8')
old = "  // 优先展示后端具体错误（如 MCP stdio 失败原因）\n"
new = """  if (status >= 500 && /^(internal server error|bad gateway|service unavailable|gateway timeout)$/i.test(detail)) {
    return '服务处理请求失败，请稍后重试；若持续出现，请联系管理员。'
  }
  // 优先展示后端具体错误（如 MCP stdio 失败原因）
"""
assert before.count(old) == 1
after = before.replace(old, new, 1)
(root / 'api.before.ts').write_text(before, encoding='utf-8', newline='\n')
(root / 'frontend.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
    fromfile='a/src/lib/api.ts', tofile='b/src/lib/api.ts')), encoding='utf-8', newline='\n')
target.write_text(after, encoding='utf-8', newline='\n')
print('Generic server status messages are now friendly; actionable backend details retained.')
