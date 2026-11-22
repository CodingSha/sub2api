<template>
      <div class="radar-surface">
        <svg viewBox="0 0 560 360" role="img" :aria-label="radarDescription" class="h-full w-full">
          <polygon v-for="tick in radarTicks" :key="tick" :points="diamond(tick)" class="radar-grid" :class="{'radar-grid-outer': tick === 100}" />
          <path d="M280 50V310 M150 180H410" class="radar-axis" />
          <text v-for="tick in radarTicks" :key="`t${tick}`" x="288" :y="180-scoreRadius(tick)+4" class="radar-tick text-gray-500 dark:text-gray-400">{{ tick }}</text>
          <g v-if="points" class="text-primary-600 dark:text-primary-400">
            <polygon :points="points" fill="currentColor" fill-opacity="0.09" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
          </g>
          <g v-for="(d,i) in chartDimensions" :key="d.code">
            <circle v-if="d.score !== null" :cx="point(i, Number(d.score)).x" :cy="point(i, Number(d.score)).y" r="3.5" class="radar-point text-primary-600 dark:text-primary-400" />
            <text :x="labels[i].x" :y="labels[i].y" text-anchor="middle" class="radar-label text-gray-600 dark:text-gray-400">{{ d.name }}</text>
            <text :x="labels[i].x" :y="labels[i].y+23" text-anchor="middle" class="radar-value text-gray-900 dark:text-gray-100">{{ d.score === null ? '—' : d.score }}</text>
          </g>
          <text x="544" y="344" text-anchor="end" class="radar-tick text-gray-500 dark:text-gray-400">非等距刻度</text>
        </svg>
      </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { Dimension } from './api'
const props = defineProps<{ dimensions: Dimension[] }>()
const chartDimensions = computed(() => ['P','M','J','E'].map(code => props.dimensions.find(d => d.code === code)!).filter(Boolean))
const labels = [{x:280,y:20},{x:475,y:173},{x:280,y:337},{x:85,y:173}]
const radarTicks = [20,40,60,80,90,100]
// 统一展开高分区间，刻度与数据共用映射；90至100留出35个坐标单位。
const scoreRadius = (value: number) => value <= 60 ? value * 0.75 : value <= 80 ? 45 + (value - 60) * 1.25 : value <= 90 ? 70 + (value - 80) * 2.5 : 95 + (value - 90) * 3.5
const point = (index: number, value: number) => ({ x: 280 + [0,1,0,-1][index]*scoreRadius(value), y: 180 + [-1,0,1,0][index]*scoreRadius(value) })
const diamond = (value: number) => [0,1,2,3].map(i => { const p=point(i,value); return `${p.x},${p.y}` }).join(' ')
const points = computed(() => chartDimensions.value.length === 4 && chartDimensions.value.every(d => d.score !== null) ? chartDimensions.value.map((d,i) => { const p=point(i,Number(d.score));return `${p.x},${p.y}` }).join(' ') : '')
const radarDescription = computed(() => `智用力${props.dimensions.some(d => d.score === null) ? '维度观察' : ''}画像，0至100分，高分区间展开，非等距刻度。${chartDimensions.value.map(d => `${d.name}${d.score === null ? '未评分' : d.score+'分'}`).join('，')}`)
</script>
<style scoped>
.radar-surface{height:340px;background:transparent}.radar-label{font-size:16px;fill:currentColor}.radar-value{font-size:21px;font-weight:600;fill:currentColor}.radar-tick{font-size:12px;fill:currentColor}
.radar-grid{fill:none;stroke:rgb(100 116 139 / .18);stroke-width:1}.radar-grid-outer{stroke:rgb(100 116 139 / .4)}.radar-axis{stroke:rgb(100 116 139 / .2);stroke-width:1}.radar-point{fill:currentColor}
:global(.dark .radar-grid){stroke:rgb(148 163 184 / .22)}:global(.dark .radar-grid-outer){stroke:rgb(148 163 184 / .45)}:global(.dark .radar-axis){stroke:rgb(148 163 184 / .25)}
</style>
