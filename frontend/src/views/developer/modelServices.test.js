import test from 'node:test'
import assert from 'node:assert/strict'
import { blankServiceForm, editServiceForm, serviceStatus } from './modelServices.js'

test('model service presentation keeps secrets write-only and maps statuses', () => {
  assert.equal(serviceStatus({ is_enabled: true, last_check_status: 'healthy' }).label, '正常')
  assert.equal(serviceStatus({ is_enabled: false, last_check_status: 'healthy' }).label, '已停用')
  assert.equal(blankServiceForm('embedding').dimension, 768)
  const form = editServiceForm({ id: 'x', serving_code: 'qwen', credential_configured: true, last_check_status: 'healthy' }, 'model')
  assert.equal(form.api_key, '')
  assert.equal('credential_configured' in form, false)
})
