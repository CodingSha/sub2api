<template>
  <div class="text-sm leading-6">
    <template v-if="report.score_explanation">
      <ul class="space-y-2" :class="report.score_explanation.reasons.length > 1 ? 'list-disc pl-4' : ''">
        <li v-for="reason in report.score_explanation.reasons" :key="reason">{{ reason }}</li>
      </ul>
      <p class="mt-3 border-t border-gray-500/20 pt-3">{{ report.score_explanation.effect }}</p>
    </template>
    <p v-else class="whitespace-pre-line">{{ reportText(report)(report.score_reason) }}</p>
  </div>
</template>
<script setup lang="ts">
import type { Report } from './api'
import { reportText } from './reportPresentation'
defineProps<{report: Report}>()
</script>
