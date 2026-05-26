# 大规模文档处理 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 扩展知识库支持上千份文档：后台任务队列、两阶段处理、多格式解析、前端进度显示。

**Architecture:** SQLite Jobs 表作为任务队列，2 个后台线程消费。quick_scan（秒级标题索引）→ full_index（OCR+向量）。新增 XLSX/PPTX/MOBI 解析器。

**Tech Stack:** Python 3.12+, SQLAlchemy, ChromaDB, openpyxl, python-pptx, ebooklib, threading

**Spec:** `docs/superpowers/specs/2026-05-26-knowledge-base-scale-design.md`

---

### Task 1: Job 模型 + 数据库迁移

- Create: `server/models/job.py`
- Modify: `server/database.py`
- Modify: `server/main.py`
- Create: `server/tests/test_job_model.py`

```python
# server/models/job.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from server.models.base import Base

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
```

在 `database.py` 的 `_migrate()` 中添加 `CREATE TABLE IF NOT EXISTS jobs (...)` 语句。

在 `main.py` 的 `startup()` 中导入 Job 模型：`from server.models.job import Job  # noqa: F401`

测试验证 jobs 表字段和默认值。提交。

---

### Task 2: XLSX 解析器

- Create: `server/services/formats/__init__.py`
- Create: `server/services/formats/xlsx.py`
- Create: `server/tests/test_format_xlsx.py`

实现 `parse_xlsx(path) → str`，用 openpyxl 遍历 sheet，逐行 " | ".join 转表格文本。测试创建单 sheet xlsx 验证文本提取。

---

### Task 3: PPTX 解析器

- Create: `server/services/formats/pptx.py`
- Create: `server/tests/test_format_pptx.py`

实现 `parse_pptx(path) → str`，用 python-pptx 遍历 slides，提取 shape.text_frame 文字。测试单页 pptx。

---

### Task 4: MOBI 解析器

- Create: `server/services/formats/mobi.py`
- Create: `server/tests/test_format_mobi.py`

实现 `parse_mobi(path) → str`，优先 ebooklib 解析 HTML，降级为原始字节读取。测试标记 skip（依赖样本文件）。

---

### Task 5: 统一解析入口 + 依赖更新

- Modify: `server/services/parser.py`
- Modify: `pyproject.toml`

在 `parse_file()` 中新增 xlsx/pptx/mobi 分支。SUPPORTED_TYPES 扩展。依赖添加 openpyxl、python-pptx、ebooklib、beautifulsoup4。

---

### Task 6: 快速扫描器

- Create: `server/services/scanner.py`
- Create: `server/tests/test_scanner.py`

```python
def quick_scan(file_path: str) -> dict:
    """返回 {title, format, page_count, preview, size_bytes}"""
    # PDF: PyMuPDF 读首页文本 + 页数
    # DOCX: 读前10段
    # TXT/MD: 读前500字符
    # 其他: 仅元数据

def build_index_md(info: dict, full_text: str = "") -> str:
    """生成 index.md: 元信息表 + 正文/预览"""
```

---

### Task 7: 后台 Worker + 两阶段管道

- Create: `server/services/worker.py`
- Modify: `server/services/pipeline.py`（新增 `index_document()` 独立函数）
- Modify: `server/main.py`（启动 worker）

Worker 功能：
- `start_workers(num=2)` / `stop_workers()`
- `_worker_loop()`: 每分钟轮询 `SELECT ... WHERE status='pending' ORDER BY priority, created_at LIMIT 1`
- `_execute_job()`: quick_scan → 调 scanner 生成 index.md 初版；full_index → 解析 + OCR + 更新 index.md + 调用 index_document() 写向量
- `create_jobs_for_document(doc_id)`: 为一篇文档创建 quick_scan(priority=1) + full_index(priority=5)

---

### Task 8: Jobs API + 批量导入

- Create: `server/routers/jobs.py`
- Modify: `server/main.py`

`GET /api/v1/jobs?status=pending` — 任务列表
`GET /api/v1/jobs/stats` — `{pending, running, completed, failed}` 计数
`POST /api/v1/jobs/{id}/retry` — 重试失败任务

---

### Task 9: 前端 — 进度条 + 批量导入

- Modify: `server/templates/index.html`

在文档管理页添加：
- 任务统计栏：`X 等待中 · Y 处理中 · Z 完成 · N 失败`
- 进度条（completed / total）
- 每 3 秒自动刷新 job stats
- 「选择本地目录」按钮增强，批量创建 document + job

---

### Task 10: 端到端验证

- 全部测试通过
- 启动 server，批量上传测试文件
- 验证 jobs API 返回正确统计
- 验证 index.md 文件生成
- 验证向量检索可查

---

## 依赖关系

```
Task 1 (Job 模型)
  └→ Task 7 (Worker) → Task 8 (Jobs API) → Task 9 (前端) → Task 10 (验证)
Task 2/3/4 (格式解析) ─→ Task 5 (统一入口) → Task 6 (Scanner) ─┘
```

并行机会：Task 2/3/4 可同时执行。
