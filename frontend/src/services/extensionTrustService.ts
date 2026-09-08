import { invoke } from '@tauri-apps/api/core'
import type { CommunityKey } from '@/contracts/community'

export interface TrustSetting {
  source: string; source_id: string; namespace: string; key_id: string
  public_key: number[]; enabled: boolean
}
export interface TrustReview {
  review_id: string; fingerprint: string; previous: TrustSetting | null; proposed: TrustSetting
}
export async function reviewTrust(url: string, sourceId: string, keys: CommunityKey[], enabled: boolean): Promise<TrustReview[]> {
  const source = new URL(url)
  if (source.protocol !== 'https:' || source.username || source.password || source.search || source.hash) throw new Error('桌面信任来源必须使用 HTTPS')
  source.pathname = `${source.pathname.replace(/\/+$/, '')}/`
  if (!keys.length || keys.length > 64) throw new Error('来源公钥数量必须为 1–64')
  const settings = keys.map(key => {
    const publicKey = Array.from(atob(key.public_key), c => c.charCodeAt(0))
    if (publicKey.length !== 32 || (enabled && key.revoked)) throw new Error('公钥无效或已撤销')
    return { source: source.toString(), source_id: sourceId, namespace: key.namespace, key_id: key.key_id, public_key: publicKey, enabled }
  })
  const reviews: TrustReview[] = []
  for (const setting of settings) reviews.push(await invoke<TrustReview>('extension_trust_review', { setting }))
  return reviews
}
export async function confirmTrust(reviews: TrustReview[]): Promise<void> {
  if (!reviews.length || reviews.length > 64) throw new Error('请重新检查来源后确认')
  await invoke('extension_trust_confirm_group', { requests: reviews.map(review => ({ review_id: review.review_id, fingerprint: review.fingerprint })) })
}
