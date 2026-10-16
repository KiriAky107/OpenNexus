/** Canvas color IDs are document data; resolve them to the active theme only for display. */
export function canvasColor(value: unknown, fallback = 'var(--color-border-default)'): string {
  if (typeof value !== 'string') return fallback
  if (/^#[\da-f]{6}$/i.test(value)) return value
  return ({ '1': 'var(--color-error)', '2': 'var(--color-warning)', '3': 'var(--color-accent-secondary)',
    '4': 'var(--color-success)', '5': 'var(--color-info)', '6': 'var(--color-accent-primary)' } as Record<string, string>)[value] ?? fallback
}
