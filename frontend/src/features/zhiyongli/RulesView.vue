<template>
  <AppLayout>
    <div class="mx-auto max-w-6xl">
      <header class="mb-6 flex items-center justify-between gap-4">
        <h2 class="text-2xl font-semibold">评分规则</h2>
        <div class="flex items-center gap-4"><RouterLink :to="auth.isAdmin ? '/admin/zhiyongli' : '/zhiyongli'" class="text-sm text-primary-700 hover:underline dark:text-primary-400">返回报告</RouterLink><a v-if="supported" :href="downloadUrl" :download="`智用力评分规则-v${version}.md`" class="btn btn-secondary">下载规则</a></div>
      </header>
      <p v-if="supported" class="mb-6 text-sm text-gray-500 dark:text-gray-400" role="status">
        <template v-if="version === latestVersion">当前规则 · v{{ version }} · 按对话上下文综合评审</template>
        <template v-else>历史规则 · v{{ version }} · 用于核对采用该版的报告。<RouterLink :to="{ path: '/zhiyongli/rules' }" class="ml-2 text-primary-700 hover:underline dark:text-primary-400">查看当前规则 v{{ latestVersion }}</RouterLink></template>
      </p>
      <div v-if="supported" class="rules-layout">
        <article class="rules-body min-w-0" v-html="rendered.html"></article>
        <nav class="rules-toc self-start border-l border-gray-200 pl-4 text-sm dark:border-dark-700" aria-label="规则章节">
          <a v-for="section in rendered.sections" :key="section.id" :href="`#${section.id}`" class="block py-2 text-gray-600 hover:text-primary-700 focus-visible:outline-primary-500 dark:text-gray-400 dark:hover:text-primary-400">{{ section.title }}</a>
        </nav>
      </div>
      <p v-else role="alert" class="text-sm text-red-600">当前应用未收录此报告采用的规则版本：{{ version }}</p>
    </div>
  </AppLayout>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { marked, Renderer } from 'marked'
import DOMPurify from 'dompurify'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useAuthStore } from '@/stores/auth'
import rules11 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1.md?raw'
import rules12 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1_2.md?raw'
import rules13 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1_3.md?raw'
import rules14 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1_4.md?raw'
import rules15 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1_5.md?raw'
import rules16 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1_6.md?raw'
const latestVersion = '1.6'
const route=useRoute()
const auth=useAuthStore()
const versions: Record<string,string> = { '1.1':rules11,'1.2':rules12,'1.3':rules13,'1.4':rules14,'1.5':rules15,'1.6':rules16 }
const version=computed(()=>route.query.version === undefined ? latestVersion : String(route.query.version))
const supported=computed(()=>Object.prototype.hasOwnProperty.call(versions,version.value))
const rules=computed(()=>supported.value ? versions[version.value] : '')
const rendered=computed(()=>{
  const sections: {id:string;title:string}[] = []
  const renderer = new Renderer()
  renderer.heading = function(token) {
    const content = this.parser.parseInline(token.tokens)
    if (token.depth !== 2) return `<h${token.depth}>${content}</h${token.depth}>`
    const id = `rule-section-${sections.length + 1}`
    sections.push({id, title:token.text})
    return `<h2 id="${id}">${content}</h2>`
  }
  return {html:DOMPurify.sanitize(marked.parse(rules.value,{async:false, renderer})), sections}
})
const downloadUrl=computed(()=>URL.createObjectURL(new Blob([rules.value],{type:'text/markdown;charset=utf-8'})))
watch(downloadUrl,(_,old)=>URL.revokeObjectURL(old))
onBeforeUnmount(()=>URL.revokeObjectURL(downloadUrl.value))
</script>
<style scoped>
.rules-body{font-size:14px;line-height:1.9}.rules-body :deep(h1){display:none}.rules-body :deep(h2){margin:36px 0 16px;font-size:20px;font-weight:600;scroll-margin-top:88px}.rules-body :deep(h3){margin:24px 0 12px;font-size:16px;font-weight:600}.rules-body :deep(p){margin:10px 0}.rules-body :deep(table){width:100%;margin:16px 0;border-collapse:collapse;font-size:13px}.rules-body :deep(th),.rules-body :deep(td){padding:10px;text-align:left;border:1px solid rgb(148 163 184 / .35)}.rules-body :deep(blockquote){border-left:2px solid var(--color-primary-500,#14b8a6);padding:2px 16px;background:rgb(148 163 184 / .06);margin:16px 0}.rules-body :deep(ul),.rules-body :deep(ol){padding-left:24px;list-style:revert}.rules-body :deep(hr){margin:24px 0;border-color:rgb(148 163 184 / .3)}.rules-body :deep(code){font-size:13px;overflow-wrap:anywhere}
.rules-layout{display:grid;grid-template-columns:minmax(0,1fr) 176px;gap:32px}.rules-toc{position:sticky;top:88px}.rules-body :deep(th){background:rgb(148 163 184 / .08);white-space:nowrap}.rules-body :deep(blockquote+blockquote){margin-top:-8px}.rules-body :deep(a){color:#0f766e;text-decoration:underline}:global(.dark .rules-body a){color:#5eead4}
</style>
