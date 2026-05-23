# 企业内部知识库 Agent 平台系统架构说明书 v1

## 1. 文档信息
- 文档名称：`系统架构说明书 v1`
- 项目代号：`KAP`
- 适用阶段：一期 MVP 技术评审与实施准备

## 2. 架构目标
- 支持 `10G+` 非结构化文档接入、解析、索引和问答。
- 支持多个结构化数据源的受控接入与模板化分析。
- 支持报告草稿生成、审核和发布。
- 支持 Wiki 草稿生成、审核、发布和版本维护。
- 支持知识库级、数据集级、发布级权限控制以及全链路审计。

## 3. 设计原则
- 事实优先：所有回答、报告、Wiki 都要可追溯到数据或文档来源。
- 权限先行：检索、查询、导出和发布都必须经过授权。
- 模板优先：一期优先模板化分析和模板化报告，不做自由生成。
- 异步优先：文档入库、报告生成、Wiki 编译采用任务化执行。
- 分层治理：文档知识层、结构化分析层、Agent 编排层和应用治理层分离。
- 本地一致性优先：本地开发环境统一使用 `OrbStack`。

## 4. 架构总览

```text
用户入口层
  ├─ Web 工作台
  ├─ Admin 管理台
  └─ Wiki 浏览站点

接入层
  └─ API Gateway / BFF

核心业务层
  ├─ Auth & Governance Service
  ├─ Ingestion Service
  ├─ Document Processing Service
  ├─ Retrieval Service
  ├─ SQL Tool Service
  ├─ Agent Orchestrator
  ├─ Report Service
  ├─ Wiki Service
  └─ Scheduler / Worker

存储与基础设施层
  ├─ PostgreSQL
  ├─ MinIO
  ├─ OpenSearch
  ├─ Redis
  ├─ ClickHouse
  └─ RAGFlow
```

## 5. 五层架构模型

### 5.1 数据接入层
- 负责数据源配置、文档上传、同步任务创建、只读接入策略。
- 输入对象：
- 文档文件
- 网盘目录
- 数据库连接
- 受控 API 数据

### 5.2 知识加工层
- 负责文档解析、OCR、结构抽取、切块、元数据增强、embedding 和索引写入。
- 主要由 `Document Processing Service + RAGFlow` 承担。

### 5.3 检索与分析层
- 负责混合检索、rerank、模板化查询、受控数据分析。
- 主要由 `Retrieval Service + SQL Tool Service` 承担。

### 5.4 Agent 编排层
- 负责多步流程编排、工具调用顺序、状态管理、任务重试和人工审核节点。
- 主要由 `Agent Orchestrator` 承担，建议使用 `LangGraph`。

### 5.5 应用与治理层
- 负责用户入口、审核发布、权限控制、审计监控、任务管理。
- 主要由 `Web/Admin + Auth/Governance + Report/Wiki` 共同承担。

## 6. 技术基线
- 前端：`Vue 3` 或 `Next.js`
- 网关与业务 API：`NestJS`
- Agent 编排：`Python + LangGraph`
- 数据接入与索引抽象：`LlamaIndex`
- 文档处理：`RAGFlow`
- 主数据库：`PostgreSQL`
- 对象存储：`MinIO`
- 检索：`OpenSearch`
- 缓存与任务：`Redis`
- 分析型存储：`ClickHouse`
- 本地运行时：`OrbStack`

## 7. 模块职责

### 7.1 API Gateway
- 接收前端和外部 API 请求。
- 校验身份、注入上下文、做限流和转发。
- 不承载复杂业务逻辑。

### 7.2 Auth & Governance Service
- 管理用户、部门、角色、资源策略。
- 做知识库级、数据集级、发布级权限判断。
- 记录权限变更和关键审计日志。

### 7.3 Ingestion Service
- 管理数据源接入。
- 保存原始文件。
- 创建文档解析任务和同步任务。
- 管理失败重试和状态流转。

### 7.4 Document Processing Service
- 调用 `RAGFlow` 做文档解析。
- 负责 OCR、结构抽取、chunk、embedding、索引写入。
- 更新文档处理状态。

### 7.5 Retrieval Service
- 提供 BM25 + 向量 + rerank 的统一检索能力。
- 支持权限过滤、知识库过滤、时间过滤、类型过滤。
- 输出命中结果与 citation 候选。

