export const EDGE_MESSAGES = Object.freeze({
  EDGE_DIRECTION_INVALID: 'Edge 必须从 output port 指向 input port',
  EDGE_SELF_LOOP: '节点不能连接自身',
  EDGE_DUPLICATED: '相同端口之间已经存在连线',
  EDGE_WOULD_CREATE_CYCLE: '该连接会形成循环依赖',
  PORT_TYPE_MISMATCH: '端口数据类型不兼容',
  KNOWLEDGE_TYPE_MISMATCH: '知识类型不兼容',
  GRAPH_MODE_MISMATCH: '图谱模式不兼容',
  INPUT_PORT_ALREADY_CONNECTED: '该输入端口已经有上游节点',
  SOURCE_NODE_NO_OUTPUT: '来源节点或输出端口不存在',
  TARGET_NODE_NO_INPUT: '目标节点或输入端口不存在',
  INPUT_NODE_CANNOT_HAVE_INCOMING: 'INPUT 节点不允许存在 Incoming Edge',
  SINK_NODE_CANNOT_HAVE_OUTGOING: 'Knowledge Sink 不允许作为上游节点',
  OPERATOR_CONTRACT_MISMATCH: '无法从当前 Flow 上下文解析端口契约',
  FLOW_DSL_VERSION_UNSUPPORTED: '当前 Flow DSL 版本不支持此连接规则',
})

export function edgeMessage(code, fallback = '') {
  return fallback || EDGE_MESSAGES[code] || '无法创建连接'
}
