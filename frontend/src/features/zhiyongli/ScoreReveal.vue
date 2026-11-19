<template>
  <span ref="element" class="score-reveal" :class="{ 'score-reveal--playing': playing }" :data-tone="tone" :aria-label="`${score}分`">
    <span v-if="playing" class="score-reveal__stage" aria-hidden="true">
      <span class="score-reveal__aura" />
      <span class="score-reveal__membrane" />
      <span class="score-reveal__pulse" />
      <span v-for="(shard, i) in shards" :key="i" class="score-reveal__shard" :style="shard" />
      <span class="score-reveal__seat" />
    </span>
    <span class="score-reveal__value" :data-score="score" aria-hidden="true" @animationend="settle"><span class="score-reveal__face">{{ score }}</span></span>
  </span>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(defineProps<{ score: string; exact: string | null; identity: string; animate?: boolean }>(), { animate: true })
// exact由服务端按有理数返回；配色不使用可能跨越档位的展示舍入值。
const tone = computed(() => {
  const match = /^(\d+)(?:\/([1-9]\d*))?$/.exec(props.exact ?? '')
  if (!match) return 'graphite'
  const numerator = BigInt(match[1]), denominator = BigInt(match[2] ?? '1')
  if (numerator > 100n * denominator) return 'graphite'
  const tones = ['graphite', 'copper', 'steel', 'jade', 'gold']
  return tones[[60n, 70n, 80n, 90n].filter(boundary => numerator >= boundary * denominator).length]
})
const shards = Array.from({ length: 16 }, (_, i) => {
  const angle = i / 16 * Math.PI * 2 + .15
  const reach = 110 + (i % 3) * 20
  return {
    '--ox': `${Math.cos(angle) * 55}px`, '--oy': `${Math.sin(angle) * 12 - 8}px`,
    '--x': `${Math.cos(angle) * reach}px`, '--y': `${Math.sin(angle) * reach * .47 - 14}px`,
    '--angle': `${i * 137}deg`, '--turn': `${i % 2 ? 170 : -140}deg`,
    '--size': `${7 + i % 4 * 2}px`, '--delay': `${i % 4 * 22}ms`
  }
})
const playing = ref(false)
const element = ref<HTMLElement>()
let observer: IntersectionObserver | undefined
let fallback: ReturnType<typeof setTimeout> | undefined
let preference: MediaQueryList | undefined
const storageKey = () => `zhiyongli:score-reveal:${props.identity}`

function finish() {
  if (!playing.value) return
  playing.value = false
  clearTimeout(fallback)
  try { window.localStorage.setItem(storageKey(), 'seen') } catch { /* 存储失败不影响真实分数。 */ }
}
function settle(event: AnimationEvent) {
  if (event.animationName.startsWith('score-emerge')) finish()
}
function preferenceChanged() { if (preference?.matches) finish() }
function timeoutFinish() {
  // 开发预览暂停帧时保留现场；正常播放仍有计时器兜住动画事件缺失。
  if (element.value?.getAnimations?.({ subtree: true }).some(a => a.playState === 'paused')) {
    fallback = setTimeout(timeoutFinish, 500)
  } else finish()
}
onMounted(() => {
  if (props.animate === false) return
  preference = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  if (preference?.matches) return
  try { if (window.localStorage.getItem(storageKey())) return } catch { return }
  preference?.addEventListener('change', preferenceChanged)
  const start = () => {
    observer?.disconnect()
    if (preference?.matches) return
    playing.value = true
    fallback = setTimeout(timeoutFinish, 2850)
  }
  if (!window.IntersectionObserver) { start(); return }
  observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) start() }, { threshold: 0.6 })
  if (element.value) observer.observe(element.value)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  preference?.removeEventListener('change', preferenceChanged)
  clearTimeout(fallback)
  playing.value = false
})
</script>

