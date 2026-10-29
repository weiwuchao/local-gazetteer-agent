import { defineStore } from 'pinia'

export interface Segment {
  segmentId: string
  status: 'pending' | 'confirmed' | 'rejected' | 'sensitive'
  raw: string
  polished: string
}

export const usePolishStore = defineStore('polish', {
  state: () => ({
    versionId: '',
    segments: [] as Segment[],
    selectedId: '',
  }),
  getters: {
    pending: (s) => s.segments.filter((x) => x.status === 'pending').length,
  },
  actions: {
    async load(_versionId: string) {
      // TODO: GET /admin/corpus/versions/{id}/segments
      throw new Error('not implemented')
    },
  },
})
