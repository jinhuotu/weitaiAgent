<script setup lang="ts">
export type TreeNode = { name: string; children: TreeNode[]; file?: any }
defineProps<{ nodes: TreeNode[] }>()
</script>
<template><ul class="package-tree"><li v-for="node in nodes" :key="node.name"><details v-if="node.children.length" open><summary>{{ node.name }} <small v-if="node.file">（压缩容器 / {{ node.file.sha256 }}）</small></summary><PackageTree :nodes="node.children" /></details><span v-else>{{ node.name }} <small v-if="node.file">层级 {{ node.file.depth }} · {{ node.file.role }} · {{ node.file.size }} 字节<span v-if="node.file.duplicate_of"> · 重复：{{ node.file.duplicate_of }}</span></small></span></li></ul></template>
<style scoped>.package-tree{padding-left:22px;overflow-wrap:anywhere}.package-tree li{margin:7px 0}small{color:#64748b}</style>
