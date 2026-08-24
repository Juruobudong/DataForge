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
  const order = []
  for (const key of configured.order) {
    if (known.has(key) && !order.includes(key)) order.push(key)
  }
  const registryKeys = registry.map(item => item.key)
  for (let registryIndex = 0; registryIndex < registryKeys.length; registryIndex += 1) {
    const key = registryKeys[registryIndex]
    if (order.includes(key)) continue
    let inserted = false
    for (let index = registryIndex - 1; index >= 0; index -= 1) {
      const predecessor = order.indexOf(registryKeys[index])
      if (predecessor >= 0) {
        order.splice(predecessor + 1, 0, key)
        inserted = true
        break
      }
    }
    if (!inserted) {
      for (let index = registryIndex + 1; index < registryKeys.length; index += 1) {
        const successor = order.indexOf(registryKeys[index])
        if (successor >= 0) {
          order.splice(successor, 0, key)
          inserted = true
          break
        }
      }
    }
    if (!inserted) order.push(key)
  }
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
