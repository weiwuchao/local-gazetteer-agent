import { defineStore } from 'pinia'

export interface Region {
  id: string
  name: string
  era_note: string
  doc_count: number
  has_published: boolean
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    userId: '',
    username: '',
    role: 'user' as 'admin' | 'user',
    regions: [] as Region[],
    regionId: '',
    accessToken: localStorage.getItem('access_token') ?? '',
  }),
  getters: {
    isAuthenticated: (s) => Boolean(s.accessToken),
    isAdmin: (s) => s.role === 'admin',
  },
  actions: {
    async login(_username: string, _password: string) {
      // TODO: POST /auth/login → 保存令牌 → GET /auth/me
      throw new Error('not implemented')
    },
    logout() {
      this.accessToken = ''
      this.username = ''
      localStorage.removeItem('access_token')
    },
  },
})
