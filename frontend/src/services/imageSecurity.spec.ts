import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

describe('desktop remote image policy', () => {
  it('allows HTTP(S) images without broadening script or API network permissions', () => {
    const config = JSON.parse(readFileSync(new URL('../../src-tauri/tauri.conf.json', import.meta.url), 'utf8'))
    const directives = new Map<string, string[]>(config.app.security.csp.split(';').map((part: string) => {
      const [name, ...values] = part.trim().split(/\s+/)
      return [name, values]
    }))
    expect(directives.get('img-src')).toEqual(expect.arrayContaining(['https:', 'http:', 'blob:']))
    expect(directives.get('script-src')).toEqual(["'self'", "'wasm-unsafe-eval'"])
    expect(directives.get('connect-src')).toEqual(['ipc:', 'http://ipc.localhost'])
  })
})