### 7.6 SQL Tool Service
- 只允许通过 `dataset + query_template` 进行受控查询。
- 不允许自由 SQL。
- 负责超时限制、结果裁剪、敏感字段控制和查询审计。

### 7.7 Agent Orchestrator
- 负责四类核心流程：
- QA Flow
- Analysis Flow
- Report Flow
- Wiki Compile Flow
- 管理工具调用、状态机和失败恢复。

### 7.8 Report Service
- 管理报告模板、报告草稿、审核、发布、导出。
- 记录报告来源和生成快照。

### 7.9 Wiki Service
- 管理 Wiki 草稿、页面结构、版本、审核、发布和 evidence。

### 7.10 Scheduler / Worker
- 执行异步任务。
- 处理同步、重建索引、报告生成、Wiki 编译、清理任务。

## 8. 核心业务流程

### 8.1 文档入库流程
1. 管理员上传文档或触发同步。
2. `Ingestion Service` 保存原始文件到 `MinIO`，创建 `documents/jobs`。
3. `Document Processing Service` 拉取任务并调用 `RAGFlow` 解析。
4. 解析结果写入 `OpenSearch` 和元数据表。
5. 文档状态变为 `indexed`。

### 8.2 知识问答流程
1. 用户在授权范围内发起问题。
2. `Agent Orchestrator` 识别意图。
3. 文档问题走 `Retrieval Service`。
4. 分析问题走 `SQL Tool Service`。
5. 结果交给 LLM 汇总，并输出 citation。
6. 问答消息、命中记录和引用快照入库。

### 8.3 报告生成流程
1. 用户选择模板并提交参数。
2. `Report Service` 创建草稿与任务。
3. `Agent Orchestrator` 调用查询模板和知识检索。
4. LLM 按模板结构生成草稿。
5. 草稿进入 `reviewing`，等待审核。

### 8.4 Wiki 编译流程
1. 来源可以是报告、问答或手工草稿。
2. `Wiki Service` 创建页面草稿。
3. `Agent Orchestrator` 按页面类型编译结构和 evidence。
4. 草稿进入 `review_pending`。
5. 审核通过后发布。

## 9. 状态机

### 9.1 文档状态
- `uploaded`
- `parsing`
- `parsed`
- `indexing`
- `indexed`
- `failed`
- `reindexing`
- `archived`

### 9.2 报告状态
- `draft`
- `generating`
- `generated`
- `reviewing`
- `approved`
- `published`
- `rejected`
- `archived`

### 9.3 Wiki 状态
- `draft`
- `compiling`
- `review_pending`
- `approved`
- `published`
- `rejected`
- `superseded`

## 10. 权限边界
- 默认拒绝，按资源策略放行。
- 检索前过滤资源范围，不允许先召回后裁剪。
- 数据分析按 `dataset + template` 双重约束。
- 报告生成权、审核权、发布权分离。
- Wiki 草稿创建权、审核权、发布权分离。

## 11. 数据边界
- 文档知识层：制度、流程、FAQ、专题材料。
- 结构化分析层：主题数据集和查询模板。
- 记忆层：一期不作为主能力上线，不承担企业事实主库职责。

## 12. 本地环境与部署基线

### 12.1 本地开发
- 容器运行时统一使用 `OrbStack`。
- 容器内运行：
- PostgreSQL
- Redis
- MinIO
- OpenSearch
- ClickHouse
- RAGFlow
- 业务服务可本机直跑，也可容器化运行。

### 12.2 试点环境
- 以 Linux 容器部署为主。
- 一期不强制 Kubernetes。
- 保持与本地相似的 Compose 编排模型。

## 13. 一期明确不做
- 自由 SQL Agent
- GraphRAG 生产链路
- 长期记忆主系统
- 自动发布报告和 Wiki
- 多部门并行推广
- 多租户能力

## 14. 架构结论
- 一期系统应定位为“企业内部知识与分析协作平台”。
- 重点不是构建一个万能 Agent，而是交付一个可控、可审计、可试点的闭环平台。
- 本地开发统一采用 `OrbStack`，生产试点以 Linux 容器部署为主，既保证研发效率，也保证后续迁移空间。
