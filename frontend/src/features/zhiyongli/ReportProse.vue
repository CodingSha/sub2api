<template><div class="report-prose" v-html="renderReportText(text)" /></template>

<script setup lang="ts">
import { renderReportText } from './reportText'
defineProps<{ text: string }>()
</script>

<style scoped>
.report-prose { overflow-wrap: anywhere; }
.report-prose :deep(p + p), .report-prose :deep(ul), .report-prose :deep(ol), .report-prose :deep(pre), .report-prose :deep(blockquote) { margin-top: .65rem; }
.report-prose :deep(ul) { list-style: disc; padding-left: 1.5rem; }
.report-prose :deep(ol) { list-style: decimal; padding-left: 1.5rem; }
.report-prose :deep(pre) { overflow-x: auto; white-space: pre; padding: .8rem; background: rgb(128 128 128 / .08); border-radius: 4px; }
.report-prose :deep(code) { font-size: .9em; }
.report-prose :deep(a) { text-decoration: underline; text-underline-offset: 3px; }
.report-prose :deep(h1), .report-prose :deep(h2), .report-prose :deep(h3), .report-prose :deep(h4) { margin-top: .8rem; font-size: 1em; font-weight: 600; }
.report-prose :deep(blockquote) { padding-left: 1rem; border-left: 2px solid rgb(128 128 128 / .25); }
.report-prose :deep(th), .report-prose :deep(td) { padding: .4rem; border: 1px solid rgb(128 128 128 / .2); }
</style>
