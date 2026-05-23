# 企业内部知识库 Agent 平台接口设计文档 v1

## 1. 文档信息
- 文档名称：`接口设计文档 v1`
- 项目代号：`KAP`
- 适用阶段：一期 MVP 接口评审与前后端联调

## 2. 设计原则
- 资源化优先：优先采用 REST 风格设计。
- 任务化优先：长任务统一返回任务单模型。
- 可追溯优先：回答、报告、Wiki 需要 evidence/citation。
- 权限前置：所有接口都基于身份和资源授权。
- 稳定优先：先保证结构统一，再逐步演进到更细化 OpenAPI。

## 3. 基本约定
- Base URL：`/api/v1`
- 鉴权方式：`Authorization: Bearer <token>`
- 请求头建议：
- `X-Request-Id`
- `X-Trace-Id`
- 响应格式统一为：

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req_001"
}
```

## 4. 通用错误码
- `OK`
- `INVALID_ARGUMENT`
- `UNAUTHORIZED`
- `FORBIDDEN`
- `NOT_FOUND`
- `CONFLICT`
- `RATE_LIMITED`
- `DATASET_NOT_ALLOWED`
- `QUERY_TEMPLATE_REQUIRED`
- `DOCUMENT_PROCESSING_FAILED`
- `REPORT_GENERATION_FAILED`
- `WIKI_COMPILE_FAILED`
- `UPSTREAM_TIMEOUT`
- `INTERNAL_ERROR`

## 5. 核心对象

### 5.1 User
```json
{
  "id": "u_001",
  "name": "Terry",
  "email": "terry@example.com",
  "department_id": "dept_ops",
  "roles": ["employee", "analyst"]
}
```

### 5.2 Knowledge Base
```json
{
  "id": "kb_001",
  "name": "运营知识库",
  "status": "active",
  "default_retrieval_strategy": "hybrid"
}
```

### 5.3 Document
```json
{
  "id": "doc_001",
  "kb_id": "kb_001",
  "title": "差旅报销管理制度.pdf",
  "doc_type": "pdf",
  "status": "indexed"
}
```

### 5.4 Dataset
```json
{
  "id": "ds_sales_orders",
  "name": "销售订单主题数据集",
  "dataset_type": "sql_view",
  "security_level": "internal"
}
```

### 5.5 Citation
```json
{
  "source_type": "document_chunk",
  "source_id": "chunk_001",
  "document_id": "doc_001",
  "document_title": "差旅报销管理制度.pdf",
  "page_no": 4,
  "section_path": "第三章/住宿标准",
  "excerpt": "上海住宿标准不超过 600 元/晚"
}
```

### 5.6 Report
```json
{
  "id": "report_001",
  "template_code": "sales_funnel_report",
  "title": "2026W19 销售漏斗分析报告",
  "status": "reviewing"
}
```

### 5.7 Wiki Page
```json
{
  "id": "wiki_001",
  "slug": "sales-funnel-analysis",
  "title": "销售漏斗分析专题",
  "page_type": "topic",
  "status": "draft"
}
```

## 6. 认证与用户接口

### 6.1 获取当前用户
- `GET /api/v1/me`

### 6.2 获取当前用户权限
- `GET /api/v1/permissions`

## 7. 数据源接口

### 7.1 创建数据源
- `POST /api/v1/data-sources`

请求示例：
```json
{
  "name": "销售 PostgreSQL",
  "type": "postgresql",
  "category": "database",
  "config": {
    "host": "10.0.0.11",
    "port": 5432,
    "database": "sales",
    "username": "readonly_user"
  }
}
```

### 7.2 获取数据源列表
- `GET /api/v1/data-sources`

### 7.3 获取数据源详情
- `GET /api/v1/data-sources/{id}`

### 7.4 测试连接
- `POST /api/v1/data-sources/{id}/test-connection`

### 7.5 手动同步
- `POST /api/v1/data-sources/{id}/sync`

## 8. 知识库接口

### 8.1 创建知识库
- `POST /api/v1/knowledge-bases`

### 8.2 获取知识库列表
- `GET /api/v1/knowledge-bases`

### 8.3 获取知识库详情
- `GET /api/v1/knowledge-bases/{id}`

### 8.4 绑定资源
- `POST /api/v1/knowledge-bases/{id}/bind`

## 9. 文档接口

### 9.1 上传文档
- `POST /api/v1/documents/upload`
- `multipart/form-data`

表单字段：
- `file`
- `kb_id`
- `title`
- `version`
- `tags`

### 9.2 获取文档列表
- `GET /api/v1/documents`

### 9.3 获取文档详情
- `GET /api/v1/documents/{id}`

### 9.4 查看文档 chunks
- `GET /api/v1/documents/{id}/chunks`

### 9.5 重建索引
- `POST /api/v1/documents/{id}/reindex`

### 9.6 归档文档
- `POST /api/v1/documents/{id}/archive`

## 10. 检索接口

### 10.1 通用检索
- `POST /api/v1/search`

请求示例：
```json
{
  "query": "差旅住宿标准是什么",
  "kb_ids": ["kb_001"],
  "top_k": 8,
  "strategy": "hybrid"
}
```

### 10.2 高级检索
- `POST /api/v1/search/advanced`

## 11. 会话与问答接口

### 11.1 创建会话
- `POST /api/v1/conversations`

### 11.2 获取会话列表
- `GET /api/v1/conversations`

### 11.3 获取会话消息
- `GET /api/v1/conversations/{id}/messages`

### 11.4 同步问答
- `POST /api/v1/chat/ask`

请求示例：
```json
{
  "conversation_id": "conv_001",
  "question": "上海住宿标准是多少？",
  "mode": "qa",
  "kb_ids": ["kb_001"]
}
```

### 11.5 流式问答
- `POST /api/v1/chat/stream`
- 返回 `text/event-stream`

SSE 事件建议：
- `meta`
- `token`
- `citations`
- `warning`
- `done`

### 11.6 问答反馈
- `POST /api/v1/messages/{id}/feedback`

## 12. 数据集与模板查询接口

### 12.1 获取数据集列表
- `GET /api/v1/datasets`

### 12.2 获取数据集详情
- `GET /api/v1/datasets/{id}`

### 12.3 获取字段定义
- `GET /api/v1/datasets/{id}/fields`

### 12.4 获取查询模板列表
- `GET /api/v1/query-templates?dataset_id=ds_xxx`

### 12.5 预览模板查询
- `POST /api/v1/query/preview`

### 12.6 执行模板查询
- `POST /api/v1/query/run-template`

## 13. 分析任务接口

### 13.1 创建分析任务
- `POST /api/v1/analysis/tasks`

### 13.2 获取分析任务详情
- `GET /api/v1/analysis/tasks/{id}`

## 14. 报告接口

### 14.1 获取报告模板列表
- `GET /api/v1/report-templates`

### 14.2 获取报告模板详情
- `GET /api/v1/report-templates/{code}`

### 14.3 生成报告
- `POST /api/v1/reports/generate`

请求示例：
```json
{
  "template_code": "sales_funnel_report",
  "title": "2026W19 销售漏斗分析报告",
  "params": {
    "period_start": "2026-05-05",
    "period_end": "2026-05-11",
    "dataset_ids": ["ds_sales_orders", "ds_leads"],
    "kb_ids": ["kb_sales_policy"]
  }
}
```

### 14.4 获取报告列表
- `GET /api/v1/reports`

### 14.5 获取报告详情
- `GET /api/v1/reports/{id}`

### 14.6 审核报告
- `POST /api/v1/reports/{id}/review`

### 14.7 发布报告
- `POST /api/v1/reports/{id}/publish`

### 14.8 导出报告
- `POST /api/v1/reports/{id}/export`

## 15. Wiki 接口

### 15.1 获取页面列表
- `GET /api/v1/wiki/pages`

### 15.2 获取页面详情
- `GET /api/v1/wiki/pages/{id}`

### 15.3 创建草稿
- `POST /api/v1/wiki/pages`

### 15.4 报告转 Wiki
- `POST /api/v1/reports/{id}/to-wiki`

### 15.5 编译草稿
- `POST /api/v1/wiki/pages/{id}/compile`

### 15.6 审核 Wiki
- `POST /api/v1/wiki/pages/{id}/review`

### 15.7 发布 Wiki
- `POST /api/v1/wiki/pages/{id}/publish`

### 15.8 历史版本
- `GET /api/v1/wiki/pages/{id}/versions`

## 16. 任务与审计接口

### 16.1 获取任务列表
- `GET /api/v1/jobs`

### 16.2 获取任务详情
- `GET /api/v1/jobs/{id}`

### 16.3 重试任务
- `POST /api/v1/jobs/{id}/retry`

### 16.4 获取审计日志
- `GET /api/v1/audit-logs`

## 17. 幂等与重试建议
- 以下创建型接口建议支持 `Idempotency-Key`：
- 文档上传
- 报告生成
- 分析任务创建
- Wiki 草稿创建

## 18. 上传与导出约束
- 上传文件建议限制在单文件 `200MB` 以内。
- 导出支持：
- `pdf`
- `html`
- `markdown`
- 导出行为必须进入审计日志。

## 19. 权限控制原则
- 所有资源接口默认要求 token。
- 问答和检索按知识库授权范围过滤。
- 分析按数据集与模板双重校验。
- 发布与导出权限分离。

## 20. 文档结论
- 一期接口设计以“资源对象 + 长任务 + 流式问答”为核心模型。
- 重点不是接口数量，而是统一结构、统一授权、统一审计和统一 evidence 表达。