<style scoped>
.score-reveal{
  --hue:210;--saturation:7%;
  --metal-low:hsl(var(--hue) var(--saturation) 22%);
  --metal-mid:hsl(var(--hue) var(--saturation) 41%);
  --metal-high:hsl(var(--hue) var(--saturation) 65%);
  --metal-shine:hsl(var(--hue) 35% 85%);
  --metal-edge:hsl(var(--hue) var(--saturation) 16%);
  --metal-side:hsl(var(--hue) var(--saturation) 29%);
  --prism:hsl(var(--hue) var(--saturation) 45%);
  --metal-face:linear-gradient(173deg,var(--metal-low) 0%,var(--metal-mid) 22%,var(--metal-high) 31%,var(--metal-low) 34%,var(--metal-mid) 50%,var(--metal-low) 65%,var(--metal-high) 82%,var(--metal-low) 100%);
  position:relative;display:inline-block;vertical-align:baseline;isolation:isolate;perspective:700px
}
.score-reveal[data-tone="copper"]{--hue:20;--saturation:49%}
.score-reveal[data-tone="steel"]{--hue:207;--saturation:46%}
.score-reveal[data-tone="jade"]{--hue:158;--saturation:49%}
.score-reveal[data-tone="gold"]{--hue:42;--saturation:68%}
:global(.dark .score-reveal){
  --metal-low:hsl(var(--hue) var(--saturation) 44%);
  --metal-mid:hsl(var(--hue) var(--saturation) 69%);
  --metal-high:hsl(var(--hue) var(--saturation) 85%);
  --metal-shine:hsl(var(--hue) 45% 94%);
  --metal-side:hsl(var(--hue) var(--saturation) 36%);
  --prism:hsl(var(--hue) var(--saturation) 69%)
}
.score-reveal__value{position:relative;display:block;z-index:2;isolation:isolate;transform-origin:50% 85%;font-weight:800;letter-spacing:-.045em;color:var(--metal-mid)}
.score-reveal__face{display:block}
.score-reveal__value::before{content:attr(data-score);position:absolute;inset:0;z-index:-1;color:var(--metal-side);-webkit-text-fill-color:var(--metal-side);transform:translate(.35px,1.1px);text-shadow:0 1px 0 var(--metal-edge),0 2px 1px rgb(0 0 0 / .15)}
.score-reveal__value::after{content:attr(data-score);position:absolute;inset:0;opacity:0;pointer-events:none}
@supports ((background-clip:text) or (-webkit-background-clip:text)){
  .score-reveal__face{background-image:repeating-linear-gradient(0deg,rgb(255 255 255 / .065) 0 1px,transparent 1px 3px),var(--metal-face);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .score-reveal__value::after{background-image:linear-gradient(112deg,transparent 38%,var(--metal-high) 44%,var(--metal-shine) 48%,var(--metal-high) 52%,transparent 59%);background-size:260% 100%;background-position:140% 0;background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent}
}
.score-reveal--playing .score-reveal__value{animation:score-emerge 2600ms both}
.score-reveal--playing .score-reveal__value::before{animation:score-depth 2600ms both}
.score-reveal--playing .score-reveal__value::after{animation:score-sheen 2600ms linear both}
.score-reveal__stage{position:absolute;inset:-20px -95px -8px;z-index:1;pointer-events:none;user-select:none;overflow:hidden}
.score-reveal__aura{position:absolute;inset:8px 25px;background:radial-gradient(ellipse,var(--prism),transparent 68%);opacity:0;animation:score-aura 2600ms both}
.score-reveal__membrane{position:absolute;left:50%;top:62%;width:65%;height:3px;background:linear-gradient(90deg,transparent,var(--prism),var(--metal-shine),var(--prism),transparent);transform:translate(-50%,-50%);animation:score-membrane 2600ms both}
.score-reveal__pulse{position:absolute;left:50%;top:55%;width:48%;height:38%;border:1px solid var(--prism);border-radius:50%;opacity:0;animation:score-pulse 2600ms both}
.score-reveal__shard{position:absolute;left:50%;top:62%;width:var(--size);height:calc(var(--size) * 2.3);background:linear-gradient(115deg,transparent 5%,var(--metal-high) 30%,var(--prism) 48%,var(--metal-shine) 50%,var(--prism) 55%,transparent 100%);clip-path:polygon(48% 0,100% 30%,54% 100%,0 65%);opacity:0;transform-origin:40% 60%;animation:score-shard 2600ms linear both;animation-delay:var(--delay)}
.score-reveal__shard:nth-of-type(3n){width:3px;height:17px;clip-path:polygon(40% 0,100% 70%,20% 100%,0 20%)}
.score-reveal__seat{position:absolute;left:50%;bottom:15%;width:52%;height:6px;border-bottom:1px solid var(--prism);border-radius:50%;opacity:0;animation:score-seat 2600ms both}
@keyframes score-emerge{
  0%,12%{opacity:0;transform:translate3d(0,24px,-100px) rotateX(-22deg) scale(.82)}
  24%{opacity:1;transform:translate3d(0,-3px,30px) rotateX(-8deg) scale(1.015);filter:drop-shadow(0 10px 6px rgb(0 0 0 / .14))}
  36%{opacity:1;transform:translate3d(0,-8px,42px) rotateX(4deg) scale(1.035);filter:drop-shadow(0 14px 8px rgb(0 0 0 / .16))}
  50%{transform:translate3d(0,-6px,28px) rotateX(2deg) scale(1.025);filter:drop-shadow(0 10px 6px rgb(0 0 0 / .12))}
  65%{transform:translate3d(0,2px,-4px) rotateX(0) scale(.99,1.015);filter:drop-shadow(0 1px 1px rgb(0 0 0 / .2))}
  72%{transform:translate3d(0,-.5px,0) scale(1.004,.998);filter:none}
  100%{opacity:1;transform:translate3d(0,0,0) scale(1);filter:none}
}
@keyframes score-depth{0%,12%{transform:translate(1px,5px);text-shadow:0 1px 0 var(--metal-edge),0 3px 0 var(--metal-side),0 5px 2px rgb(0 0 0 / .2)}36%,50%{transform:translate(1px,4px);text-shadow:0 1px 0 var(--metal-edge),0 3px 0 var(--metal-side),0 5px 2px rgb(0 0 0 / .2)}65%{transform:translate(0,0);text-shadow:0 -1px 0 var(--metal-edge)}100%{transform:translate(.35px,1.1px);text-shadow:0 1px 0 var(--metal-edge),0 2px 1px rgb(0 0 0 / .15)}}
@keyframes score-sheen{0%,20%{opacity:0;background-position:140% 0}25%{opacity:.9;background-position:140% 0}42%{opacity:.9;background-position:-35% 0}55%,100%{opacity:0;background-position:-70% 0}}
@keyframes score-aura{0%{opacity:0;transform:scale(.3,.15)}15%{opacity:.13;transform:scale(.7,.3)}26%{opacity:.18;transform:scale(1,.8)}48%{opacity:.06;transform:scale(1.1,1)}70%,100%{opacity:0;transform:scale(1.2,1)}}
@keyframes score-membrane{0%{opacity:0;transform:translate(-50%,-50%) scaleX(.1)}12%{opacity:.7;transform:translate(-50%,-50%) scaleX(.65)}20%{opacity:1;transform:translate(-50%,-50%) scaleX(1)}35%{opacity:0;transform:translate(-50%,-50%) scaleX(1.5)}100%{opacity:0}}
@keyframes score-pulse{0%,18%{opacity:0;transform:translate(-50%,-50%) scale(.3)}24%{opacity:.5}48%{opacity:0;transform:translate(-50%,-50%) scale(1.75,1.4)}100%{opacity:0}}
@keyframes score-shard{
  0%,16%{opacity:0;transform:translate(calc(-50% + var(--ox)),calc(-50% + var(--oy))) rotate(var(--angle)) scale(.2)}
  23%{opacity:.9}
  38%{opacity:.65;transform:translate(calc(-50% + var(--x)),calc(-50% + var(--y))) rotate(var(--turn)) rotateY(35deg) scale(.75)}
  55%,100%{opacity:0;transform:translate(calc(-50% + var(--x) * 1.15),calc(-50% + var(--y) * 1.15)) rotate(var(--turn)) scale(.2)}
}
@keyframes score-seat{0%,62%{opacity:0;transform:translateX(-50%) scale(.65)}66%{opacity:.55;transform:translateX(-50%) scale(1)}85%,100%{opacity:0;transform:translateX(-50%) scale(1.12)}}
@media(prefers-reduced-motion:reduce){.score-reveal--playing .score-reveal__value,.score-reveal--playing .score-reveal__value::before{animation:none;opacity:1;transform:none;filter:none}.score-reveal__value::after,.score-reveal__stage{display:none}}
@media(forced-colors:active){.score-reveal__value{color:CanvasText;animation:none!important;opacity:1;transform:none;filter:none}.score-reveal__face{background:none;-webkit-text-fill-color:CanvasText;color:CanvasText}.score-reveal__value::before,.score-reveal__value::after,.score-reveal__stage{display:none}}
</style>
