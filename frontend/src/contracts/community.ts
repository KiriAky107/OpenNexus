/** 社区协议 v1；签名元数据与安装运行状态分离。 */
export type PackageKind = 'theme' | 'skill' | 'plugin' | 'mcp' | 'persona' | 'template' | 'model'
export interface CommunityKey { key_id: string; namespace: string; public_key: string; revoked: boolean }
export interface CommunitySource { id: string; url: string; enabled: boolean; keys: CommunityKey[]; fetchedAt?: string }
export interface CommunityRelease {
  schema_version: 1; namespace: string; package_id: string; type: PackageKind; version: string
  name: string; author_id: string; license: string; description: string; sha256: string; size: number
  platforms: string[]; architectures: string[]; min_app_version: string; max_app_version: string | null
  dependencies: Record<string, string>; permissions: string[]; changelog: string; published_at: string
  key_id: string; signature: string; release_id: string; withdrawn: boolean; download_path: string
}
export interface CommunityCatalog { schema_version: 1; items: CommunityRelease[]; total: number; offset: number }
