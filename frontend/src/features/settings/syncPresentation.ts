import { localeTag, t } from '@/i18n'
export function syncBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${(value / 1024 ** 3).toFixed(2)} GiB`
}
export function syncTime(seconds: number | null | undefined) {
  const date = new Date((seconds ?? NaN) * 1000)
  return Number.isFinite(date.getTime()) ? date.toLocaleString(localeTag()) : t('尚无记录', 'No record yet')
}
export function syncError(cause: unknown) {
  const raw = cause instanceof Error ? cause.message : String(cause)
  const code = /^[A-Z][A-Z0-9_]{0,79}$/.test(raw) ? raw : 'SYNC_FAILED'
  let message: string
  switch (code) {
    case 'SYNC_BUSY': message = t('同步任务正在处理，请稍后重试。', 'A sync task is in progress. Try again shortly.'); break
    case 'CREDENTIALS_LOCKED': case 'CREDENTIALS_UNAVAILABLE': message = t('请先解锁本机凭据保险库。', 'Unlock the device credential vault first.'); break
    case 'SYNC_LOGIN_REQUIRED': case 'SESSION_EXPIRED': case 'AUTH_FAILED': message = t('登录或设备授权已失效，请重新登录。', 'Sign-in or device authorization expired. Sign in again.'); break
    case 'QUOTA_EXCEEDED': message = t('远端存储配额不足，请调整配额或清理远端历史后重试。', 'Remote storage quota is insufficient. Adjust it or clean up remote history, then retry.'); break
    case 'SYNC_NETWORK_ERROR': message = t('连接服务失败，请检查网络与服务地址。', 'Cannot reach the service. Check the network and service address.'); break
    case 'SYNC_IO_FAILED': case 'SYNC_SPOOL_FAILED': message = t('本地读写失败，请检查磁盘空间和文件访问权限。', 'Local file access failed. Check disk space and file permissions.'); break
    case 'PROTOCOL_INCOMPATIBLE': message = t('服务协议不兼容，请核对桌面与服务版本。', 'The service protocol is incompatible. Check desktop and service versions.'); break
    case 'SYNC_ENCRYPTION_INCOMPATIBLE': message = t('服务声明的内容加密格式不兼容，已停止同步。请核对服务配置。', 'The declared content encryption format is incompatible. Sync stopped. Check the service configuration.'); break
    case 'SYNC_OBJECT_CORRUPT': case 'SYNC_SPOOL_CORRUPT': case 'HASH_MISMATCH': message = t('内容校验失败，文件没有作为成功传输处理。', 'Content verification failed. The file was not accepted as a successful transfer.'); break
    case 'REVISION_CONFLICT': case 'SYNC_CONFLICT_CHANGED': message = t('文件修订已变化，请刷新并审核冲突。', 'The file revision changed. Refresh and review the conflict.'); break
    case 'SYNC_CANCELLED': message = t('本轮同步已停止，确认过的上传偏移可在重试时恢复。', 'This sync cycle stopped. Retry can resume confirmed upload offsets.'); break
    case 'SYNC_BINDING_CHANGED': case 'VAULT_PERMISSION_CHANGED': case 'VAULT_CHANGED': message = t('知识库或同步绑定已变化，请刷新页面。', 'The vault or sync binding changed. Refresh the page.'); break
    case 'SYNC_CURRENT_DEVICE': message = t('请使用退出登录来停止当前设备的访问。', 'Use sign out to stop this device’s access.'); break
    case 'SYNC_DEVICE_NOT_FOUND': message = t('设备已撤销或不存在，请刷新设备列表。', 'The device was revoked or no longer exists. Refresh the device list.'); break
    default: message = t('同步操作未完成，请检查服务状态后重试。', 'Sync did not complete. Check service status and retry.')
  }
  return `${message} (${code})`
}
