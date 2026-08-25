export const BUSINESS_MENU_REGISTRY = [
  { key: 'dashboard', label: '工作台', caption: '整体运行状态', icon: '⌂', to: '/business/dashboard', required: true },
  { key: 'documents', label: '文档管理', caption: '文档库与原始资料', icon: '▣', to: '/business/documents' },
  { key: 'jobs', label: '处理任务', caption: '运行、日志与重试', icon: '⇄', to: '/business/jobs' },
  { key: 'knowledge', label: '知识库', caption: '文 / 问 / 图', icon: '◆', to: '/business/knowledge' },
  { key: 'vector-storage', label: '向量存储', caption: 'Collection / Partition', icon: '◈', to: '/business/vector-storage' },
  { key: 'authorization', label: '项目发布', caption: 'Deployment 与知识授权', icon: '✓', to: '/business/authorization' },
  { key: 'institution-deployments', label: '机构发布部署', caption: '多项目 Seed / Release / Update', icon: '⇲', to: '/institution-deployments/new', activePrefix: '/institution-deployments/' },
]

export const LOCAL_BUSINESS_MENU_ITEM = {
  key: 'local-initialization', label: '本地初始化', caption: '组件、自检与导入', icon: '◉', to: '/local/initialization',
}

export const DEVELOPER_MENU_REGISTRY = [
  { key: 'model-services', label: '模型服务', caption: '运行时资源注册中心', icon: '✦', to: '/developer/model-services' },
  { key: 'standard-pipelines', label: '标准流程', caption: '公共前置处理', icon: '⇢', to: '/developer/standard-pipelines' },
  { key: 'flow-templates', label: '知识流程', caption: '单产出 / 多产出', icon: '▦', to: '/developer/flow-templates' },
  { key: 'operator-catalog', label: '能力组件', caption: '平台可组合能力', icon: '⊞', to: '/developer/operator-catalog' },
  { key: 'dataflow-debug', label: '运行调试', caption: '运行诊断（只读）', icon: '◎', to: '/developer/dataflow-debug' },
]

export function businessMenuRegistry(instanceMode = 'central') {
  return instanceMode === 'local'
    ? [...BUSINESS_MENU_REGISTRY, LOCAL_BUSINESS_MENU_ITEM]
    : [...BUSINESS_MENU_REGISTRY]
}

export function menuItemActive(item, path) {
  return path === item.to || path.startsWith(`${item.to}/`) || Boolean(item.activePrefix && path.startsWith(item.activePrefix))
}
