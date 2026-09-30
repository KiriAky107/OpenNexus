import { defineStore } from 'pinia'
import { ref, watch, computed } from 'vue'
import { useWorkspaceStore } from './workspace'
import { VaultLinkIndex } from '@/services/vaultLinkIndex'
import { readFileContent } from '@/services/workspaceService'
export const useVaultLinksStore = defineStore('vaultLinks', () => {
  const workspace = useWorkspaceStore(), index = new VaultLinkIndex()
  const revision = ref(0), busy = ref(false)
  let sequence = 0
  async function refresh() {
    const version = ++sequence, vault = workspace.vaultId
    busy.value = true
    try {
      if (await index.update(workspace.fileTree, readFileContent) && vault === workspace.vaultId) revision.value++
    } finally { if (version === sequence) busy.value = false }
  }
  watch(() => workspace.vaultId, () => { sequence++; index.reset(); revision.value++; busy.value = false })
  const references = computed(() => { void revision.value; return index.references })
  const errors = computed(() => { void revision.value; return index.errors })
  return { references, errors, busy, refresh }
})
