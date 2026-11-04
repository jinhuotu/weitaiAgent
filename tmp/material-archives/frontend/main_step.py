from pathlib import Path
p=Path('tmp/material-archives/frontend/TendersView.vue')
s=p.read_text(encoding='utf-8')
s=s.replace('const form = reactive<BidBrief>(emptyBrief())', 'const form = reactive<BidBrief>(emptyBrief())\nconst materialStageActive = ref(false)',1)
s=s.replace('''        <li
          v-for="(step, i) in workflowSteps"
          :key="step.key"''', '''        <template v-for="(step, i) in workflowSteps" :key="step.key">
        <li''',1)
s=s.replace('@click="goToStep(i)"', '@click="materialStageActive = false; goToStep(i)"',1)
s=s.replace("'tender-step-dot--active': i === activeWorkflowIndex,", "'tender-step-dot--active': i === activeWorkflowIndex && !materialStageActive,",1)
s=s.replace("{{ i < activeWorkflowIndex ? '✓' : i + 1 }}", "{{ i < activeWorkflowIndex ? '✓' : i === 0 ? 1 : i + 2 }}",1)
s=s.replace('''        </li>
      </ol>''','''        </li>
        <li v-if="i === 0" class="tender-step-item tender-step-item--grow">
          <button type="button" class="tender-step-btn" :disabled="maxStepReached < 1"
            @click="goToStep(1); materialStageActive = true">
            <span class="tender-step-dot" :class="{
              'tender-step-dot--active': currentStepIndex === 1 && materialStageActive,
              'tender-step-dot--done': currentStepIndex > 1 || (currentStepIndex === 1 && !materialStageActive),
            }">{{ currentStepIndex > 1 || (currentStepIndex === 1 && !materialStageActive) ? '✓' : 2 }}</span>
            <span class="text-[12px] font-medium hidden md:inline whitespace-nowrap">资料与图纸</span>
          </button>
          <span class="tender-step-line" :class="currentStepIndex > 1 ? 'tender-step-line--done' : ''" />
        </li>
        </template>
      </ol>''',1)
s=s.replace('''  advanceStep(1)
  await nextTick()
  document.getElementById('tender-parse-notes')''','''  advanceStep(1)
  materialStageActive.value = true
  await nextTick()
  document.getElementById('tender-parse-notes')''',1)
old='''    <section v-else-if="currentStepIndex === 1" class="tender-step-panel space-y-4">
      <MaterialArchivePanel'''
new='''    <section v-else-if="currentStepIndex === 1 && materialStageActive" class="tender-step-panel space-y-4">
      <MaterialArchivePanel'''
assert old in s
s=s.replace(old,new,1)
old='''        @check="openMaterialCheck(false)"
      />
      <div'''
new='''        @check="openMaterialCheck(false)"
      />
      <div class="flex justify-end"><button type="button" class="kb-btn-primary" :disabled="materialCheckBusy" @click="materialStageActive = false">资料已核对，继续填写商务/技术标</button></div>
    </section>
    <section v-else-if="currentStepIndex === 1" class="tender-step-panel space-y-4">
      <button type="button" class="kb-btn-secondary" @click="materialStageActive = true">返回资料与图纸步骤</button>
      <div'''
assert old in s
s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')
