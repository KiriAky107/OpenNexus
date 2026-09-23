<script setup lang="ts">
// Small, font-independent glyphs for window chrome and editing controls.
const paths = {
  minimize: 'M5 12h14',
  maximize: 'M5 5h14v14H5z',
  restore: 'M9 5h10v10M5 9h10v10H5z',
  close: 'm6 6 12 12M18 6 6 18',
  moon: 'M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0-5V1m0 22v-2M3 12H1m22 0h-2M4.2 4.2l1.4 1.4m12.8 12.8 1.4 1.4M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  fold: 'M4 4h16M4 20h16m4-13 4 4 4-4m-8 10 4-4 4 4',
  unfold: 'M4 4h16M4 20h16m-12-9 4-4 4 4m-8 2 4 4 4-4',
  heading: 'M5 5v14M19 5v14M5 12h14',
  bold: 'M6 4h7a4 4 0 0 1 0 8H6m0-8v16h8a4 4 0 0 0 0-8H6',
  italic: 'M10 4h9M5 20h9M15 4 9 20',
  orderedList: 'M10 5h10M10 12h10M10 19h10M3 4l1-1v5M3 8h2M2.5 14a1.5 1.5 0 0 1 3 0c0 1-3 2-3 4h3',
  bulletList: 'M10 5h10M10 12h10M10 19h10M4 5h.01M4 12h.01M4 19h.01',
  fontSize: 'M3 19 9 5l6 14M5 14h8M18 10h5m-2.5 0v9',
  inlineCode: 'm7 7-5 5 5 5m10-10 5 5-5 5M14 4l-4 16',
  codeBlock: 'M8 3H6a2 2 0 0 0-2 2v4c0 2-2 3-2 3s2 1 2 3v4a2 2 0 0 0 2 2h2m8-18h2a2 2 0 0 1 2 2v4c0 2 2 3 2 3s-2 1-2 3v4a2 2 0 0 1-2 2h-2',
  function: 'M13 4h-1c-2 0-3 1-3.5 3L6 18c-.5 2-1.5 2-3 2M4 10h9m2 3 6 7m0-7-6 7',
  formula: 'M19 4H5l7 8-7 8h14',
  link: 'm10 14 4-4m-5 7-1 1a4.2 4.2 0 0 1-6-6l4-4a4.2 4.2 0 0 1 6 0m0 8a4.2 4.2 0 0 0 6 0l4-4a4.2 4.2 0 0 0-6-6l-1 1',
  image: 'M4 4h16v16H4zM4 16l5-5 4 4 3-3 4 4M15 8h.01',
  callout: 'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z M12 8v4m0 3h.01',
  chevron: 'm8 10 4 4 4-4',
  hide: 'm7 14 5-5 5 5M4 4h16',
  toolbar: 'M3 5h18v14H3zM3 10h18M7 7.5h.01M11 7.5h.01M15 7.5h.01',
  panelClose: 'M3 4h18v16H3zM9 4v16m7-11-3 3 3 3',
  files: 'M3 6h7l2 2h9v12H3zM3 6V4h7l2 2',
  outline: 'M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01',
  undo: 'M4 5v6h6M4 11l4-4a7 7 0 1 1 1 12',
  redo: 'M20 5v6h-6m6 0-4-4a7 7 0 1 0-1 12',
  strikethrough: 'M17 6c-1-2-8-3-9 1-.5 2 1 3 4 4M5 12h14m-7 2c3 1 5 2 4 4-1 3-7 3-10 0',
  taskList: 'M10 6h11M10 17h11m-18-1 2 2 3-4M3 3h4v5H3z',
  quote: 'M4 6h6v6H4zM4 12c0 4 1 6 5 7M14 6h6v6h-6zM14 12c0 4 1 6 5 7',
  table: 'M3 4h18v16H3zM3 10h18M3 15h18M10 4v16',
  rule: 'M4 12h16',
  diagram: 'M8 3h8v5H8zM3 16h6v5H3zM15 16h6v5h-6zM12 8v4M6 16v-4h12v4',
} as const
withDefaults(defineProps<{ name: keyof typeof paths; size?: number }>(), { size: 18 })
</script>

<template>
  <svg class="control-icon" :data-icon="name" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="paths[name]" /></svg>
</template>

<style scoped>
.control-icon { display: block; flex: 0 0 auto; pointer-events: none; }
</style>
