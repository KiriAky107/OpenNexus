import { ref } from 'vue'
const visible = ref(false)
export function useExperimentPane() {
  return { visible, open: () => { visible.value = true }, close: () => { visible.value = false } }
}
