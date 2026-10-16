import { expect, it } from 'vitest'
import { canvasColor } from './canvasColors'
it('resolves document color IDs through the same active semantic palette on both clients', () => {
  expect(['1', '2', '3', '4', '5', '6'].map(value => canvasColor(value))).toEqual(['error', 'warning', 'accent-secondary', 'success', 'info', 'accent-primary'].map(name => `var(--color-${name})`))
})
it('preserves an original custom color and rejects unknown CSS expressions without changing document data', () => {
  expect(canvasColor('#aBc123')).toBe('#aBc123')
  for (const value of [undefined, '', 'url(https://example.org)', '7', '#ffffffaa']) expect(canvasColor(value)).toBe('var(--color-border-default)')
  expect(canvasColor(undefined, 'var(--color-text-secondary)')).toBe('var(--color-text-secondary)')
})
