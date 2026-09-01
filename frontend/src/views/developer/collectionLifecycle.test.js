import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultCollectionName, managedCollectionCanRequestDelete } from '../../components/governance/collectionLifecycle.js'

test('extension type defaults to the normalized managed collection name', () => {
  assert.equal(defaultCollectionName('clinical-note'), 'dataforge_clinical_note_knowledge')
  assert.equal(defaultCollectionName(''), '')
})

test('only deletable managed collection states expose a delete request', () => {
  assert.equal(managedCollectionCanRequestDelete({ status: 'ready' }), true)
  assert.equal(managedCollectionCanRequestDelete({ status: 'delete_failed' }), true)
  assert.equal(managedCollectionCanRequestDelete({ status: 'planned' }), false)
  assert.equal(managedCollectionCanRequestDelete(null), false)
})
