<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="mb-5 flex items-start justify-between gap-4 flex-wrap">
    <div class="min-w-0">
      <div class="flex items-center gap-3">
        <h1 class="text-xl lg:text-2xl font-semibold tracking-wide">
          {{ title }}
        </h1>
        <slot name="badges" />
      </div>
      <p
        v-if="description"
        class="text-xs lg:text-sm text-muted-foreground mt-1.5 max-w-3xl"
      >
        {{ description }}
      </p>
    </div>
    <div v-if="$slots.actions" class="shrink-0 flex items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
