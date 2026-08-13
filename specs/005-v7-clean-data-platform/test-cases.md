# Test Cases：V7 全新数据平台

| ID | 场景 | 自动化状态 |
| --- | --- | --- |
| TC-001 | Alembic 空库和 V7 revision guard | PASSED |
| TC-002 | 上传、替换、删除、重试、状态筛选和版本溯源 | PASSED |
| TC-003 | 文/问/图当前态 Diff 与多来源 | PASSED |
| TC-004 | 新上传文件经 Runner 生成知识并排队 Vector Sync | PASSED |
| TC-005 | 四 Collection、指定 V7 Partition 加载/释放/搜索、容量阈值 | PASSED（fake Milvus） |
| TC-006 | Vector Ready、授权、原子发布和回滚 | PASSED |
| TC-007 | 不调用旧 Collection 删除，也不使用 `org_code → Partition` 旧路由 | PASSED |
| TC-008 | 旧数据库清理命令已移除；空库和已有 V7 schema 仅通过常规 Alembic 升级 | PASSED（CLI 帮助和 TC-001） |
| TC-009 | 任务批量停止、重试、日志与已形成知识的删除保护 | PASSED |
| TC-010 | Compose + MySQL/MinIO/Milvus/Embedding 真机验收 | BLOCKED：部署环境 |
| TC-011 | Draft/已发布路由引用阻止删除；无引用库异步删除并仅清理 V7 Partition | PASSED（fake Milvus） |
| TC-012 | 来源 Evidence、历史 Diff 兼容与新增 before/after 快照 | PASSED |
| TC-013 | 受控模板修订、默认项、归档、样例运行无持久化和任务修订固定 | PASSED |
| TC-014 | 图谱实体搜索、1/2 跳邻居、关系证据聚合与范围限制 | PASSED |
| TC-015 | 知识库、项目授权、模板、图谱和工作台的前端构建与关键交互 | PASSED（Vite build） |
| TC-016 | 九个 V7 页面共享原型视觉系统、响应式应用壳与真实功能入口 | PASSED（Vite build；页面静态核对） |
| TC-017 | DataFlow 调试台固定导航、向量诊断收口和无 Milvus 删除失败重试 | PASSED（11 项 V7 pytest + Vite build） |
