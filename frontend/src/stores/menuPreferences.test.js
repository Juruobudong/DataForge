import assert from 'node:assert/strict'
import test from 'node:test'

import { loadMenuPreference, MENU_PREFERENCE_KEY, mergeMenuPreference } from './menuPreferences.js'
import { BUSINESS_MENU_REGISTRY, businessMenuRegistry } from '../constants/workspaceMenus.js'

test('旧偏好自动插入新菜单并忽略删除和重复 key', () => {
  const merged = mergeMenuPreference(BUSINESS_MENU_REGISTRY, { business: {
    order: ['dashboard', 'knowledge', 'authorization', 'removed', 'knowledge', 'documents', 'jobs', 'institution-deployments'],
    hidden: [],
  } })
  assert.deepEqual(merged.order, [
    'dashboard', 'milvus-targets', 'knowledge', 'vector-storage', 'authorization',
    'documents', 'jobs', 'institution-deployments',
  ])
})

test('默认业务菜单将 Milvus 服务固定在工作台之后', () => {
  assert.deepEqual(
    mergeMenuPreference(BUSINESS_MENU_REGISTRY).order.slice(0, 2),
    ['dashboard', 'milvus-targets'],
  )
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

test('损坏 localStorage 回退为 null', () => {
  const storage = { getItem: key => key === MENU_PREFERENCE_KEY ? '{broken' : null }
  assert.equal(loadMenuPreference(storage), null)
})
