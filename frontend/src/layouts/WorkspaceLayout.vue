<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useWorkspaceStore } from '../stores/workspace'
import { api } from '../api/platform'

const router = useRouter()
const route = useRoute()
const workspace = useWorkspaceStore()
const instance = ref(null)
const developer = computed(() => route.path.startsWith('/developer'))
const items = computed(() => developer.value ? [
  { label: '知识类型', caption: '当前启用与扩展', icon: '◇', to: '/developer/knowledge-types' },
  { label: '标准流程', caption: '公共前置处理', icon: '⇢', to: '/developer/standard-pipelines' },
  { label: '模板', caption: '单产出 / 多产出', icon: '▦', to: '/developer/flow-templates' },
  { label: 'DataFlow 调试台', caption: 'V7 运行诊断（只读）', icon: '◎', to: '/developer/dataflow-debug' },
] : [
  { label: '工作台', caption: '整体运行状态', icon: '⌂', to: '/business/dashboard' },
  { label: '文档管理', caption: '文档库与原始资料', icon: '▣', to: '/business/documents' },
  { label: '处理任务', caption: '运行、日志与重试', icon: '⇄', to: '/business/jobs' },
  { label: '知识库', caption: '文 / 问 / 图', icon: '◆', to: '/business/knowledge' },
  { label: '项目发布', caption: 'Deployment 与知识授权', icon: '✓', to: '/business/authorization' },
  { label: '机构发布部署', caption: '多项目 Seed / Release / Update', icon: '⇲', to: '/institution-deployments/new' },
  ...(instance.value?.instance_mode === 'local' ? [{ label: '本地初始化', caption: '组件、自检与导入', icon: '◉', to: '/local/initialization' }] : []),
])
const current = computed(() => items.value.find(item => route.path === item.to || route.path.startsWith(`${item.to}/`) ||
  item.to === '/institution-deployments/new' && route.path.startsWith('/institution-deployments/'))?.label || items.value[0].label)
function switchTo(name) {
  workspace.switchTo(name)
  router.push(name === 'business' ? '/business/dashboard' : '/developer/knowledge-types')
}
onMounted(async () => { try { instance.value = await api.instance() } catch (_) { instance.value = null } })
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><div class="brandmark">DF</div><div><h1>DataForge</h1><small>统一知识平台 · V7</small></div></div>
      <div class="workspace-switch">
        <button :class="{ active: !developer }" @click="switchTo('business')">业务工作区</button>
        <button :class="{ active: developer }" @click="switchTo('developer')">流程开发区</button>
      </div>
      <p class="nav-group-title">{{ developer ? '流程开发区' : '业务工作区' }}</p>
      <nav>
        <RouterLink v-for="item in items" :key="item.to" :to="item.to" class="nav-item">
          <span class="nav-icon">{{ item.icon }}</span><span><b>{{ item.label }}</b><small>{{ item.caption }}</small></span>
        </RouterLink>
      </nav>
    </aside>
    <main>
      <header class="topbar"><span class="crumb">{{ developer ? '流程开发区' : '业务工作区' }} / {{ current }}</span><div class="page-actions"><span class="badge blue">{{ instance?.display_name || 'DataForge' }}{{ instance?.deployment_flavor==='institution_private' ? ' · 机构本地' : '' }}</span><span class="admin">Admin</span></div></header>
      <div class="content"><RouterView /></div>
    </main>
  </div>
</template>
