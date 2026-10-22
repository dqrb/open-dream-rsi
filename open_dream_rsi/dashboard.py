"""实时 Web 看板：看着自主 RSI 循环干活。

    python -m open_dream_rsi dashboard                  # 假模型，零配置
    python -m open_dream_rsi dashboard --provider openai --tasks tasks.json
    python -m open_dream_rsi dashboard --provider cursor --model grok-4.7

纯标准库 http.server，无前端依赖。展示：KPI（轮次 / API / 解题 / 做梦迭代）、
实时事件流、每题尝试板、各类别做梦策略、配方库与教训库。默认用脚本化
「假 LLM」，无需 API Key 也能看完整条 改进 → 做梦 → 热启动 故事。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from open_dream_rsi.loop import AutoRSIRuntime, Task
from open_dream_rsi.memory import DreamMemory

# ---------------------------------------------------------------------------
# Demo task set + scripted "mock LLM" (first attempt buggy, fix after feedback)
# ---------------------------------------------------------------------------

#: 类别 -> (第一次错误答案, 修正后答案) —— 假模型按题号演戏
SOLUTIONS = {
    "add": (
        "def add(a, b):\n    return a - b\n",
        "def add(a, b):\n    return a + b\n",
    ),
    "clamp": (
        "def clamp(x, lo, hi):\n    return min(x, hi)\n",
        "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
    ),
    "reverse_words": (
        "def reverse_words(s):\n    return s[::-1]\n",
        "def reverse_words(s):\n    return \" \".join(s.split()[::-1])\n",
    ),
    "factorial": (
        "def factorial(n):\n    r = 1\n    for i in range(1, n):\n        r *= i\n    return r\n",
        "def factorial(n):\n    r = 1\n    for i in range(1, n + 1):\n        r *= i\n    return r\n",
    ),
    "is_palindrome": (
        "def is_palindrome(s):\n    return s == s[::-1]\n",
        "def is_palindrome(s):\n    t = s.lower()\n    return t == t[::-1]\n",
    ),
}

DEMOS = [
    dict(task_id="add1", category="add",
         prompt="实现 add(a, b)，返回两数之和。",
         tests=[{"call": "add(2, 3)", "expected": 5}, {"call": "add(-1, 1)", "expected": 0}]),
    dict(task_id="clamp1", category="clamp",
         prompt="实现 clamp(x, lo, hi)，把 x 限制在 [lo, hi] 区间内。",
         tests=[{"call": "clamp(5, 0, 10)", "expected": 5},
                {"call": "clamp(-3, 0, 10)", "expected": 0},
                {"call": "clamp(42, 0, 10)", "expected": 10}]),
    dict(task_id="rev1", category="reverse_words",
         prompt="实现 reverse_words(s)，反转单词顺序（不是反转字符）。",
         tests=[{"call": "reverse_words('a b c')", "expected": "c b a"},
                {"call": "reverse_words('hello')", "expected": "hello"}]),
    dict(task_id="fact1", category="factorial",
         prompt="实现 factorial(n)，且 factorial(0) == 1。",
         tests=[{"call": "factorial(0)", "expected": 1}, {"call": "factorial(5)", "expected": 120}]),
    dict(task_id="pal1", category="is_palindrome",
         prompt="实现 is_palindrome(s)，忽略大小写判断回文。",
         tests=[{"call": "is_palindrome('RaceCar')", "expected": True},
                {"call": "is_palindrome('abc')", "expected": False}]),
]


class MockLLM:
    """脚本化假模型：第一次交错误答案，看到失败反馈后交正确解。

    故意 sleep 一点，让看板动画看起来像真的在「思考」。
    """

    def __init__(self, latency: float = 0.25):
        self.latency = latency
        self.calls = 0

    def chat(self, messages, model=None, temperature=0.7, max_tokens=1024):
        self.calls += 1
        time.sleep(self.latency)
        system = messages[0]["content"] if messages else ""
        if "exploration policy" in system:            # policy-generation call
            return ("```python\ndef choose_action(frontier, step):\n"
                    "    if not frontier:\n        return None\n"
                    "    fresh = [n for n in frontier if n['children'] == 0]\n"
                    "    pool = fresh or frontier\n"
                    "    return max(pool, key=lambda n: (n['outcome'], n['score']))['node_id']\n```")
        if "knowledge curator" in system:              # curation call
            cat = ""
            m = __import__("re").search(r"Category \[([\w]+)\]", prompt := messages[-1]["content"])
            if m:
                cat = m.group(1)
            return ("```json\n[{\"trigger\": \"" + cat + " hidden branch\", "
                    "\"text\": \"A plausible branch whose verifier errors never change will never "
                    "improve; re-open the low-scoring sibling branch where the fix idea first appeared.\"}]\n```")
        prompt = messages[-1]["content"]
        category = prompt.split("[")[1].split("]")[0] if "[" in prompt else "?"
        buggy, fixed = SOLUTIONS.get(category, (None, None))
        if fixed and fixed.strip() in prompt:              # recipe warm start: reuse best
            code = fixed
        elif "(none yet)" in prompt and "failure feedback:\n(none)" in prompt:
            code = buggy                                    # never seen this task: ship a bug
        else:                                               # recipe or verifier feedback
            code = fixed
        return f"```python\n{code}```"


# ---------------------------------------------------------------------------
# Shared dashboard state, fed by AutoRSIRuntime.on_event
# ---------------------------------------------------------------------------


class DashboardState:
    def __init__(self, demo_tasks: List[Task]):
        self.lock = threading.Lock()
        self.demo_tasks = demo_tasks
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.cycles = 0
            self.api_calls_total = 0
            self.api_calls_cycle = 0
            self.dreams_total = 0
            self.paused = False
            self.events: List[Dict[str, Any]] = []
            self.tasks: Dict[str, Dict[str, Any]] = {
                t.task_id: {"task_id": t.task_id, "category": t.category,
                            "prompt": t.prompt, "tests": t.tests, "status": "pending",
                            "attempts": []}
                for t in self.demo_tasks
            }
            self.policy_history: Dict[str, List[Dict[str, Any]]] = {}
            self.recipes: Dict[str, Dict[str, Any]] = {}

    def handle_event(self, kind: str, data: Dict[str, Any]) -> None:
        with self.lock:
            ev = {"ts": time.time(), "kind": kind, **data}
            self.events.append(ev)
            if len(self.events) > 400:
                self.events = self.events[-400:]
            tid = data.get("task_id")
            task = self.tasks.get(tid) if tid else None
            if kind == "llm_call":
                self.api_calls_total += 1
                self.api_calls_cycle = getattr(self, "api_calls_cycle", 0) + 1
                if task:
                    task["status"] = "attempting"
            elif kind == "verification" and task:
                task["attempts"].append({
                    "score": data.get("score", 0.0), "ok": data.get("ok"),
                    "errors": data.get("errors"), "code": data.get("code")})
            elif kind == "task_done" and task:
                task["status"] = "solved" if data.get("solved") else "pending"
            elif kind == "dream_done":
                cat = data.get("category", "?")
                pol = data.get("policy", {})
                self.policy_history.setdefault(cat, []).append(
                    {**pol, "ts": ev["ts"], "cycle": self.cycles + 1})
                self.policy_history[cat] = self.policy_history[cat][-40:]
            elif kind == "cycle_start":
                self.api_calls_cycle = 0
            elif kind == "cycle_done":
                self.cycles += 1
                self.dreams_total += data.get("dream_iterations", 0)
                for t, meta in self.tasks.items():
                    if meta["status"] == "solved" and t not in self.recipes:
                        pass  # recipes are loaded by snapshot() from memory

    def snapshot(self, memory: DreamMemory, budget: int, interval: float) -> Dict[str, Any]:
        with self.lock:
            recipes = {}
            for cat, entry in getattr(memory, "_recipes", {}).items():
                recipes[cat] = {"code": entry["code"], "score": entry.get("score")}
            policy_codes = {}
            for cat, entry in getattr(memory, "_policy_codes", {}).items():
                policy_codes[cat] = {"code": entry["code"], "score": entry.get("score")}
            lessons = {}
            for cat, entries in getattr(memory, "_lessons", {}).items():
                lessons[cat] = [{"trigger": l.get("trigger", ""),
                                 "text": l.get("text", ""),
                                 "wins": l.get("wins", 0), "uses": l.get("uses", 0)}
                                for l in entries]
            policies = {cat: hist[-1] for cat, hist in self.policy_history.items() if hist}
            return {
                "cycles": self.cycles,
                "api_calls_total": self.api_calls_total,
                "api_calls_cycle": getattr(self, "api_calls_cycle", 0),
                "budget": budget,
                "interval": interval,
                "dreams_total": self.dreams_total,
                "paused": self.paused,
                "solved": sum(1 for t in self.tasks.values() if t["status"] == "solved"),
                "tasks_total": len(self.tasks),
                "tasks": list(self.tasks.values()),
                "events": self.events[-120:],
                "policies": policies,
                "policy_history": self.policy_history,
                "policy_codes": policy_codes,
                "recipes": recipes,
                "lessons": lessons,
            }


# ---------------------------------------------------------------------------
# Supervisor thread + HTTP server
# ---------------------------------------------------------------------------


def run_dashboard(host: str = "0.0.0.0", port: int = 8765, provider: str = "mock",
                  model: Optional[str] = None, tasks_file: Optional[str] = None,
                  interval: float = 1.0, budget: int = 40, memory_root: str = ".dream_rsi_dashboard",
                  max_tokens: int = 2048, block: bool = True) -> None:
    if tasks_file:
        raw = json.loads(open(tasks_file, encoding="utf-8").read())
        demo_tasks = [Task(**t) for t in raw]
    else:
        demo_tasks = [Task(max_attempts=3, **t) for t in DEMOS]

    if provider == "mock":
        client: Any = MockLLM()
        provider_label = "假模型（脚本）"
    else:
        from open_dream_rsi.llm import LLMConfig, OpenAICompatibleClient

        cfg = LLMConfig.from_preset(provider)
        if model:
            cfg.model = model
        client = OpenAICompatibleClient(cfg)
        provider_label = f"{provider}（{cfg.base_url}）"

    # 每次启动用新记忆目录，避免上次残留脏树干扰演示
    memory_root = f"{memory_root}-{int(time.time())}"

    state = DashboardState(demo_tasks)
    holder: Dict[str, Any] = {"memory": DreamMemory(memory_root)}

    def build_runtime() -> AutoRSIRuntime:
        # 看板在终端启动时默认打印中文链路，方便对照网页事件流
        return AutoRSIRuntime(
            client=client, memory=holder["memory"], tasks=demo_tasks,
            api_call_budget=budget, dream_iterations=90, interval_seconds=interval,
            max_tokens=max_tokens, on_event=state.handle_event,
            verbose=True,
        )

    runtime = build_runtime()

    def worker() -> None:
        cycle = 0
        while True:
            if state.paused:
                time.sleep(0.2)
                continue
            cycle += 1
            runtime.api_calls_used = 0
            print(f"[链路] ═══ 看板循环第 {cycle} 轮 ═══", flush=True)
            try:
                report = runtime.run_once()
                print(
                    f"[链路] ═══ 第 {cycle} 轮收工 | "
                    f"{report.tasks_solved}/{report.tasks_attempted} 题已解 | "
                    f"API {report.api_calls} | 做梦 {report.dream_iterations} ═══",
                    flush=True,
                )
            except Exception as exc:  # supervisor must survive
                state.handle_event("runtime_error", {"error": str(exc)[:300]})
                print(f"[链路] 💥 本轮异常: {exc}", flush=True)
            time.sleep(interval)

    threading.Thread(target=worker, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path.startswith("/api/snapshot"):
                snap = state.snapshot(holder["memory"], budget, interval)
                snap["provider"] = provider_label
                snap["memory"] = str(holder["memory"].root)
                self._send(200, json.dumps(snap).encode("utf-8"), "application/json")
            elif self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if self.path == "/api/pause":
                state.paused = not state.paused
                self._send(200, json.dumps({"paused": state.paused}).encode(), "application/json")
            elif self.path == "/api/reset":
                state.reset()
                holder["memory"] = DreamMemory(memory_root + f"-{time.time():.0f}")
                nonlocal runtime
                runtime = build_runtime()
                self._send(200, b'{"ok": true}', "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, format, *args):  # silence request logging
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"[odr] 看板已启动 {url}  模型={provider_label}  "
          f"任务数={len(demo_tasks)}  (Ctrl-C 停止)", flush=True)
    if block:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n[odr] dashboard stopped.", flush=True)
            srv.shutdown()


# ---------------------------------------------------------------------------
# The single-page UI (vanilla JS, polls /api/snapshot)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open Dream-RSI — 实时看板</title>
<style>
:root{--bg:#0a0d13;--card:#101625;--card2:#0d131f;--line:#1d2739;--txt:#e6edf3;
--mut:#8b98ad;--acc:#6c8cff;--acc2:#9a6cff;--ok:#3fd68f;--bad:#ff6b81;--dream:#b18cff;}
*{box-sizing:border-box}html{scrollbar-color:var(--line) var(--bg)}
body{margin:0;background:radial-gradient(1200px 500px at 70% -10%,#141b33 0%,var(--bg) 60%);
color:var(--txt);font:14px/1.5 -apple-system,'Segoe UI',Roboto,Inter,Ubuntu,sans-serif;padding:24px}
h1{font-size:20px;margin:0;display:flex;align-items:center;gap:10px}
.moon{filter:drop-shadow(0 0 12px rgba(154,108,255,.8))}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:10px}
.chip{padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--mut);
font-size:12px;background:var(--card)}
.chip.ok{color:var(--ok);border-color:#1d4436}
.chip.bad{color:var(--bad);border-color:#4a2630}
button{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:8px;
padding:6px 14px;cursor:pointer;font-size:13px}
button:hover{border-color:var(--acc)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi b{font-size:26px;display:block;font-variant-numeric:tabular-nums}
.kpi span{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.kpi .sub{font-size:11px;color:var(--mut);margin-top:2px}
.grid{display:grid;grid-template-columns:minmax(340px,5fr) minmax(420px,7fr);gap:14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
.panel h2{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:var(--mut)}
.ev{padding:6px 10px;border-left:3px solid var(--line);margin:5px 0;background:var(--card2);
border-radius:0 8px 8px 0;font-size:13px;animation:in .3s ease}
@keyframes in{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
.ev .t{color:var(--mut);font-size:11px;margin-right:8px;font-variant-numeric:tabular-nums}
.ev.llm_call{border-color:var(--acc)}.ev.verification{border-color:var(--ok)}
.ev.verification.bad{border-color:var(--bad)}.ev.dreaming,.ev.dream_done{border-color:var(--dream)}
.ev.policy_gen,.ev.policy_promoted{border-color:var(--dream)}.ev.policy_rejected{border-color:var(--bad)}
.ev.cycle_done{border-color:#2a3955;color:var(--mut)}
.ev.task_done{border-color:var(--acc2)}
.task{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.task .head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.task .name{font-weight:600}
.pill{font-size:11px;padding:2px 9px;border-radius:999px;border:1px solid var(--line);color:var(--mut)}
.pill.solved{color:var(--ok);border-color:#1d4436;background:#0d1f18}
.pill.attempting{color:var(--acc);border-color:#28406e;background:#0e1830}
.attempts{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.att{flex:1;min-width:90px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.att .lbl{font-size:10px;color:var(--mut);text-transform:uppercase}
.bar{height:5px;border-radius:3px;background:#1a2336;overflow:hidden;margin-top:4px}
.bar i{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,var(--acc),var(--acc2));
transition:width .5s ease}
.err{color:var(--bad);font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
white-space:pre-wrap;margin-top:6px}
code,pre{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:10px;
overflow:auto;color:#c8d3e8;margin:6px 0}
.pol{display:grid;grid-template-columns:110px 1fr 60px 1fr 60px;gap:6px 10px;align-items:center;
font-size:12px;margin:8px 0}
.pol .cat{color:var(--mut)}
.pbar{height:8px;border-radius:4px;background:#1a2336;overflow:hidden}
.pbar i{display:block;height:100%;border-radius:4px}
.temp i{background:linear-gradient(90deg,#3fd68f,#6c8cff)}
.dep i{background:linear-gradient(90deg,#9a6cff,#ff6bd6)}
.val{font-variant-numeric:tabular-nums;color:var(--txt)}
.foot{color:var(--mut);font-size:12px;margin-top:6px}
details summary{cursor:pointer;color:var(--acc);font-size:12px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="top">
  <h1><span class="moon">🌙</span> Open Dream-RSI <span class="chip" id="provider">…</span></h1>
  <div>
    <span class="chip" id="memchip">记忆目录</span>
    <button onclick="act('pause')" id="pauseBtn">⏸ 暂停</button>
    <button onclick="act('reset')">↺ 重置</button>
  </div>
</div>

<div class="kpis">
  <div class="kpi"><span>循环轮次</span><b id="kCycles">0</b><div class="sub">自主醒来次数</div></div>
  <div class="kpi"><span>已解题目</span><b id="kSolved">0/0</b><div class="sub">通过测试的任务</div></div>
  <div class="kpi"><span>API 调用</span><b id="kApi">0</b><div class="sub" id="kBudget">本轮预算</div></div>
  <div class="kpi"><span>做梦迭代</span><b id="kDreams">0</b><div class="sub">离线、免费 🌙</div></div>
</div>

<div class="grid">
  <div>
    <div class="panel"><h2>实时事件流</h2><div id="feed"></div></div>
  </div>
  <div>
    <div class="panel"><h2>任务板 — 在线尝试</h2><div id="tasks"></div></div>
    <div class="panel"><h2>做梦策略（离线学习）</h2><div id="policies"></div></div>
    <div class="panel"><h2>演化策略代码（LLM 编写，回放门控）</h2><div id="policycodes"></div></div>
    <div class="panel"><h2>配方库 — 已学会的解法</h2><div id="recipes"></div>
      <div class="foot">open-dream-rsi · 仅标准库 · 策略与配方跨重启保留</div></div>
    <div class="panel"><h2>知识库 — 从失败提炼的教训</h2><div id="lessons"></div></div>
  </div>
</div>

<script>
const $=s=>document.querySelector(s);
const fmt=t=>new Date(t*1000).toLocaleTimeString('zh-CN');
const STATUS={pending:'待开始',attempting:'尝试中',solved:'已解决'};
const ICONS={llm_call:"📡",verification:"🧪",dreaming:"🌙",dream_done:"✨",task_done:"🎯",
cycle_start:"▶",cycle_done:"🔄",runtime_error:"💥",policy_gen:"🧬",policy_promoted:"🧬",policy_rejected:"🚫",
knowledge_curate:"📚",lessons_curated:"📚",lesson_rejected:"🚫"};
async function act(a){await fetch('/api/'+a,{method:'POST'});refresh();}
function evLine(e){
  const d=document.createElement('div');
  let cls=e.kind,txt='';
  if(e.kind==='verification'){cls+=' '+(e.ok?'':'bad');
    txt=`${e.task_id} → 得分 ${e.score}${e.ok?' ✅':' ❌'}${!e.ok&&e.errors?' — '+String(e.errors).slice(0,80):''}`;}
  else if(e.kind==='llm_call')txt=`${e.task_id}: 询问模型 @温度=${(+e.temperature).toFixed(2)}`;
  else if(e.kind==='dreaming')txt=`${e.task_id}: 在 ${e.nodes} 个节点上做梦 ×${e.iterations}`;
  else if(e.kind==='dream_done')txt=`${e.task_id}: 策略 → 温度=${(+e.policy.temperature).toFixed(2)}, 深度=${(+e.policy.exploration_depth).toFixed(2)}`;
  else if(e.kind==='task_done')txt=`${e.task_id} ${e.solved?'已解决 🏆':'未解决'}（${e.nodes} 个节点）`;
  else if(e.kind==='policy_gen')txt=`${e.task_id}: 请模型写新探索策略（现任分 ${(+e.incumbent_score).toFixed(3)}）`;
  else if(e.kind==='policy_promoted')txt=`${e.task_id}: 🧬 新策略已晋升（回放分 ${(+e.score).toFixed(3)}）`;
  else if(e.kind==='policy_rejected')txt=`${e.task_id}: 策略被拒绝 — ${String(e.reason||'').slice(0,90)}`;
  else if(e.kind==='knowledge_curate')txt=`${e.task_id}: 📚 策展消化 ${e.new_failures} 条新失败`;
  else if(e.kind==='lessons_curated')txt=`${e.task_id}: 📚 知识库 +${(e.added||[]).length} 条教训，合并 ${e.merged}`;
  else if(e.kind==='lesson_rejected')txt=`${e.task_id}: 教训被拒绝 — ${String(e.reason||'').slice(0,90)}`;
  else if(e.kind==='cycle_done')txt=`本轮结束 — ${e.tasks_solved}/${e.tasks_attempted} 题已解，${e.api_calls} 次调用`;
  else if(e.kind==='cycle_start')txt='本轮开始：'+(e.tasks||[]).join(', ');
  else if(e.kind==='runtime_error')txt=e.error;
  d.className='ev '+cls;
  d.innerHTML=`<span class="t">${fmt(e.ts)}</span>${ICONS[e.kind]||'·'} ${txt}`;
  return d;
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function refresh(){
  const s=await (await fetch('/api/snapshot')).json();
  $('#kCycles').textContent=s.cycles;
  $('#kSolved').textContent=`${s.solved}/${s.tasks_total}`;
  $('#kApi').textContent=s.api_calls_total;
  $('#kBudget').textContent=`本轮已用 ${s.api_calls_cycle}/${s.budget}`;
  $('#kDreams').textContent=s.dreams_total;
  $('#provider').textContent=s.provider;
  $('#memchip').textContent='记忆：'+s.memory;
  $('#pauseBtn').textContent=s.paused?'▶ 继续':'⏸ 暂停';
  const feed=$('#feed');feed.innerHTML='';
  s.events.slice().reverse().forEach(e=>feed.appendChild(evLine(e)));
  $('#tasks').innerHTML=s.tasks.map(t=>{
    const att=(t.attempts||[]).slice(-4).map(a=>`<div class="att"><div class="lbl">得分 ${a.score}</div>
      <div class="bar"><i style="width:${Math.round(a.score*100)}%"></i></div>
      ${a.ok?'':`<div class="err">${esc((Array.isArray(a.errors)?a.errors.join('\n'):a.errors||'').slice(0,120))}</div>`}
      ${a.code?`<details><summary>查看代码</summary><pre>${esc(a.code)}</pre></details>`:''}</div>`).join('');
    return `<div class="task"><div class="head"><span class="name">${t.task_id}
      <span style="color:var(--mut);font-weight:400">· ${t.category}</span></span>
      <span class="pill ${t.status}">${STATUS[t.status]||t.status}</span></div>
      <div style="color:var(--mut);font-size:12px;margin-top:2px">${esc(t.prompt)}</div>
      <div class="attempts">${att||'<div class="att"><div class="lbl">等待首次尝试</div></div>'}</div></div>`;
  }).join('');
  const cats=Object.keys(s.policies||{});
  $('#policies').innerHTML=cats.length?cats.map(c=>{
    const p=s.policies[c];
    return `<div class="pol"><span class="cat">${c}</span>
      <span>温度<div class="pbar temp"><i style="width:${Math.min(100,p.temperature/1.2*100)}%"></i></div></span><span class="val">${(+p.temperature).toFixed(2)}</span>
      <span>深度<div class="pbar dep"><i style="width:${Math.min(100,p.exploration_depth/6*100)}%"></i></div></span><span class="val">${(+p.exploration_depth).toFixed(2)}</span></div>`;
  }).join(''):'<span style="color:var(--mut)">还没有做梦结果…</span>';
  const pc=Object.entries(s.policy_codes||{});
  $('#policycodes').innerHTML=pc.length?pc.map(([c,e])=>
    `<div style="margin-bottom:8px"><b style="font-size:12px;color:var(--dream)">🧬 ${c}</b>
     <span style="color:var(--mut);font-size:11px">回放分 ${(+e.score).toFixed(3)}</span><pre>${esc(e.code)}</pre></div>`).join('')
    :'<span style="color:var(--mut)">尚无晋升的策略代码 — 只有回放打赢现任才会晋升</span>';
  const r=Object.entries(s.recipes||{});
  $('#recipes').innerHTML=r.length?r.map(([c,e])=>
    `<div style="margin-bottom:8px"><b style="font-size:12px;color:var(--ok)">✓ ${c}</b>
     <span style="color:var(--mut);font-size:11px">得分 ${e.score}</span><pre>${esc(e.code)}</pre></div>`).join('')
    :'<span style="color:var(--mut)">空 — 验证通过的解法会保存在这里</span>';
  const ls=Object.entries(s.lessons||{});
  $('#lessons').innerHTML=ls.length?ls.map(([c,arr])=>
    `<div style="margin-bottom:8px"><b style="font-size:12px;color:var(--dream)">📚 ${c}</b>`+
    arr.map(l=>`<div style="font-size:12px;margin:3px 0 3px 10px"><span style="color:var(--mut)">[${esc(l.trigger)}]</span> ${esc(l.text)}
      <span style="color:var(--mut);font-size:10px">胜 ${l.wins} · 用 ${l.uses}</span></div>`).join('')+`</div>`).join('')
    :'<span style="color:var(--mut)">空 — 策展器会把失败提炼成教训写在这里</span>';
}
setInterval(refresh,700);refresh();
</script>
</body>
</html>"""
