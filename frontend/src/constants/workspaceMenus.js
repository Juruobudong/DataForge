export const BUSINESS_MENU_GROUPS = [
  { key: 'knowledge-production', label: '知识生产', group: true, children: [
    { key: 'dashboard', label: '工作台', caption: '整体运行状态', icon: '⌂', to: '/business/dashboard', required: true },
    { key: 'documents', label: '文档管理', caption: '文档库与原始资料', icon: '▣', to: '/business/documents' },
    { key: 'jobs', label: '处理任务', caption: '运行、日志与重试', icon: '⇄', to: '/business/jobs' },
    { key: 'knowledge', label: '知识库', caption: '文 / 问 / 图', icon: '◆', to: '/business/knowledge' },
  ] },
  { key: 'publishing-delivery', label: '发布与交付', group: true, children: [
    { key: 'authorization', label: '项目发布', caption: 'Deployment 与知识授权', icon: '✓', to: '/business/authorization' },
    { key: 'institution-deployments', label: '机构发布部署', caption: '多项目 Seed / Release / Update', icon: '⇲', to: '/institution-deployments/new', activePrefix: '/institution-deployments/' },
  ] },
  { key: 'platform-operations', label: '平台运维', group: true, children: [
    { key: 'milvus-targets', label: 'Milvus 服务', caption: '服务注册与连接验证', icon: '◉', to: '/business/milvus-targets' },
    { key: 'vector-storage', label: '向量存储', caption: 'Collection / Partition', icon: '◈', to: '/business/vector-storage' },
  ] },
]

export const LOCAL_BUSINESS_MENU_ITEM = {
  key: 'local-initialization', groupKey: 'platform-operations', groupLabel: '平台运维', label: '本地初始化', caption: '组件、自检与导入', icon: '◉', to: '/local/initialization',
}

export const DEVELOPER_MENU_REGISTRY = [
  { key: 'flow-development', label: '流程开发', group: true, children: [
    { key: 'flow-templates', label: '知识流程', caption: '标准配置 / 高级编排', icon: '▦', to: '/developer/flow-templates' },
    { key: 'dataflow-debug', label: '运行调试', caption: '运行诊断', icon: '◎', to: '/developer/dataflow-debug' },
  ] },
  { key: 'capability-configuration', label: '能力配置', group: true, children: [
    { key: 'standard-pipelines', label: '文档预处理', caption: '解析、清洗与分块', icon: '⇢', to: '/developer/standard-pipelines' },
    { key: 'model-services', label: '模型服务', caption: '运行时资源注册中心', icon: '✦', to: '/developer/model-services' },
  ] },
  { key: 'developer-resources', label: '开发者资源', group: true, children: [
    { key: 'operator-catalog', label: '算子组件', caption: 'Registry / Contract', icon: '⊞', to: '/developer/operator-catalog' },
    { key: 'subflows', label: '可复用子流程', caption: '草稿 / Revision', icon: '◈', to: '/developer/subflows' },
  ] },
]

export function flattenMenuRegistry(items) {
  return items.flatMap(item => item.group
    ? (item.children || []).map(child => ({ ...child, groupKey: item.key, groupLabel: item.label }))
    : [item])
}

export const BUSINESS_MENU_REGISTRY = flattenMenuRegistry(BUSINESS_MENU_GROUPS)

export function groupMenuRegistry(items, groups = BUSINESS_MENU_GROUPS) {
  return groups.map(group => ({
    ...group,
    children: items.filter(item => item.groupKey === group.key),
  })).filter(group => group.children.length)
}

export function businessMenuRegistry(instanceMode = 'central') {
  return instanceMode === 'local'
    ? [...BUSINESS_MENU_REGISTRY.filter(item => item.key !== 'milvus-targets'), LOCAL_BUSINESS_MENU_ITEM]
    : [...BUSINESS_MENU_REGISTRY]
}

export function menuItemActive(item, path) {
  return path === item.to || path.startsWith(`${item.to}/`) || Boolean(item.activePrefix && path.startsWith(item.activePrefix))
}
