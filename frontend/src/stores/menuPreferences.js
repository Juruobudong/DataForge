import { defineStore } from 'pinia'

export const MENU_PREFERENCE_KEY = 'dataforge.workspace-menu.v1'

function validBusinessPreference(value) {
  return value && typeof value === 'object' && value.business && typeof value.business === 'object'
    && Array.isArray(value.business.order) && Array.isArray(value.business.hidden)
}

export function loadMenuPreference(storage = globalThis.localStorage) {
  if (!storage) return null
  try {
    const parsed = JSON.parse(storage.getItem(MENU_PREFERENCE_KEY) || 'null')
    return validBusinessPreference(parsed) ? parsed : null
  } catch (_) {
    return null
  }
}

export function mergeMenuPreference(registry = [], preference = null) {
  const known = new Map(registry.map(item => [item.key, item]))
  const configured = validBusinessPreference(preference) ? preference.business : { order: [], hidden: [] }
  const groupKeys = [...new Set(registry.map(item => item.groupKey || 'default'))]
  const order = groupKeys.flatMap(groupKey => {
    const registryKeys = registry.filter(item => (item.groupKey || 'default') === groupKey).map(item => item.key)
    const preferred = configured.order.filter(key => known.has(key) && registryKeys.includes(key))
    const grouped = [...new Set(preferred)]
    for (let registryIndex = 0; registryIndex < registryKeys.length; registryIndex += 1) {
      const key = registryKeys[registryIndex]
      if (grouped.includes(key)) continue
      let inserted = false
      for (let index = registryIndex - 1; index >= 0; index -= 1) {
        const predecessor = grouped.indexOf(registryKeys[index])
        if (predecessor >= 0) {
          grouped.splice(predecessor + 1, 0, key)
          inserted = true
          break
        }
      }
      if (!inserted) {
        for (let index = registryIndex + 1; index < registryKeys.length; index += 1) {
          const successor = grouped.indexOf(registryKeys[index])
          if (successor >= 0) {
            grouped.splice(successor, 0, key)
            inserted = true
            break
          }
        }
      }
      if (!inserted) grouped.push(key)
    }
    return grouped
  })
  const hidden = [...new Set(configured.hidden)].filter(key => known.has(key) && !known.get(key).required)
  const items = order.map(key => ({ ...known.get(key), hidden: hidden.includes(key) }))
  return { order, hidden, items, visible: items.filter(item => !item.hidden) }
}

export const useMenuPreferencesStore = defineStore('menuPreferences', {
  state: () => ({ preference: null, loaded: false }),
  actions: {
    hydrate(storage = globalThis.localStorage) {
      this.preference = loadMenuPreference(storage)
      this.loaded = true
    },
    saveBusiness(order, hidden, storage = globalThis.localStorage) {
      this.preference = { business: { order: [...order], hidden: [...hidden] } }
      try { storage?.setItem(MENU_PREFERENCE_KEY, JSON.stringify(this.preference)) } catch (_) { /* read-only storage */ }
    },
    resetBusiness(storage = globalThis.localStorage) {
      this.preference = null
      try { storage?.removeItem(MENU_PREFERENCE_KEY) } catch (_) { /* read-only storage */ }
    },
  },
})
