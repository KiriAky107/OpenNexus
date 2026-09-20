import { hostInvoke, isDesktop } from '@/services/platform/desktop'

export interface ReleaseCheck {
  current_version: string
  latest_version: string
  name: string | null
  release_url: string
  published_at: string | null
  prerelease: boolean
  update_available: boolean
}

export async function checkGithubRelease() {
  return hostInvoke<ReleaseCheck>('github_release_check')
}

export async function openExternalUrl(url: string) {
  if (isDesktop()) return hostInvoke<void>('open_external_url', { url })
  window.open(url, '_blank', 'noopener,noreferrer')
}
