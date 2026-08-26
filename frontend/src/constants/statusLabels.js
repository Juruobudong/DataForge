export const STATUS_LABELS = Object.freeze({
  draft: '草稿', frozen: '已冻结', published: '已发布', active: '启用', disabled: '已禁用',
  central: '中心在线运行', institution: '机构离线交付',
  test: '测试环境', production: '生产环境',
})

export const statusLabel = value => STATUS_LABELS[value] || value || '—'
