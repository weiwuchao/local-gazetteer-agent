import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
  { path: '/chat', component: () => import('./views/ChatView.vue') },
  { path: '/reports', component: () => import('./views/ReportsView.vue') },
  { path: '/corpus', component: () => import('./views/CorpusView.vue'), meta: { admin: true } },
  { path: '/polish', component: () => import('./views/PolishView.vue'), meta: { admin: true } },
  { path: '/trace', component: () => import('./views/TraceView.vue'), meta: { admin: true } },
  { path: '/spec', component: () => import('./views/SpecView.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) return '/login'
  // 与原型 go() 的兜底一致；真正的防线在接口（403）
  if (to.meta.admin && auth.role !== 'admin') return '/chat'
  return true
})

export default router
