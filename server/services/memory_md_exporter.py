"""MemoryMDExporter — 记忆 Markdown 导出器（线程安全）。"""

import re
import threading
from datetime import datetime, timezone
from pathlib import Path


class MemoryMDExporter:
    """将记忆导出为人类可读的 Markdown 文件。

    目录结构:
        data/memories/
        ├── global/
        │   ├── preferences.md
        │   ├── facts.md
        │   └── conclusions.md
        ├── sessions/
        │   └── {conv_id}.md
        └── INDEX.md
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def _get_lock(self, path: Path) -> threading.Lock:
        key = str(path)
        with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def _ensure_dirs(self):
        (self.base_dir / "global").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "sessions").mkdir(parents=True, exist_ok=True)

    # ====== 增量更新 ======

    def incremental_update(self, mem_id: str, content: str, metadata: dict):
        """新增或更新单条记忆时，增量更新对应的 md 文件。"""
        self._ensure_dirs()
        mem_type = metadata.get("type", "manual")
        scope = metadata.get("scope", "global")
        conv_id = metadata.get("source_conv_id", "")

        if scope == "session" and conv_id:
            file_path = self.base_dir / "sessions" / f"{conv_id}.md"
            self._append_to_session_file(file_path, mem_id, content, metadata)
        elif mem_type in ("preference", "fact", "conclusion", "manual"):
            file_path = self.base_dir / "global" / f"{mem_type}s.md"
            self._append_to_global_file(file_path, mem_type, mem_id, content, metadata)
        else:
            file_path = self.base_dir / "global" / "other.md"
            self._append_to_global_file(file_path, "other", mem_id, content, metadata)

        # 更新 INDEX.md
        self._update_index()

    def _append_to_global_file(self, path: Path, mem_type: str, mem_id: str,
                               content: str, metadata: dict):
        importance = metadata.get("importance", 0.5)
        count = metadata.get("count", 1)
        stars = "⭐" if importance >= 0.8 else ""
        line = f"- {content} — 出现 {count} 次 | {stars} {importance:.2f}\n"

        lock = self._get_lock(path)
        with lock:
            if not path.exists():
                title_map = {
                    "preference": "# 用户偏好", "fact": "# 已知事实",
                    "conclusion": "# 跨会话结论", "manual": "# 手动备注", "other": "# 其他记忆",
                }
                header = title_map.get(mem_type, "# 记忆")
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                path.write_text(f"{header}\n\n> 最后更新: {now} | 共 1 条\n\n{line}", encoding="utf-8")
            else:
                text = path.read_text(encoding="utf-8")
                # 更新计数和时间
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                def _update_header(m):
                    return f'> 最后更新: {now} | 共 {int(m.group(1)) + 1} 条'
                text = re.sub(
                    r'> 最后更新: .* \| 共 (\d+) 条',
                    _update_header,
                    text, count=1
                )
                text += line
                path.write_text(text, encoding="utf-8")

    def _append_to_session_file(self, path: Path, mem_id: str,
                                content: str, metadata: dict):
        mem_type = metadata.get("type", "fact")
        importance = metadata.get("importance", 0.5)
        line = f"- [{mem_type}] {content} | ⭐ {importance:.2f}\n"

        lock = self._get_lock(path)
        with lock:
            if not path.exists():
                conv_id = metadata.get("source_conv_id", "unknown")
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                path.write_text(f"# 会话记忆 ({conv_id[:8]}...)\n\n> 最后更新: {now}\n\n{line}", encoding="utf-8")
            else:
                text = path.read_text(encoding="utf-8")
                text += line
                path.write_text(text, encoding="utf-8")

    def _update_index(self):
        idx_path = self.base_dir / "INDEX.md"
        lock = self._get_lock(idx_path)
        with lock:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            lines = [
                "# 记忆索引\n\n",
                f"> 最后更新: {now}\n\n",
                "## 全局记忆\n\n",
            ]
            global_dir = self.base_dir / "global"
            if global_dir.exists():
                for f in sorted(global_dir.iterdir()):
                    if f.suffix == ".md":
                        content = f.read_text(encoding="utf-8")
                        count = len([l for l in content.split("\n") if l.startswith("- [") or l.startswith("- {")])
                        lines.append(f"- [{f.stem}](global/{f.name}) — {count} 条\n")
            lines.append("\n## 会话记忆\n\n")
            sessions_dir = self.base_dir / "sessions"
            if sessions_dir.exists():
                for f in sorted(sessions_dir.iterdir(), reverse=True):
                    if f.suffix == ".md":
                        lines.append(f"- [{f.stem[:12]}...](sessions/{f.name})\n")
            idx_path.write_text("".join(lines), encoding="utf-8")

    # ====== 全量导出 ======

    def full_export(self, memories: list[dict], scope: str | None = None) -> Path:
        """全量重写导出所有记忆。返回导出目录路径。"""
        self._ensure_dirs()

        # 按类型和作用域分组
        groups: dict[str, list[dict]] = {}
        for mem in memories:
            meta = mem.get("metadata", {})
            mtype = meta.get("type", "other")
            mscope = meta.get("scope", "global")
            if scope and mscope != scope:
                continue
            key = f"{mscope}:{mtype}"
            groups.setdefault(key, []).append(mem)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        # 写 global 文件
        type_labels = {
            "preference": ("用户偏好", "# 用户偏好"),
            "fact": ("已知事实", "# 已知事实"),
            "conclusion": ("跨会话结论", "# 跨会话结论"),
            "manual": ("手动备注", "# 手动备注"),
        }
        for mtype, (label, header) in type_labels.items():
            key = f"global:{mtype}"
            items = groups.get(key, [])
            items.sort(key=lambda m: m.get("metadata", {}).get("importance", 0), reverse=True)
            path = self.base_dir / "global" / f"{mtype}s.md"
            lock = self._get_lock(path)
            with lock:
                lines = [header + "\n\n", f"> 最后更新: {now} | 共 {len(items)} 条\n\n"]
                for m in items:
                    imp = m.get("metadata", {}).get("importance", 0.5)
                    cnt = m.get("metadata", {}).get("count", 1)
                    stars = "⭐" if imp >= 0.8 else ""
                    lines.append(f"- {m['content']} — 出现 {cnt} 次 | {stars} {imp:.2f}\n")
                path.write_text("".join(lines), encoding="utf-8")

        # 写 session 文件
        session_groups: dict[str, list[dict]] = {}
        for mem in memories:
            meta = mem.get("metadata", {})
            if meta.get("scope") == "session":
                cid = meta.get("source_conv_id", "unknown")
                session_groups.setdefault(cid, []).append(mem)

        for cid, items in session_groups.items():
            path = self.base_dir / "sessions" / f"{cid}.md"
            lock = self._get_lock(path)
            with lock:
                lines = [f"# 会话记忆 ({cid[:8]}...)\n\n", f"> 最后更新: {now} | 共 {len(items)} 条\n\n"]
                for m in items:
                    mt = m.get("metadata", {}).get("type", "fact")
                    imp = m.get("metadata", {}).get("importance", 0.5)
                    lines.append(f"- [{mt}] {m['content']} | ⭐ {imp:.2f}\n")
                path.write_text("".join(lines), encoding="utf-8")

        # 更新 INDEX.md
        self._update_index()
        return self.base_dir

    def get_export_files(self) -> list[str]:
        """返回所有导出文件的相对路径列表。"""
        files = []
        for d in ("global", "sessions"):
            dpath = self.base_dir / d
            if dpath.exists():
                for f in sorted(dpath.iterdir()):
                    if f.suffix == ".md":
                        files.append(f"{d}/{f.name}")
        if (self.base_dir / "INDEX.md").exists():
            files.append("INDEX.md")
        return files
