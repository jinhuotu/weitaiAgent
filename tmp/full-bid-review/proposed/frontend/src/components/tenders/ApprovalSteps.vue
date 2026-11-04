<script setup lang="ts">
import { Check } from 'lucide-vue-next'
import type { ApprovalStepDef } from '@/lib/tenders-api'

const props = withDefaults(
  defineProps<{
    steps: readonly string[] | ApprovalStepDef[]
    current: number
    compact?: boolean
    showRoles?: boolean
  }>(),
  { compact: false, showRoles: false },
)

function labelOf(step: string | ApprovalStepDef): string {
  return typeof step === 'string' ? step : step.name
}

function roleOf(step: string | ApprovalStepDef): string {
  if (typeof step === 'string') return ''
  return step.roleName || ''
}

function keyOf(step: string | ApprovalStepDef, index: number): string {
  return typeof step === 'string' ? `${step}-${index}` : step.key || `${step.name}-${index}`
}

function stateOf(i: number): 'done' | 'active' | 'todo' {
  if (props.current < 0) return 'todo'
  if (i < props.current) return 'done'
  if (i === props.current) return 'active'
  return 'todo'
}
</script>

<template>
  <ol class="approval-steps" :class="compact ? 'approval-steps--compact' : ''">
    <li
      v-for="(step, i) in steps"
      :key="keyOf(step, i)"
      class="approval-step"
      :class="`approval-step--${stateOf(i)}`"
    >
      <div class="approval-step__node">
        <span class="approval-step__circle">
          <Check v-if="stateOf(i) === 'done'" class="size-3.5" />
          <template v-else>{{ i + 1 }}</template>
        </span>
        <span
          v-if="i < steps.length - 1"
          class="approval-step__line"
          :class="stateOf(i) === 'done' ? 'approval-step__line--done' : ''"
        />
      </div>
      <div class="approval-step__label">{{ labelOf(step) }}</div>
      <div v-if="showRoles && roleOf(step)" class="approval-step__role">
        {{ roleOf(step) }}
      </div>
    </li>
  </ol>
</template>

<style scoped>
.approval-steps {
  display: flex;
  align-items: flex-start;
  width: 100%;
  margin: 0;
  padding: 0.5rem 0 0.25rem;
  list-style: none;
}

.approval-step {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
}

.approval-step__node {
  display: flex;
  align-items: center;
  width: 100%;
  position: relative;
}

.approval-step__circle {
  width: 2.25rem;
  height: 2.25rem;
  border-radius: 9999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 700;
  border: 2px solid var(--border);
  background: var(--background);
  color: var(--muted-foreground);
  position: relative;
  z-index: 1;
  margin: 0 auto;
  flex-shrink: 0;
}

.approval-steps--compact .approval-step__circle {
  width: 1.75rem;
  height: 1.75rem;
  font-size: 0.6875rem;
}

.approval-step--done .approval-step__circle {
  background: color-mix(in srgb, var(--accent-patina, #5f9e4e) 18%, var(--card));
  border-color: var(--accent-patina, #5f9e4e);
  color: var(--accent-patina, #5f9e4e);
}

.approval-step--active .approval-step__circle {
  background: color-mix(in srgb, var(--accent-iron, #2563eb) 16%, var(--card));
  border-color: var(--accent-iron, #2563eb);
  color: var(--accent-iron, #2563eb);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent-iron, #2563eb) 18%, transparent);
}

.approval-step__line {
  position: absolute;
  top: 50%;
  left: calc(50% + 1.2rem);
  right: calc(-50% + 1.2rem);
  height: 2px;
  background: var(--border);
  transform: translateY(-50%);
}

.approval-steps--compact .approval-step__line {
  left: calc(50% + 0.95rem);
  right: calc(-50% + 0.95rem);
}

.approval-step__line--done {
  background: var(--accent-patina, #5f9e4e);
}

.approval-step:last-child .approval-step__line {
  display: none;
}

.approval-step__label {
  margin-top: 0.45rem;
  font-size: 0.6875rem;
  color: var(--muted-foreground);
  text-align: center;
  line-height: 1.3;
}

.approval-step--done .approval-step__label,
.approval-step--active .approval-step__label {
  color: var(--foreground);
  font-weight: 600;
}

.approval-step__role {
  margin-top: 0.15rem;
  font-size: 0.625rem;
  line-height: 1.3;
  color: var(--muted-foreground);
  text-align: center;
}
</style>
