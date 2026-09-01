<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useWorkspaceStore } from '../stores/workspace'
import { useMenuPreferencesStore, mergeMenuPreference } from '../stores/menuPreferences'
import { businessMenuRegistry, DEVELOPER_MENU_REGISTRY, flattenMenuRegistry, groupMenuRegistry, menuItemActive } from '../constants/workspaceMenus'
import MenuCustomizeDialog from '../components/MenuCustomizeDialog.vue'
import { api } from '../api/platform'

const router = useRouter()
const route = useRoute()
const workspace = useWorkspaceStore()
const menuPreferences = useMenuPreferencesStore()
const instance = ref(null)
const customizeOpen = ref(false)
const developer = computed(() => route.path.startsWith('/developer'))
const hideTopbar = computed(() => route.meta.hideTopbar === true)
const businessRegistry = computed(() => businessMenuRegistry(instance.value?.instance_mode))
const businessMenu = computed(() => mergeMenuPreference(businessRegistry.value, menuPreferences.preference))
const items = computed(() => developer.value ? DEVELOPER_MENU_REGISTRY : groupMenuRegistry(businessMenu.value.visible))
const currentRegistry = computed(() => developer.value ? flattenMenuRegistry(DEVELOPER_MENU_REGISTRY) : businessRegistry.value)
const current = computed(() => currentRegistry.value.find(item => menuItemActive(item, route.path))?.label || currentRegistry.value[0].label)
function switchTo(name) {
  workspace.switchTo(name)
  router.push(name === 'business' ? '/business/dashboard' : '/developer/flow-templates')
}
function saveMenu(value) {
  menuPreferences.saveBusiness(value.order, value.hidden)
  customizeOpen.value = false
}
onMounted(async () => {
  menuPreferences.hydrate()
  try { instance.value = await api.instance() } catch (_) { instance.value = null }
})
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
      <nav class="sidebar-nav">
        <template v-for="item in items" :key="item.key || item.to">
          <section v-if="item.group" class="sidebar-menu-group">
            <p>{{ item.label }}</p>
            <RouterLink v-for="child in item.children" :key="child.to" :to="child.to" class="nav-item group-child">
              <span class="nav-icon">{{ child.icon }}</span><span><b>{{ child.label }}</b><small>{{ child.caption }}</small></span>
            </RouterLink>
          </section>
          <RouterLink v-else :to="item.to" class="nav-item">
            <span class="nav-icon">{{ item.icon }}</span><span><b>{{ item.label }}</b><small>{{ item.caption }}</small></span>
          </RouterLink>
        </template>
      </nav>
      <div v-if="!developer" class="sidebar-footer"><button class="customize-menu-button" @click="customizeOpen=true">⚙ 自定义菜单</button></div>
    </aside>
    <main>
      <header v-if="!hideTopbar" class="topbar"><span class="crumb">{{ developer ? '流程开发区' : '业务工作区' }} / {{ current }}</span><div class="page-actions"><span class="badge blue">{{ instance?.display_name || 'DataForge' }}{{ instance?.deployment_flavor==='institution_private' ? ' · 机构本地' : '' }}</span><span class="admin">Admin</span></div></header>
      <div class="content"><RouterView /></div>
    </main>
    <MenuCustomizeDialog v-if="customizeOpen" :items="businessMenu.items" :defaults="businessRegistry" @close="customizeOpen=false" @save="saveMenu" />
  </div>
</template>
