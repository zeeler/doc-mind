# 企业内部知识库 Agent 平台数据模型说明书 v1

## 1. 文档信息
- 文档名称：`数据模型说明书 v1`
- 项目代号：`KAP`
- 适用阶段：一期 MVP 数据库设计评审

## 2. 建模原则
- 主事实与派生产物分离。
- 任务与对象分离。
- 状态显式化。
- 权限外置到资源策略，不硬编码在业务表里。
- 发布内容必须可追溯到 evidence/source。

## 3. 数据域划分
- 身份与权限域
- 数据源与知识库域
- 文档与索引域
- 问答与检索域
- 分析与报告域
- Wiki 域
- 任务与审计域

## 4. 身份与权限域

### 4.1 `departments`
- 用途：组织架构树。
- 关键字段：
- `id`
- `name`
- `parent_id`
- `path`
- 建议索引：
- `path`

### 4.2 `users`
- 用途：平台用户主表。
- 关键字段：
- `id`
- `email`
- `name`
- `mobile`
- `status`
- `department_id`
- `external_identity`
- 状态枚举：
- `active`
- `inactive`
- `locked`
- 建议索引：
- `email` 唯一索引
- `department_id`

### 4.3 `roles`
- 用途：角色定义。
- 关键字段：
- `code`
- `name`
- 建议内置角色：
- `admin`
- `department_admin`
- `employee`
- `analyst`
- `report_reviewer`
- `wiki_reviewer`
- `knowledge_operator`

### 4.4 `user_roles`
- 用途：用户和角色多对多关系。
- 约束：
- `unique(user_id, role_id)`

### 4.5 `resource_policies`
- 用途：统一资源授权策略。
- 关键字段：
- `resource_type`
- `resource_id`
- `subject_type`
- `subject_id`
- `permission`
- `effect`
- 枚举建议：
- `resource_type`：`knowledge_base`、`document`、`dataset`、`query_template`、`report`、`wiki_page`
- `subject_type`：`user`、`role`、`department`
- `permission`：`read`、`search`、`query`、`generate`、`review`、`publish`、`export`、`admin`
- `effect`：`allow`、`deny`

## 5. 数据源与知识库域

### 5.1 `data_sources`
- 用途：外部数据源配置。
- 关键字段：
- `name`
- `type`
- `category`
- `config_encrypted`
- `status`
- `last_sync_at`
- `owner_id`
- 状态枚举：
- `active`
- `inactive`
- `error`

### 5.2 `knowledge_bases`
- 用途：知识逻辑空间。
- 关键字段：
- `name`
- `description`
- `default_retrieval_strategy`
- `status`
- `owner_id`
- 状态枚举：
- `active`
- `inactive`
- `archived`

### 5.3 `kb_bindings`
- 用途：知识库与资源绑定关系。
- 关键字段：
- `kb_id`
- `source_type`
- `source_id`
- 建议约束：
- `unique(kb_id, source_type, source_id)`

## 6. 文档与索引域

### 6.1 `documents`
- 用途：文档主表。
- 关键字段：
- `kb_id`
- `source_id`
- `title`
- `doc_type`
- `mime_type`
- `storage_path`
- `checksum`
- `version`
- `status`
- `language`
- `published_at`
- `created_by`
- 状态枚举：
- `uploaded`
- `parsing`
- `parsed`
- `indexing`
- `indexed`
- `failed`
- `archived`
- `reindexing`
- 建议索引：
- `(kb_id, status)`
- `checksum`
- `updated_at`

### 6.2 `document_versions`
- 用途：文档历史版本。
- 关键字段：
- `document_id`
- `version`
- `storage_path`
- `checksum`
- `status`
- 建议约束：
- `unique(document_id, version)`

### 6.3 `document_chunks`
- 用途：切块结果。
- 关键字段：
- `document_id`
- `chunk_no`
- `content`
- `summary`
- `token_count`
- `page_no`
- `section_path`
- `metadata_json`
- 建议约束：
- `unique(document_id, chunk_no)`

### 6.4 `document_entities`
- 用途：文档实体抽取结果。
- 关键字段：
- `document_id`
- `entity_type`
- `entity_name`
- `entity_value`
- `confidence`
- 说明：
- 一期可保留为增强表，不作为核心闭环强依赖。

### 6.5 `document_relations`
- 用途：文档关系抽取结果。
- 一期说明：
- 可预留表，不作为首批上线必需能力。

## 7. 问答与检索域

### 7.1 `conversations`
- 用途：会话主表。
- 关键字段：
- `user_id`
- `title`
- `mode`
- `status`
- `mode` 枚举：
- `qa`
- `analysis`
- `report`

### 7.2 `messages`
- 用途：消息记录。
- 关键字段：
- `conversation_id`
- `role`
- `content`
- `citations_json`
- `tool_calls_json`
- `role` 枚举：
- `user`
- `assistant`
- `system`
- `tool`

### 7.3 `search_sessions`
- 用途：一次检索或问答上下文。
- 关键字段：
- `user_id`
- `query`
- `intent`
- `kb_scope`
- `intent` 枚举：
- `document_qa`
- `data_query`
- `hybrid_analysis`
- `report_generation`

### 7.4 `search_hits`
- 用途：记录召回命中与是否最终被采用。
- 关键字段：
- `session_id`
- `source_type`
- `source_id`
- `chunk_id`
- `score`
- `rerank_score`
- `used_in_answer`

## 8. 分析与报告域

### 8.1 `datasets`
- 用途：结构化分析对象。
- 关键字段：
- `name`
- `source_id`
- `dataset_type`
- `schema_name`
- `table_name`
- `view_name`
- `description`
- `security_level`
- `dataset_type` 枚举：
- `sql_table`
- `sql_view`
- `materialized_view`
- `api_dataset`

