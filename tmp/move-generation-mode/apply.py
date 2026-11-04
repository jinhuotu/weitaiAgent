from pathlib import Path
import difflib

target = Path('E:/code/python/weitaiAgentui/src/views/TendersView.vue')
root = Path(__file__).resolve().parent
before = target.read_text(encoding='utf-8')
block = '''      <label class="flex items-center gap-2 text-[12px]">
        生成模式
        <select class="kb-input font-sans" :value="effectiveGenerationMode" :disabled="generating || workflowLocked" @change="selectGenerationMode">
          <option value="full">生成完整投标文件（综合标）</option>
          <option value="split">分册两本（商务标 + 技术标）</option>
          <option value="single">单卷（当前商务标 / 技术标）</option>
        </select>
      </label>
'''
anchor = '''          <div class="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
            <div
              v-if="result.generateVolume !== 'full' && result.docxFile && result.techDocxFile"'''
assert before.count(block) == 1
assert before.count(anchor) == 1
after = before.replace(block, '', 1)
moved = '\n'.join('      ' + line for line in block.rstrip('\n').splitlines()) + '\n'
after = after.replace(anchor, anchor.split('\n', 1)[0] + '\n' + moved + anchor.split('\n', 1)[1], 1)
assert before.split('</script>', 1)[0] == after.split('</script>', 1)[0]
assert after.count('@change="selectGenerationMode"') == 1
header = after.split('<template #badges>', 1)[1].split('</template>', 1)[0]
assert '@change="selectGenerationMode"' not in header
preview = after.split('<!-- 步骤 3：在线预览 -->', 1)[1].split('<!-- 步骤 4：AI 自检 -->', 1)[0]
assert preview.index('@change="selectGenerationMode"') < preview.index('aria-label="预览商务标或技术标"')
(root / 'TendersView.before.vue').write_text(before, encoding='utf-8', newline='\n')
(root / 'move-generation-mode.patch').write_text(''.join(difflib.unified_diff(
    before.splitlines(True), after.splitlines(True), fromfile='a/src/views/TendersView.vue',
    tofile='b/src/views/TendersView.vue')), encoding='utf-8', newline='\n')
target.write_text(after, encoding='utf-8', newline='\n')
print('Moved generation mode into preview operations before volume tabs; script unchanged.')
