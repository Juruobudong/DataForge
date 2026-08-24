export const SERVICE_STATUS_LABELS = {
  pending_configuration: '待配置', not_checked: '未检查', healthy: '正常',
}

export function serviceStatus(item) {
  if (!item?.is_enabled) return { label: '已停用', tone: 'amber' }
  const label = SERVICE_STATUS_LABELS[item?.last_check_status] || '异常'
  return { label, tone: item?.last_check_status === 'healthy' ? 'green' : item?.last_check_status === 'not_checked' ? 'blue' : item?.last_check_status === 'pending_configuration' ? 'amber' : 'red' }
}

export function blankServiceForm(kind) {
  return kind === 'model'
    ? { serving_code: '', name: '', serving_type: 'openai-compatible-chat', model_name: '', base_url: '', api_key: '', timeout_seconds: 120, max_retries: 2, max_tokens: 16384, disable_thinking: true, is_enabled: true, clear_credential: false }
    : { serving_code: '', name: '', provider_type: 'openai-compatible-embedding', model_name: '', base_url: '', api_key: '', dimension: 768, batch_size: 32, timeout_seconds: 120, max_retries: 2, is_enabled: true, clear_credential: false }
}

export function editServiceForm(item, kind) {
  const form = { ...blankServiceForm(kind), ...item, api_key: '', clear_credential: false }
  delete form.credential_configured
  delete form.last_check_status
  delete form.last_check_at
  delete form.last_check_latency_ms
  delete form.last_check_error
  delete form.last_observed_dimension
  delete form.created_at
  delete form.updated_at
  delete form.is_default
  delete form.id
  return form
}
