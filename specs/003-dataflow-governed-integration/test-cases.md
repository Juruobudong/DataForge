# Test Cases: 文档知识生产链路

| ID | Scenario | Level | Expected result |
| --- | --- | --- | --- |
| TC-001 | 默认流程资格与排序 | unit/API | 仅符合全部门槛的版本可设默认且始终置顶。 |
| TC-002 | 上传与选项 | API | 格式、大小和 CSV 映射受校验，输出项由默认流程限制。 |
| TC-003 | Runner 分支 | integration | text/qa/graph 产生独立结果，未选分支为 skipped。 |
| TC-004 | 任务恢复 | integration | 租约过期后可幂等恢复，事件可从 Last-Event-ID 续读。 |
| TC-005 | 文档替换删除 | integration | 校验失败保留旧数据；删除清除对象和知识，仅留审计。 |
| TC-006 | 前端工作区 | build/manual | 固定菜单、整体可点击文档库卡片、默认流程和动态上传选项正确。 |
| TC-007 | Compose | e2e | 六服务启动，管理员登录后可完成上传到结果查看。 |
