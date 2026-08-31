# 不可变知识资产版本

> 当前状态：已实现架构，更新于 2026-08-22。

## 逻辑知识与物理资产

DataForge 将用户编辑与查看的逻辑知识库和供检索、发布使用的物理向量资产分开：

| 层 | 实体 | 作用 |
| --- | --- | --- |
| 逻辑当前态 | `knowledge_libraries`、`knowledge_items` | 保存当前业务知识、来源与变更历史 |
| 不可变发布资产 | `knowledge_asset_versions` | 固化一次完整向量构建结果及其验证状态 |
| 物理存储 | `kl_<knowledge_library_id>__v<asset_version_no>` | 在冻结 Profile 对应 Collection 中隔离每个版本 |
| 发布引用 | `project_route_version_assets` | 固化 RouteVersion 实际使用的 AssetVersion |

## 版本构建

1. 用户在知识库显式执行全量发布，或运行离线导入工具，为知识库分配新的版本号和候选 Partition；Knowledge Job 本身不再自动发布。
2. Text/QA 只冻结当前 approved 且 Evidence 合规的完整集合，并要求没有 pending；Graph 与扩展类型冻结全部 active。每个 KnowledgeAssetItem 固化知识审核快照。
3. 任务只向候选 Partition 写入，稳定向量 ID 由 Profile、知识库和知识身份确定。
4. 系统核对记录数、内容摘要、Partition load 和冒烟检索；全部通过后版本才成为 Ready，失败候选不改变旧 Ready。
5. RouteVersion 冻结时把每个授权知识库解析到明确的 Ready AssetVersion；RoutingSnapshot 保存该版本化 Partition，而不是逻辑前缀。知识库发布不自动切换 Routing。

运行中的 Ready Partition 不执行 `reset_partition()`，也不被后续构建原地覆盖。新数据要么更新逻辑当前态后生成新版本，要么在离线导入中生成新的候选版本。

## 运行时库存与复核

- 业务“向量存储”库存实时连接 Milvus，并与 AssetVersion、Contract、Routing、Release、Candidate、Migration 和 GC 引用合并；不建立独立库存表。
- 普通刷新只读取 Collection/Partition metadata 与 stats，不遍历向量。显式 verify 复用同一稳定排序 digest 算法，并把最近 observed count/digest、结果、时间和错误写入 AssetVersion 的附加字段。
- 最近复核结果只用于运维可见性；它不会覆盖 `item_count/content_digest`、改变 Ready、切换 Routing 或触发删除。
- load/release 只允许 ownership marker、Storage Contract、资产映射和版本化 Partition 名全部通过的 DataForge-owned 资源；GC 仍是独立显式 Job。

## Collection 与 Contract

- Profile Revision 冻结 `managed` 或 `external` Collection 策略，Storage Contract Revision 冻结字段、Embedding、维度、度量与索引，其规范摘要为 `storage_spec_hash`。
- DataForge-owned Collection 由 Provisioner 按 Contract 幂等创建或核验 ownership marker；external Collection 只校验与映射。
- 相同 Contract 默认仍使用独立受管 Collection；只有管理员显式选择哈希一致的 ready 受管登记时才复用。
- 不论是否复用 Collection，每个知识库的每个资产版本都使用独立版本化 Partition。

## 引用保护与回收

- RouteVersion、ImportedRouteCandidate、Release Snapshot 或迁移任务引用某个 AssetVersion 时禁止回收。
- 无引用版本至少保留 30 天，并且每个知识库至少保留最近两个 Ready 版本。
- GC 只能通过显式 Job 执行，默认 dry-run；它与受管 Collection 整库删除是两个独立流程。
- 知识库删除只处理该库的 Partition；external、legacy、客户或 ownership 无法证明的 Collection 不由 DataForge 删除。

## 来源与关联

- 实现：`src/dataforge/v7/models.py`、`store.py`、`vector.py`、`provisioning.py`、`migration/`。
- 详细事实：[`wiki/pages/domain-model.md`](../../wiki/pages/domain-model.md)、[`wiki/pages/system-architecture.md`](../../wiki/pages/system-architecture.md)。
- 决策：[ADR-002 不可变资产版本](../adr/ADR-002-immutable-asset-version.md)、[ADR-003 RoutingSnapshot](../adr/ADR-003-routing-snapshot.md)。
