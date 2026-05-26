# 知识库系统扩展设计 — 大规模文档处理

**日期**: 2026-05-26  
**版本**: v1  
**状态**: 已确认

## 1. 背景

现有 MVP 已支持 PDF/Word/Markdown/TXT 的上传、OCR、检索和问答。新需求：处理上千份文档，多种格式，批量导入，后台处理，自动生成 Markdown 用于检索。

## 2. 核心决策

| 维度 | 决策 |
|------|------|
| 文档导入 | 批量目录导入为主，单文件上传为辅 |
| 检索方式 | .md 文件索引检索，源文件存档 |
| 处理策略 | 两阶段：快速扫描(秒级) → 全文索引(后台) |
| 架构 | SQLite 任务队列 + 后台线程池 Worker |
| 存储 | 维持现有 data/files/<doc_id>/ 结构 |
| Markdown | 每份文档生成 index.md 与源文件同目录 |
| 平台 | macOS Apple Silicon，本地运行 |

## 3. 新增文件格式支持

| 格式 | 解析策略 |
|------|---------|
| PDF | PyMuPDF 文本提取 + Tesseract/多模态 OCR |
| DOCX | python-docx 段落提取 |
| XLSX | openpyxl 逐 sheet 转表格文本 |
| PPTX | python-pptx 逐 slide 提取文本 |
| MOBI | ebooklib 或 calibre 工具转换 |
| TXT/MD | 直接读取 |

## 4. 任务队列模型

### Job 表

```
jobs
├── id (UUID)
├── document_id (FK → documents.id)
├── job_type: "quick_scan" | "full_index"
├── priority: INTEGER (越小越优先)
├── status: "pending" → "running" → "completed" / "failed"
├── progress: 0-100
├── error_message (TEXT)
├── started_at / finished_at (DateTime)
└── created_at (DateTime)
```

### 两阶段处理

1. **quick_scan**（priority=1，5 秒内）
   - 解析文件标题、页数/行数、文件大小
   - 提取第一页/开头的文本内容
   - 生成 index.md 初版
   - 更新文档状态 → `scanned`

2. **full_index**（priority=5，分钟级）
   - 完整文本提取 + OCR 扫描页
   - 更新 index.md 为完整文本
   - Chunk → Embedding → ChromaDB
   - 更新文档状态 → `done`

## 5. 项目结构

```
server/
├── services/
│   ├── pipeline.py           # 改为两阶段调度
│   ├── scanner.py            # [NEW] 快速扫描器
│   ├── worker.py             # [NEW] 后台线程池 Worker
│   ├── parser.py             # 扩增格式支持
│   └── formats/              # [NEW]
│       ├── pdf.py
│       ├── docx.py
│       ├── xlsx.py
│       ├── pptx.py
│       ├── mobi.py
│       └── txt.py
├── models/
│   └── job.py                # [NEW] Job ORM
├── routers/
│   └── jobs.py               # [NEW] 任务进度 API
```

## 6. API 扩展

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/jobs` | 所有任务列表（按状态筛选） |
| `GET` | `/api/v1/jobs/stats` | 统计：pending/running/done/fail 计数 |
| `POST` | `/api/v1/jobs/{id}/retry` | 重试失败任务 |
| `POST` | `/api/v1/documents/import` | 批量导入（接收文件路径列表） |

## 7. 前端改动

- 文档管理页：任务进度条（扫描中 / 索引中 数量）
- 每个文档显示两阶段状态标记
- 「选择目录」按钮整合批量导入接口

## 8. 非功能约束

- 保持单进程（不引入 Redis/消息队列）
- 2-3 个后台线程处理任务
- 失败任务自动重试 3 次
- 扫描阶段优先于索引阶段（priority 机制）
- 所有 .md 文件与源文件同目录存放
