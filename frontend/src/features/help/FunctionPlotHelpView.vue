<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { t } from '@/i18n'
import { useThemeStore } from '@/stores/theme'
import { renderFunctionPlot } from '@/services/functionPlotService'

const theme = useThemeStore()
const source = ref(`domain: -6, 6
range: -2, 8
xlabel: x
ylabel: y
grid: true
y = sin(x)
y = x^2 / 5`)
const svg = ref('')
const warnings = ref<string[]>([])
const error = ref('')
const loading = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

async function renderPreview() {
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const result = await renderFunctionPlot(source.value, theme.currentThemeId)
    if (current !== generation) return
    svg.value = result.svg
    warnings.value = result.warnings
  } catch (cause) {
    if (current !== generation) return
    svg.value = ''
    warnings.value = []
    error.value = cause instanceof Error ? cause.message : t('函数图渲染失败', 'Function Plot rendering failed')
  } finally {
    if (current === generation) loading.value = false
  }
}

watch([source, () => theme.currentThemeId], () => {
  clearTimeout(timer)
  timer = setTimeout(() => void renderPreview(), 180)
}, { immediate: true })
onBeforeUnmount(() => { clearTimeout(timer); generation += 1 })
</script>

<template>
  <div class="feature-page function-plot-help">
    <header class="feature-header">
      <div>
        <span class="badge info">Markdown · Function Plot</span>
        <h1>{{ t('Function Plot 教程', 'Function Plot Tutorial') }}</h1>
        <p>{{ t('用安全的数学表达式在笔记中绘制静态函数图。预览、HTML 和 PDF 导出使用同一套解析规则。', 'Draw static function graphs in notes with safe mathematical expressions. Preview, HTML, and PDF export share the same parser.') }}</p>
      </div>
    </header>

    <div class="tutorial-layout">
      <main class="tutorial-content">
        <section class="panel lesson">
          <span class="step">01</span>
          <h2>{{ t('插入代码围栏', 'Insert a fenced block') }}</h2>
          <p>{{ t('语言标记使用 function-plot。每个非空表达式行都绘制一条曲线。', 'Use function-plot as the language tag. Every non-empty expression line draws one curve.') }}</p>
          <pre><code>```function-plot
domain: -10, 10
y = sin(x)
y = x^2 / 8
```</code></pre>
        </section>

        <section class="panel lesson">
          <span class="step">02</span>
          <h2>{{ t('设置坐标范围', 'Configure the axes') }}</h2>
          <div class="directive-grid">
            <div class="surface-nested"><code>domain: -10, 10</code><span>{{ t('横轴范围', 'X-axis range') }}</span></div>
            <div class="surface-nested"><code>range: -5, 20</code><span>{{ t('可选纵轴范围', 'Optional Y-axis range') }}</span></div>
            <div class="surface-nested"><code>xlabel: 时间</code><span>{{ t('横轴名称', 'X-axis label') }}</span></div>
            <div class="surface-nested"><code>ylabel: 距离</code><span>{{ t('纵轴名称', 'Y-axis label') }}</span></div>
            <div class="surface-nested"><code>grid: false</code><span>{{ t('显示或隐藏网格', 'Show or hide the grid') }}</span></div>
          </div>
        </section>

        <section class="panel lesson">
          <span class="step">03</span>
          <h2>{{ t('可用数学语法', 'Supported math syntax') }}</h2>
          <p>{{ t('支持 +、-、*、/、^，变量 x，常量 pi、e，以及以下单参数函数。2x、2(x+1) 等隐式乘法也可使用。', 'Use +, -, *, /, ^, variable x, constants pi and e, and the single-argument functions below. Implicit multiplication such as 2x and 2(x+1) is also supported.') }}</p>
          <div class="tag-list function-list">
            <code v-for="name in ['sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh', 'exp', 'log', 'ln', 'log10', 'log2', 'sqrt', 'abs']" :key="name">{{ name }}(x)</code>
          </div>
          <p class="notice-banner safety-note">{{ t('表达式由白名单解析器计算，不执行 JavaScript、Python、属性访问或任意函数调用。单个图块最多 16 条表达式。', 'Expressions are evaluated by an allowlist parser. JavaScript, Python, property access, and arbitrary calls are never executed. Each plot supports up to 16 expressions.') }}</p>
        </section>
      </main>

      <aside class="panel playground">
        <div class="playground-heading"><div><span class="step">04</span><h2>{{ t('即时练习', 'Try it live') }}</h2></div><span v-if="loading" class="badge">{{ t('渲染中', 'Rendering') }}</span></div>
        <label class="field">
          <span>{{ t('函数图源码', 'Function Plot source') }}</span>
          <textarea v-model="source" class="textarea plot-source" spellcheck="false" />
        </label>
        <div class="plot-preview" :aria-busy="loading">
          <div v-if="svg" class="svg-host" v-html="svg" />
          <div v-else class="empty-state"><strong>{{ t('暂无预览', 'No preview') }}</strong><span>{{ error || t('输入有效表达式后会在这里显示。', 'Enter a valid expression to render it here.') }}</span></div>
        </div>
        <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
        <ul v-if="warnings.length" class="warning-list">
          <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
        </ul>
        <p class="subtle">{{ t('也可以在智能体中启用 function_plot.compose，让 Agent 生成并校验同样的代码块。', 'Enable function_plot.compose in an Agent to generate and validate the same fenced block.') }}</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.function-plot-help { container-type: inline-size; }
.feature-header h1 { margin-top: var(--space-sm); }
.tutorial-layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(340px, .85fr); gap: var(--space-lg); max-width: 1180px; margin-inline: auto; align-items: start; }
.tutorial-content { display: grid; gap: var(--space-lg); }
.lesson { position: relative; display: grid; gap: var(--space-md); }
.step { color: var(--color-accent-primary); font: 700 var(--font-size-xs)/1 var(--font-ui-mono); letter-spacing: .12em; }
.lesson p { color: var(--color-text-secondary); line-height: var(--line-height-relaxed); }
pre { overflow: auto; padding: var(--space-lg); border: 1px solid var(--color-code-border); border-radius: var(--radius-md); background: var(--color-code-background); color: var(--color-code-text); }
code { font-family: var(--font-ui-mono); }
.directive-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: var(--space-sm); }
.directive-grid > div { display: grid; gap: var(--space-xs); padding: var(--space-md); }
.directive-grid span { color: var(--color-text-secondary); font-size: var(--font-size-sm); }
.function-list code { padding: 5px 8px; border: 1px solid var(--color-border-subtle); border-radius: var(--radius-sm); background: var(--color-background-secondary); color: var(--color-accent-primary); }
.safety-note { margin: 0; }
.playground { position: sticky; top: var(--space-lg); display: grid; gap: var(--space-md); }
.playground-heading, .playground-heading > div { display: flex; align-items: center; justify-content: space-between; gap: var(--space-sm); }
.playground-heading > div { justify-content: flex-start; }
.plot-source { min-height: 190px; font-family: var(--font-ui-mono); }
.plot-preview { min-height: 280px; overflow: auto; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-plot-background); }
.svg-host { min-width: 620px; line-height: 0; }
.svg-host :deep(svg) { display: block; width: 100%; height: auto; }
.plot-preview .empty-state { min-height: 278px; border: 0; border-radius: 0; }
.warning-list { display: grid; gap: var(--space-xs); color: var(--color-warning); font-size: var(--font-size-sm); }
@container (max-width: 860px) { .tutorial-layout { grid-template-columns: 1fr; } .playground { position: static; } }
</style>
