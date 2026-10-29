import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    toasts: [] as { id: number; text: string; kind: 'info' | 'error' }[],
    annotationMode: false,
    rolePreview: false,
  }),
  actions: {
    toast(text: string, kind: 'info' | 'error' = 'info') {
      const id = Date.now()
      this.toasts.push({ id, text, kind })
      setTimeout(() => {
        this.toasts = this.toasts.filter((t) => t.id !== id)
      }, 3000)
    },
  },
})
