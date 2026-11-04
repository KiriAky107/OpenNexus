<script setup lang="ts">
import { computed } from 'vue'
import { layoutHandwrittenText, strokePath, type Handwriting } from './handwriting'
const props=defineProps<{ ink: Handwriting; text: string; draft?: ReturnType<typeof strokePath>; draftColor?: string }>()
const lines=computed(()=>Array.from({length:Math.ceil(props.ink.height/(props.ink.fontSize*props.ink.lineHeight))},(_,i)=>(i+1)*props.ink.fontSize*props.ink.lineHeight+24))
const layout=computed(()=>props.ink.mode==='text'?layoutHandwrittenText(props.text,props.ink):{glyphs:[],overflow:false})
</script>
<template>
  <svg class="handwriting-view" :viewBox="`0 0 ${ink.width} ${ink.height}`" role="img" :aria-label="text || 'Handwriting'" xmlns="http://www.w3.org/2000/svg">
    <rect width="100%" height="100%" fill="#fffdf6"/>
    <g v-if="ink.paper!=='plain'" stroke="#dce4ed" stroke-width="1" aria-hidden="true">
      <path v-for="y in lines" :key="y" :d="`M 0 ${y} H ${ink.width}`"/>
      <template v-if="ink.paper==='grid'"><path v-for="i in Math.ceil(ink.width/32)" :key="`x${i}`" :d="`M ${i*32} 0 V ${ink.height}`"/></template>
    </g>
    <path v-for="stroke in ink.strokes" :key="stroke.id" v-memo="[stroke]" :d="strokePath(stroke)" :fill="stroke.color"/>
    <path v-if="draft" :d="draft" :fill="draftColor??'#263746'"/>
    <g v-if="ink.mode==='text'" fill="#263746" :font-size="ink.fontSize" font-family="'OpenNexus Handwriting', KaiTi, STKaiti, 'Kaiti SC', cursive">
      <text v-for="(glyph,index) in layout.glyphs" :key="index" :x="glyph.x" :y="glyph.y" :transform="`rotate(${glyph.rotation} ${glyph.x} ${glyph.y})`">{{ glyph.char }}</text>
    </g>
  </svg>
</template>
<style scoped>
@font-face{font-family:'OpenNexus Handwriting';src:url('../../assets/fonts/OpenNexusHandwriting.woff') format('woff');font-style:normal;font-weight:400;font-display:swap}
.handwriting-view{display:block;width:100%;height:100%;pointer-events:none}
</style>
