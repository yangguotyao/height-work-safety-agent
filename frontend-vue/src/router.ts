import { createRouter, createWebHashHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'

export default createRouter({
  history: createWebHashHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/', component: DashboardView },
    {
      path: '/audit',
      component: () => import('./views/AuditView.vue'),
      meta: { manager: true }
    },
    { path: '/worker', component: () => import('./views/WorkerView.vue') },
    { path: '/risk', component: () => import('./views/DynamicRiskView.vue') },
    {
      path: '/hazards',
      component: () => import('./views/HazardInspectionView.vue'),
      meta: { manager: true }
    },
    {
      path: '/rectification',
      component: () => import('./views/RectificationView.vue'),
      meta: { manager: true }
    },
    { path: '/agent', component: () => import('./views/AgentView.vue') },
    { path: '/knowledge', redirect: '/' },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})
