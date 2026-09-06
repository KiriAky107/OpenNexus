import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ChatPreferences { persona: string; presetDialogue: string; aiAvatar: string; userAvatar: string }
const storageKey = 'chat-persona-preferences-v1'
const empty = (): ChatPreferences => ({ persona: '', presetDialogue: '', aiAvatar: '', userAvatar: '' })
export function validAvatar(value: unknown): value is string {
  return typeof value === 'string' && (value === '' || (value.length <= 710000 && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value)))
}
function validate(value: ChatPreferences) {
  if (typeof value.persona !== 'string' || typeof value.presetDialogue !== 'string' || value.persona.length > 16000 || value.presetDialogue.length > 32000 || !validAvatar(value.aiAvatar) || !validAvatar(value.userAvatar)) throw new Error('人设、预设对话或头像格式无效 / Invalid chat preferences')
  return {persona:value.persona, presetDialogue:value.presetDialogue, aiAvatar:value.aiAvatar, userAvatar:value.userAvatar}
}
export const useChatPreferences = defineStore('chatPreferences', () => {
  const settings = ref<ChatPreferences>(empty())
  try { const stored = localStorage.getItem(storageKey); if (stored) settings.value = validate(JSON.parse(stored)) } catch { /* Invalid or unavailable local settings use defaults. */ }
  function save(value: ChatPreferences) {
    const next = validate(value)
    localStorage.setItem(storageKey, JSON.stringify(next))
    settings.value = next
  }
  return { settings, save }
})
