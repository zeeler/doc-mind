# 文档管理增强 — 设计文档

**日期**: 2026-05-29
**状态**: 已确认

## 概述

为 Doc Mind 知识库增加标签系统、分类系统、虚拟集合、文件夹浏览和批量操作能力。

## 设计决策

- 物理路径（自动记录）+ 虚拟集合（手动创建）两套体系
- 分类互斥（一个文档一个分类），标签自由附加（多个）
- 批量操作为完整集合：删除、重处理、打标签/去标签、改分类、集合管理、下载
- 务实分层模型：高频字段用简单列，多对多用关联表

## 数据模型

### Document 表新增字段

```sql
ALTER TABLE documents ADD COLUMN folder_path TEXT DEFAULT '';
ALTER TABLE documents ADD COLUMN category VARCHAR(100) DEFAULT '';
```

两者均建立索引。

### 新建表

**tags**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| name | VARCHAR(100) UNIQUE | 标签名，大小写不敏感 |

**document_tags**
| 字段 | 类型 | 说明 |
|------|------|------|
| doc_id | VARCHAR(36) FK | 指向 documents.id |
| tag_id | VARCHAR(36) FK | 指向 tags.id |
| 联合主键 | (doc_id, tag_id) | |

**collections**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| name | VARCHAR(200) | 集合名称 |
| description | TEXT | 可选描述 |
| created_at | DateTime | 创建时间 |

**collection_documents**
| 字段 | 类型 | 说明 |
|------|------|------|
| doc_id | VARCHAR(36) FK | 指向 documents.id |
| collection_id | VARCHAR(36) FK | 指向 collections.id |
| added_at | DateTime | 加入时间 |
| 联合主键 | (doc_id, collection_id) | |

### 级联规则

- 删除 tag → 级联删除 document_tags 关联
- 删除 collection → 级联删除 collection_documents 关联（不动文档）
- 删除 document → 级联删除所有关联记录

### 标签去重

- 创建/添加时统一 `strip().lower()` 匹配
- 显示保留用户首次创建的原始大小写

## API 设计

所有接口前缀 `/api/v1`。

### 标签

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/tags` | 列出所有标签（含 doc_count） |
| POST | `/tags` | 创建标签 `{name}` — 重名返回已有 |
| DELETE | `/tags/{id}` | 删除标签 |

### 集合

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/collections` | 列出所有集合（含 doc_count） |
| POST | `/collections` | 创建集合 `{name, description?}` |
| PUT | `/collections/{id}` | 更新集合名/描述 |
| DELETE | `/collections/{id}` | 删除集合（不删文档） |

### 单文档元数据更新

**PUT** `/documents/{id}`

```json
{
  "category": "技术",
  "add_tags": ["python", "ai"],
  "remove_tags": ["old-tag"],
  "add_collections": ["c1"],
  "remove_collections": ["c2"]
}
```

所有字段可选。设 `category: ""` 清除分类。

### 文档列表增强

**GET** `/documents`

| 参数 | 类型 | 说明 |
|------|------|------|
| skip | int | 分页偏移 |
| limit | int | 每页数量 |
| folder | string | 按物理路径筛选 |
| category | string | 按分类筛选 |
| tag | string | 按标签名筛选 |
| collection | string | 按集合 ID 筛选 |
| status | string | 按状态筛选 |
| search | string | 标题模糊搜索 |

响应每个文档附带 `tags` 和 `collections` 数组。

### 文件夹浏览

**GET** `/documents/folders`

返回去重后的 `folder_path` 列表，前端拼装为树。

### 批量操作

**POST** `/documents/batch`

```json
{
  "ids": ["id1", "id2"],
  "action": "delete | retry | tag | untag | categorize | collect | download",
  "params": {
    "category": "...",
    "tags": ["..."],
    "collection_id": "..."
  }
}
```

逐个执行，部分失败不影响后续。返回每个 id 的独立结果：

```json
{
  "code": "OK",
  "data": [
    {"id": "id1", "success": true},
    {"id": "id2", "success": false, "error": "文档不存在"}
  ]
}
```

### 错误码

| 场景 | HTTP 状态码 | detail |
|------|------------|--------|
| 标签名空 | 400 | 标签名不能为空 |
| 标签名超长 | 400 | 标签名不能超过100个字符 |
| 文档不存在 | 404 | 文档不存在 |
| 集合名重复 | 409 | 集合名已存在 |
| 集合名空 | 400 | 集合名不能为空 |
| ids 为空 | 400 | ids 不能为空 |
| 不支持的操作 | 400 | 不支持的操作类型: xxx |

## 前端 UI

### 布局

两栏布局：侧边栏（目录树 + 标签列表 + 集合列表）+ 主内容区（搜索/筛选工具栏 + 文档列表 + 批量操作栏）。

### 侧边栏

- 三个可折叠区块
- 文件夹由 `GET /documents/folders` 返回的平铺路径前端拼装树
- 标签和集合显示文档数量徽标
- 点击任一筛选项 → 右侧列表过滤

### 主内容区

- 搜索框 + 状态/分类/标签下拉筛选
- "批量模式"切换按钮
- 文档列表每行带 checkbox（批量模式下显示）
- 标签以 chip 形式展示，点击即筛选
- 悬浮行出现快捷按钮：打标签、加集合

### 批量操作栏

- 条件渲染，选中文档后底部浮现
- 显示已选数量 + 操作按钮组
- 标签选择面板：输入框 + 已有标签列表，输入不存在的自动创建

### 技术约束

- 不引入任何第三方 UI 库
- 复用现有 CSS 变量体系
- 新增 CSS 控制在 150 行以内
- 保持零前端依赖

## 后端文件结构

```
server/
├── database.py           # 新增 migrate_v2()
├── models/
│   ├── tag.py            # 新增：Tag 模型
│   └── collection.py     # 新增：Collection + CollectionDocument 模型
├── routers/
│   ├── documents.py      # 修改：批量操作、文件夹、增强列表、单文档更新
│   ├── tags.py           # 新增：标签 CRUD
│   └── collections.py    # 新增：集合 CRUD
└── tests/
    └── test_routers/
        ├── test_tags.py          # 新增
        ├── test_collections.py   # 新增
        ├── test_documents.py     # 修改：新增批量/更新/筛选用例
        └── test_batch.py         # 新增：批量操作用例
```

## 数据库迁移

启动时自动检测 `tags` 表是否存在，不存在则执行 `migrate_v2()` 创建所有新表+新列。不引入 Alembic。

## 测试计划

| 模块 | 内容 | 预计用例 |
|------|------|---------|
| 模型 | Tag / Collection / 关联表 | 5 |
| 路由 - tags | CRUD + 去重 + 边界 | 5 |
| 路由 - collections | CRUD + 重名 + 边界 | 5 |
| 路由 - documents 增强 | 更新/筛选/文件夹 | 6 |
| 路由 - batch | 各操作类型 + 部分失败 | 5 |
| 集成 | 端到端流程 | 2 |
| **合计** | | **约 28** |

现有 87 个测试，新增后约 115 个。

## 依赖

零新增依赖。
