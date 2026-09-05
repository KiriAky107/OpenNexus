// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { installCodeBlockLabels } from './codeBlockLabels'

it('keeps footer labels in sync when the language changes and stops after disposal', async () => {
  const root = document.createElement('div')
  root.innerHTML = '<div class="milkdown-code-block"><button class="language-button">Python</button></div>'
  const dispose = installCodeBlockLabels(root)
  const block = root.firstElementChild as HTMLElement
  expect(block.dataset.languageLabel).toBe('Python')
  block.querySelector('button')!.textContent = 'TypeScript'
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(block.dataset.languageLabel).toBe('TypeScript')
  dispose()
  block.querySelector('button')!.textContent = 'Rust'
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(block.dataset.languageLabel).toBe('TypeScript')
})
