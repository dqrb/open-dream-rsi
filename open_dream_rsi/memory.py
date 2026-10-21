"""学习链路第 6 步：持久记忆 —— 代理学到的东西跨重启还在（Hermes 风格）。

一个 ``--memory`` 目录 = 一个自我改进实例。主要文件：

* ``policies.json``     — 各类别做梦得到的策略参数（下次热启动）
* ``policy_codes.json`` — LLM 写的探索策略程序（回放打赢现任才晋升）
* ``recipes.json``      — 验证通过的解法，当热启动配方
* ``lessons.json``      — 从失败提炼的教训知识库（还要过门才能进提示）
* ``trees/``            — 每题的发现树归档（做梦的原料）
* ``events.jsonl``      — 每次决策的追加日志

看板右下角「配方 / 策略 / 教训」面板，读的就是这些。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from open_dream_rsi.core.tree import DiscoveryTree, TreeNode

#: How many already-distilled failure snippets to remember per category
#: (bounded call economy: the same failure evidence never re-buys a call).
MAX_DIGESTED_PER_CATEGORY = 64


class DreamMemory:
    """基于 JSON 的跨运行存储：策略库 + 配方库 + 教训 + 树 + 事件日志。"""

    def __init__(self, root: "str | Path" = ".dream_rsi"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._policies = self._load(self.root / "policies.json", {})
        self._policy_codes = self._load(self.root / "policy_codes.json", {})
        self._recipes = self._load(self.root / "recipes.json", {})
        self._lessons = self._load(self.root / "lessons.json", {})

    # -- low-level -------------------------------------------------------------

    @staticmethod
    def _load(path: Path, default: Any) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return default
        return default

    def _save(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic

    # -- policies ----------------------------------------------------------------

    def get_policy(self, category: str) -> Optional[Dict[str, float]]:
        """读取某类别最新做梦策略参数（temperature / exploration_depth）。"""
        entry = self._policies.get(category)
        return dict(entry["params"]) if entry else None

    def save_policy(self, category: str, params: Dict[str, float]) -> None:
        """写下做梦后的策略，供下一轮在线探索使用。"""
        self._policies[category] = {"params": dict(params), "updated_at": time.time()}
        self._save(self.root / "policies.json", self._policies)

    # -- policy code (LLM-written exploration policies) -------------------------

    def get_policy_code(self, category: str) -> Optional[Dict[str, Any]]:
        """Return ``{"code", "score"}`` of the promoted policy program, or None."""
        entry = self._policy_codes.get(category)
        return dict(entry) if entry else None

    def save_policy_code(self, category: str, code: str, score: float,
                         steps: int = 0) -> bool:
        old = self._policy_codes.get(category)
        if old and old.get("code") == code:
            return False  # unchanged — no churn, no re-save
        self._policy_codes[category] = {"code": code, "score": score,
                                        "steps": steps, "updated_at": time.time()}
        self._save(self.root / "policy_codes.json", self._policy_codes)
        return True

    # -- recipes (learned solutions) ----------------------------------------------

    def get_recipe(self, category: str) -> Optional[str]:
        """已验证的最佳解法源码（热启动用）。"""
        entry = self._recipes.get(category)
        return entry["code"] if entry else None

    def save_recipe(self, category: str, code: str, score: float) -> bool:
        """只保留更高分的解法；更差的直接丢弃。"""
        old = self._recipes.get(category)
        if old and old.get("score", 0.0) >= score:
            return False  # 保住当前最佳
        self._recipes[category] = {"code": code, "score": score, "saved_at": time.time()}
        self._save(self.root / "recipes.json", self._recipes)
        return True

    # -- lessons (curated knowledge base) ---------------------------------------

    def get_lessons(self, category: str) -> List[Dict[str, Any]]:
        """Lesson records for a category (copies; callers must not mutate)."""
        return [dict(l) for l in self._lessons.get(category, [])]

    def replace_lessons(self, category: str, entries: List[Dict[str, Any]]) -> None:
        """Atomically write the curated lesson list for a category."""
        if entries:
            self._lessons[category] = [dict(l) for l in entries]
        else:
            self._lessons.pop(category, None)
        self._save(self.root / "lessons.json", self._lessons)

    def record_lesson_usage(self, category: str, keys: List[str], win: bool = False) -> None:
        """Retrieval feedback: bump ``uses`` once per cycle on lessons that
        were surfaced into proposals, ``wins`` when the task carrying them
        solved. This is what keeps the KB honest — lessons that never help
        are pruned by the curator."""
        entries = self._lessons.get(category)
        if not entries or not keys:
            return
        from open_dream_rsi.core.curator import lesson_key  # lazy: curator owns the key format
        wanted = set(keys)
        touched = False
        for l in entries:
            if lesson_key(l) in wanted:
                l["uses"] = int(l.get("uses", 0)) + 1
                if win:
                    l["wins"] = int(l.get("wins", 0)) + 1
                touched = True
        if touched:
            self._save(self.root / "lessons.json", self._lessons)

    # -- per-task outcomes (headroom precondition input) --------------------------

    def record_task_outcome(self, category: str, solved: bool, calls: int) -> None:
        """Remember one task attempt (solved flag + proposal-call count) per
        category; the headroom verdict is computed over the recent window."""
        data = self._load(self.root / "task_outcomes.json", {})
        hist = list(data.get(category, []))
        hist.append({"solved": bool(solved), "calls": int(calls)})
        data[category] = hist[-100:]
        self._save(self.root / "task_outcomes.json", data)

    def get_task_outcomes(self, category: str) -> List[Dict[str, Any]]:
        data = self._load(self.root / "task_outcomes.json", {})
        return list(data.get(category, []))

    # -- digested failure evidence (survives lesson pruning) ----------------------

    def get_digested(self, category: str) -> List[str]:
        """Evidence snippets the curator has already distilled. Lives in its
        own file so pruning lessons never re-triggers distillation of the
        same failures (call economy + no add/prune flapping)."""
        data = self._load(self.root / "lessons_digest.json", {})
        return list(data.get(category, []))

    def mark_digested(self, category: str, snippets: List[str]) -> None:
        data = self._load(self.root / "lessons_digest.json", {})
        merged = list(dict.fromkeys(list(data.get(category, [])) + list(snippets)))
        data[category] = merged[-MAX_DIGESTED_PER_CATEGORY:]
        self._save(self.root / "lessons_digest.json", data)

    # -- 发现树归档（做梦原料）-------------------------------------------------------

    def archive_tree(self, task_id: str, tree: DiscoveryTree) -> None:
        """把本题发现树落到 ``trees/<task_id>.json``。"""
        payload = {
            "root_id": tree.root_id,
            "nodes": [vars(n) for n in tree.nodes.values()],
        }
        self._save(self.root / "trees" / f"{task_id}.json", payload)

    def load_tree(self, task_id: str) -> Optional[DiscoveryTree]:
        """加载历史树；没有则返回 None（``loop`` 会新建一棵）。"""
        path = self.root / "trees" / f"{task_id}.json"
        if not path.exists():
            return None
        data = self._load(path, None)
        if not data:
            return None
        tree = DiscoveryTree()
        tree.root_id = data.get("root_id")
        for n in data.get("nodes", []):
            node = TreeNode(**n)
            tree.nodes[node.node_id] = node
        return tree

    # -- 事件日志（看板左侧事件流同源）------------------------------------------------

    def log_event(self, kind: str, **fields: Any) -> None:
        """追加一条决策记录到 ``events.jsonl``。"""
        event = {"ts": time.time(), "kind": kind, **fields}
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
