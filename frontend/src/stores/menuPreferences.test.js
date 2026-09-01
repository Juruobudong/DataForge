import assert from 'node:assert/strict'
import test from 'node:test'

import { loadMenuPreference, MENU_PREFERENCE_KEY, mergeMenuPreference } from './menuPreferences.js'
import { BUSINESS_MENU_GROUPS, BUSINESS_MENU_REGISTRY, businessMenuRegistry, groupMenuRegistry } from '../constants/workspaceMenus.js'

test('旧偏好自动插入新菜单并忽略删除和重复 key', () => {
  const merged = mergeMenuPreference(BUSINESS_MENU_REGISTRY, { business: {
    order: ['dashboard', 'knowledge', 'authorization', 'removed', 'knowledge', 'documents', 'jobs', 'institution-deployments'],
    hidden: [],
  } })
  assert.deepEqual(merged.order, [
    'dashboard', 'knowledge', 'documents', 'jobs',
    'authorization', 'institution-deployments', 'milvus-targets', 'vector-storage',
  ])
})

test('默认业务菜单按知识生产、发布交付和平台运维固定分组', () => {
  assert.deepEqual(
    groupMenuRegistry(mergeMenuPreference(BUSINESS_MENU_REGISTRY).visible).map(group => [group.label, group.children.map(item => item.key)]),
    [
      ['知识生产', ['dashboard', 'documents', 'jobs', 'knowledge']],
      ['发布与交付', ['authorization', 'institution-deployments']],
      ['平台运维', ['milvus-targets', 'vector-storage']],
    ],
  )
  assert.equal(BUSINESS_MENU_GROUPS.length, 3)
})

test('required 菜单不能隐藏，普通菜单保留隐藏状态', () => {
  const merged = mergeMenuPreference(BUSINESS_MENU_REGISTRY, { business: {
    order: BUSINESS_MENU_REGISTRY.map(item => item.key), hidden: ['dashboard', 'jobs'],
  } })
  assert.deepEqual(merged.hidden, ['jobs'])
  assert.equal(merged.visible.some(item => item.key === 'dashboard'), true)
  assert.equal(merged.visible.some(item => item.key === 'jobs'), false)
})

test('local-only 菜单只在 local registry 中出现', () => {
  assert.equal(mergeMenuPreference(businessMenuRegistry('central')).order.includes('local-initialization'), false)
  assert.equal(mergeMenuPreference(businessMenuRegistry('local')).order.includes('milvus-targets'), false)
  assert.equal(mergeMenuPreference(businessMenuRegistry('local')).order.at(-1), 'local-initialization')
})

test('隐藏整组菜单时侧栏不保留空分组标题', () => {
  const merged = mergeMenuPreference(BUSINESS_MENU_REGISTRY, { business: {
    order: BUSINESS_MENU_REGISTRY.map(item => item.key), hidden: ['milvus-targets', 'vector-storage'],
  } })
  assert.deepEqual(groupMenuRegistry(merged.visible).map(group => group.label), ['知识生产', '发布与交付'])
})

test('损坏 localStorage 回退为 null', () => {
  const storage = { getItem: key => key === MENU_PREFERENCE_KEY ? '{broken' : null }
  assert.equal(loadMenuPreference(storage), null)
})