### 8.2 `dataset_fields`
- 用途：字段级治理信息。
- 关键字段：
- `dataset_id`
- `field_name`
- `field_type`
- `label`
- `is_sensitive`
- `is_filterable`
- `is_aggregatable`

### 8.3 `query_templates`
- 用途：受控查询模板。
- 关键字段：
- `dataset_id`
- `name`
- `description`
- `template_sql`
- `params_schema`
- `status`
- 状态枚举：
- `active`
- `inactive`
- `deprecated`

### 8.4 `analysis_tasks`
- 用途：分析任务记录。
- 关键字段：
- `user_id`
- `task_type`
- `input_json`
- `status`
- `result_json`
- `error_message`
- 状态枚举：
- `pending`
- `running`
- `completed`
- `failed`
- `cancelled`

### 8.5 `report_templates`
- 用途：报告模板定义。
- 关键字段：
- `code`
- `name`
- `description`
- `input_schema`
- `prompt_template`
- `layout_template`
- `status`
- 状态枚举：
- `draft`
- `active`
- `inactive`

### 8.6 `reports`
- 用途：报告对象。
- 关键字段：
- `template_id`
- `title`
- `owner_id`
- `status`
- `period_start`
- `period_end`
- `content_md`
- `content_html`
- `snapshot_json`
- 状态枚举：
- `draft`
- `generating`
- `generated`
- `reviewing`
- `approved`
- `published`
- `rejected`
- `archived`

### 8.7 `report_sources`
- 用途：报告与证据来源的关系表。
- 关键字段：
- `report_id`
- `source_type`
- `source_id`
- `weight`
- `note`
- `source_type` 枚举：
- `dataset`
- `document`
- `document_chunk`
- `wiki_page`

## 9. Wiki 域

### 9.1 `wiki_pages`
- 用途：Wiki 页面主表。
- 关键字段：
- `kb_id`
- `slug`
- `title`
- `page_type`
- `status`
- `summary`
- `body_md`
- `owner_id`
- `page_type` 枚举：
- `topic`
- `faq`
- `process`
- 状态枚举：
- `draft`
- `compiling`
- `review_pending`
- `approved`
- `published`
- `rejected`
- `superseded`

### 9.2 `wiki_page_versions`
- 用途：Wiki 历史版本。
- 关键字段：
- `page_id`
- `version_no`
- `body_md`
- `change_note`
- `created_by`

### 9.3 `wiki_page_links`
- 用途：页面间关系。
- 关键字段：
- `page_id`
- `target_page_id`
- `link_type`
- `link_type` 枚举：
- `related`
- `references`
- `depends_on`
- `supersedes`

### 9.4 `wiki_evidence`
- 用途：页面 evidence 关联。
- 关键字段：
- `page_id`
- `source_type`
- `source_id`
- `excerpt`
- `confidence`

## 10. 任务与审计域

### 10.1 `jobs`
- 用途：统一后台任务表。
- 关键字段：
- `job_type`
- `payload_json`
- `status`
- `retry_count`
- `scheduled_at`
- `started_at`
- `finished_at`
- `job_type` 枚举：
- `document_index`
- `datasource_sync`
- `analysis_run`
- `report_generate`
- `wiki_compile`
- `export_report`
- `status` 枚举：
- `pending`
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `dead_letter`

### 10.2 `audit_logs`
- 用途：关键行为审计。
- 关键字段：
- `user_id`
- `action`
- `resource_type`
- `resource_id`
- `request_json`
- `result_json`
- `ip`
- 建议记录动作：
- `login`
- `search`
- `query_run`
- `report_generate`
- `report_publish`
- `wiki_publish`
- `document_upload`
- `document_archive`
- `dataset_export`
- `permission_change`

## 11. 表关系概要
- `departments` 1:N `users`
- `users` N:M `roles` via `user_roles`
- `knowledge_bases` 1:N `documents`
- `documents` 1:N `document_versions`
- `documents` 1:N `document_chunks`
- `users` 1:N `conversations`
- `conversations` 1:N `messages`
- `users` 1:N `search_sessions`
- `search_sessions` 1:N `search_hits`
- `data_sources` 1:N `datasets`
- `datasets` 1:N `dataset_fields`
- `datasets` 1:N `query_templates`
- `report_templates` 1:N `reports`
- `reports` 1:N `report_sources`
- `knowledge_bases` 1:N `wiki_pages`
- `wiki_pages` 1:N `wiki_page_versions`
- `wiki_pages` 1:N `wiki_evidence`

## 12. 生命周期与保留策略
- 文档原始文件长期保留，归档后默认不参与检索。
- 会话与消息建议保留 `180-365 天`。
- 检索命中记录建议保留 `90-180 天`。
- 已发布报告长期保留，草稿和驳回报告保留 `90-180 天`。
- 已发布 Wiki 和历史版本长期保留。
- 审计日志建议至少保留 `180 天`。

## 13. 合规与脱敏
- `data_sources.config_encrypted` 必须加密存储。
- `dataset_fields.is_sensitive = true` 的字段默认脱敏或限制显示。
- 审计日志中不得明文记录凭据和敏感值。
- 发布内容必须在权限范围内展示。

## 14. 一期不重点落库的能力
- 长期记忆画像
- GraphRAG 社区结构
- 自由 SQL 推理链
- 大模型完整内部推理过程

## 15. 文档结论
- 一期数据模型已经足够支撑文档入库、问答、分析、报告、Wiki、权限与审计闭环。
- 后续扩展 GraphRAG、长期记忆和指标中心时，也可以在当前分域模型上继续演进。
