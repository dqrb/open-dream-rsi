# Open Dream-RSI

> **📄 预印本：** *Open Dream-RSI: An Open-Source Library for Recursive
> Self-Improvement Around a Frozen LLM, with Replay-Gated Learned Policies and
> a Curated Knowledge Base* — P. Orwat, 2026.
> [**PDF**](paper/main.pdf) · [**LaTeX 源码**](paper/main.tex)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-321%20passing-brightgreen)](tests/)

> 命令、路径与代码块保留英文原文，便于直接复制执行。

## 这是什么？

每个 AI Agent 都在反复交同一笔税：任务失败、想明白哪里错了，会话一结束就把理解扔掉。Open Dream-RSI 把学到的东西留下来——**且不碰模型本身**。

大模型保持**冻结**。改进的是它*周围*的一切：

- **探索策略**（甚至可由 LLM 自己改写成 Python 代码，须经回放门控才被信任），
- **已验证解法**库（recipes），用作热启动，
- 从失败中蒸馏的**策展教训**——每一条只有在回放上*可度量地*有帮助后才晋升，
- 以及策略打分所依据的**世界模型**：记录每次尝试的发现树（Discovery Tree），并由 Sentinel 引擎附上可安全回放的错误类事实。

运行之间系统会「做梦」：在进程内对已记录历史回放成千上万种策略，**零 API 成本**，只留下打赢现任的结果。这是 *Recursive Self-Improvement through Evolving Worlds*（Dream-RSI，Zheng 等，[arXiv:2609.14858](https://arxiv.org/abs/2609.14858)）的开源实现。

**零运行时第三方依赖**——仅 Python 3.10+ 标准库，约 10.2k 行库代码、约 5.1k 行测试，无构建步骤。

**不是又一个记忆插件。** 经验记忆系统记录 Agent 学到的东西并原样复用。ODR 是 *evolving worlds* 循环：每次尝试记入发现树，运行之间系统做梦——针对该记录世界回放成千上万策略变体。没有任何东西仅凭模型「嘴上说好」就晋升：候选策略、配方或教训只有在反事实回放中幸存后，才替换现任。

**评估政策：** 本项目的性能主张仅针对**一个基准——goose × TravelPlanner**，公开且由基准自带评测器打分（见[下文](#基准-goose--travelplanner公开外部评分)）。脚本套件（`bench`、`bench-policy`）是**确定性自检**——针对机器行为的回归契约——**不承载模型质量声明**。

---

## 它如何工作

```
                        ┌───────────────┐
                        │  Frozen LLM   │
                        └──────┬────────┘
                               │ proposals
              online loop      ▼      offline dream
        ┌──────────── DiscoveryTree ──────────── ReplaySimulator ──────────┐
        │                     │                          │                 │
        │              node.sentinel              counterfactual          │
        │              (Sentinel facts,           rollout scoring         │
        │               replay-safe)                    │                 │
        │                     │                         ▼                 │
        │                     └────→ Policy improvement ────→ next cycle  │
        │                                                                   │
   SENTINEL MODE (tool-execution layer)                                     │
        │          │             │                                          │
   error-class   nudge       permute-gate ──→ block tool calls             │
   ledger        (once/session)              past 80% of budget ───────────┘
```

1. **在线执行**——Agent 用真实 LLM 与沙箱验证器尝试每道任务，把每次尝试追加到按任务划分的*发现树*（代码、测试反馈、得分、一行计划；若接入 Sentinel 引擎，还有结构化错误类事实与明确终止原因）。
2. **离线做梦**——不付真实世界 rollout 的费用，而是在已记录历史上「做梦」：在进程内回放模拟器中对成千上万策略变体打分，外部调用成本为零。最优参数、程序与配方被持久化，并指导下一轮。

在参数级做梦之外，循环闭合了论文第 3 节（「用代码做梦」）：每轮 LLM 可把**探索策略本身**改写成小 Python 程序 `choose_action(frontier, step)`。候选经静态校验（AST 门），仅在硬化子进程沙箱中执行，并用记录树上的**反事实回放 rollout** 打分。仅在有证据时替换现任——且现任会在*当前*树上重新打分，不信任其存储分。崩溃或作弊的策略永远打不破循环：展开回退到贪心基线。

---

## 快速开始

```bash
git clone https://github.com/patrykorwat/open-dream-rsi.git
cd open-dream-rsi
pip install -e .
```

**指向一个模型——也可以不指。** 任意 OpenAI 兼容端点均可；密钥只从环境变量读取。*模型名从端点自动发现*（`GET /v1/models`），因此指向本地 vLLM/Ollama 通常只需 base URL：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1   # vLLM / Ollama / 任意网关
export OPENAI_API_KEY=***                         # 本地服务任意非空令牌即可
# 不必设 ODR_LLM_MODEL：使用端点提供的第一个模型。
# 仅当端点有多个模型时，才用 ODR_LLM_MODEL 钉死其中一个。
```

在 `tasks.json` 中排队任务：

```json
[{
  "task_id": "add1",
  "category": "math",
  "prompt": "Implement add(a, b) returning the sum.",
  "tests": [{"call": "add(2, 3)", "expected": 5}],
  "max_attempts": 3
}]
```

运行 supervisor——单次、守护进程，或推荐的自动化 `dream` 周期（一条命令：导入会话证据、在证据充分时跑晋升门、把挣来的教训发布为 skills）：

```bash
python -m open_dream_rsi --memory .dream_rsi dream \
    --sessions ~/.hermes/state.db --skills-out ~/.hermes/skills/odr-curated
python -m open_dream_rsi loop --tasks tasks.json --interval 300   # 守护模式
python -m open_dream_rsi loop --tasks tasks.json --once           # 单轮
python -m open_dream_rsi status                                   # 学到了什么
```

没有 Key？确定性部分仍可跑：`bench`、`bench-policy`、`gate-replay` 与看板都使用脚本化 mock 客户端。完整进程内演示（带 mock OpenAI 服务）：`examples/live_loop_demo.py`。

不想用 CLI？**同一循环可作为 MCP 服务器跑在你现有的编程 Agent 里**——这是最快的接入方式：

```bash
python3 -m open_dream_rsi mcp --tasks ./tasks.json --memory ./.dream_rsi
```

…下一节给出各 Agent 的具体配置块。

---

## 接入你常用的 Agent

循环讲 **MCP**（Model Context Protocol）。一块配置即可让日常 Agent 给做梦器排队任务、取回已验证配方、查阅策展教训——**无需额外 LLM 配置**：做梦器自己解析大脑（环境变量 → 本地 goose 配置 → 本机 vLLM），并询问端点使用哪个模型。

| 工具 | 作用 |
|---|---|
| `odr_status` | 循环已学到什么（策略、配方、事件） |
| `odr_recipes` | 某类别的最佳**已验证**解法——用作热启动 |
| `odr_lessons` | 某类别 / 检索查询的策展失败教训 |
| `odr_add_task` | 排队任务（prompt + tests，或 prompt + criteria）——无测试任务由完成判定器评判 |
| `odr_run_once` | 立即跑一轮改进（有 API 预算上限） |
| `odr_dream` | 自动化周期：导入宿主会话证据、蒸馏策展教训、跑晋升门、发布 skills |

工具按客户端加前缀注册（例如 Hermes 的 `mcp_open_dream_rsi_odr_run_once`）。**没有模型会主动调用这些工具**——须通过 recipe 的 `instructions:`、hook，或显式要求（「再试前先查 odr_recipes」）。让循环更主动的技巧：在项目 `AGENTS.md` 中加入：

```markdown
## Self-improvement loop (MCP: open-dream-rsi)
- Before implementing a self-contained Python utility, call `odr_recipes`
  and `odr_lessons` for its category; reuse a verified recipe verbatim.
- When you finish a hard, testable function, queue it with `odr_add_task`
  (task_id = function name, tests = your test cases) so the loop can
  dream over it offline.
```

### OpenCode

在你**工作项目根目录**创建（或合并进）`opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "open-dream-rsi": {
      "type": "local",
      "command": ["python3", "-m", "open_dream_rsi", "mcp",
                  "--tasks", "./tasks.json", "--memory", "./.dream_rsi"],
      "enabled": true
    }
  }
}
```

启动 OpenCode 并输入 `/mcps`——应看到 `open-dream-rsi: connected`。

### Goose

推荐——脚本会接好一切（诊断安装、解析 goose provider、可选启动借脑代理，并把扩展块写入 `~/.config/goose/config.yaml`）：

```bash
./scripts/odr_goose_setup.sh            # 诊断 + 配置 goose + 启动代理
./scripts/odr_goose_setup.sh --check    # 仅诊断，不改动
./scripts/odr_goose_setup.sh --no-proxy # 跳过代理（使用 provider='goose'）
```

或手动加入 `~/.config/goose/config.yaml`：

```yaml
extensions:
  open-dream-rsi:
    enabled: true
    type: stdio
    name: open-dream-rsi
    description: "Dream-RSI self-improvement loop. Use odr_add_task to queue a
      Python task with tests, odr_run_once to run an improvement cycle,
      odr_recipes/odr_lessons to reuse verified solutions and failure lessons,
      odr_status to inspect what the loop has learned."
    cmd: python3
    args: ["-m", "open_dream_rsi", "mcp",
           "--tasks", "/ABSOLUTE/PATH/projects/myproject/tasks.json",
           "--memory", "/ABSOLUTE/PATH/projects/myproject/.dream_rsi"]
    timeout: 300
```

Goose 拉起扩展时**没有 shell，且环境被清洗**：须用绝对路径（不要 `~`，不要相对 `./`），并通过 `envs:` 传入服务器需要的环境——它不会继承你 export 的 `OPENAI_*`。改配置后：完全退出 goose（桌面端启动时缓存配置），再启动，并在会话扩展选择器中激活 `open-dream-rsi`。一次性替代（不改配置）：
`goose session --with-extension "python3 -m open_dream_rsi mcp --tasks ./tasks.json --memory ./.dream_rsi"`。

两种一等方式让做梦器跑在**goose 所用的同一模型**上（无需第二把 API Key）：`provider: "goose"` 自行读取 `~/.config/goose/config.yaml`（CLI 与桌面方言、`custom_providers/*.json`、`secrets.yaml`、macOS 钥匙串）；以及 `python3 -m open_dream_rsi proxy --port 8799` 把 goose 的脑暴露为普通 OpenAI 兼容端点——详见 [docs/integrations.md](docs/integrations.md)。

### Hermes Agent

在 `~/.hermes/config.yaml` 的 `mcp_servers` 下添加（或经看板 MCP 目录）：

```yaml
mcp_servers:
  open-dream-rsi:
    command: "python3"
    args: ["-m", "open_dream_rsi", "mcp",
           "--tasks", "/ABS/PATH/open-dream-rsi/tasks.json",
           "--memory", "/ABS/PATH/open-dream-rsi/.dream_rsi"]
    timeout: 300
    env:
      OPENAI_BASE_URL: "http://YOUR-VLLM-HOST:8000/v1"
      OPENAI_API_KEY: "***"        # 本地服务任意令牌
```

重启 Hermes；工具以 `mcp_open_dream_rsi_*` 出现在各平台工具集中。Hermes 也是目前具备 MCP **sampling** 支持的宿主——若希望做梦器经协议借用宿主模型（今天 ODR 仍自行解析端点）。

**Hermes in Docker：** 只读挂载仓库并 export `PYTHONPATH`（镜像内无 pip 层），再为服务器块显式写 `env:`（容器内没有 goose 配置）：

```dockerfile
ENV PYTHONPATH=/opt/open-dream-rsi
```

```yaml
# compose.yaml -> services.hermes
volumes:
  - /home/USER/git/open-dream-rsi:/opt/open-dream-rsi:ro
  - ./data:/opt/data
```

把 `--tasks`/`--memory` 指到挂载数据卷下路径（如 `/opt/data/dream-rsi/...`），以便重建后记忆仍在。更新 = 宿主 `git pull` + 重启容器——无需重建镜像。

### Codex CLI

```bash
codex mcp add open-dream-rsi -- python3 -m open_dream_rsi mcp \
  --tasks /ABS/PATH/tasks.json --memory /ABS/PATH/.dream_rsi
```

用 `codex mcp list` 验证（或在 `~/.codex/config.toml` 中写 `[mcp_servers.open-dream-rsi]` 的 `command`/`args`）。

### Claude Code

```bash
claude mcp add open-dream-rsi -- python3 -m open_dream_rsi mcp \
  --tasks /ABS/PATH/tasks.json --memory /ABS/PATH/.dream_rsi
```

在会话内用 `/mcp` 检查。

### Claude Cowork / claude.ai（仅远程）

Cowork 连接 Anthropic 云侧经纪的**远程** MCP URL。提供 HTTP、做隧道，把 URL 加为自定义连接器：

```bash
python3 -m open_dream_rsi mcp --http --host 0.0.0.0 --port 8800 \
  --tasks /ABS/PATH/tasks.json --memory /ABS/PATH/.dream_rsi
# 隧道 8800（tailscale funnel / cloudflared）-> Settings -> Connectors -> Add custom
```

端点实现无状态 Streamable-HTTP 配置（`POST /mcp`，每请求一条 JSON-RPC；`GET /health`）。**把该 URL 当凭证**：谁够得到谁就能排队任务并花你的 LLM 预算。

### DeepSeek Harness

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）通过官方桥接（`@deepseek-ai/dsh-mcp-client`）跑外部 MCP。包已发布到 npm：

```bash
dsh plugin --profile web add dsh-open-dream-rsi
```

工具名为 `mcp__open_dream_rsi__<tool>`（`odr_status`、`odr_recipes`、`odr_lessons`、`odr_add_task`、`odr_run_once`、`odr_dream`）。包内自带 Python 服务器——PATH 上有 `python3`（3.10+）即可，无需 pip。默认把 `--tasks`/`--memory` 指到 `dsh` 启动目录旁的 `tasks.json`/`.dream_rsi`；可在 profile 的 `cordis.patch.yml` 中按同 id 覆盖 `mcp-open-dream-rsi` 行。
DSH 会清洗子进程环境（匹配 `KEY|PASSWORD|SECRET|TOKEN` 的名字及所有 `DSH_*`）。桥接（`childEnv()`）只回传**实际已设置**的 LLM 变量——空的 `ODR_LLM_PRESET=""` 不会进子进程（旧版 ODR 会直接拿去选 provider 并死于 `Unknown preset ''`）；当 harness 进程没有 `OPENAI_BASE_URL` 时，把子进程指到 harness 进程内 OpenAI 兼容代理：做梦器每次 LLM 调用由 harness 已配置的模型（`ctx.llm.stream`）服务，无需 API Key 或 preset。harness 环境里若有真实 `OPENAI_BASE_URL` 则绕过代理（直连）。手工 overlay 通过 `process.env.VAR || undefined` 回传 `OPENAI_*`/`ODR_LLM_*`，让 ODR 自行解析端点（环境、如 `ODR_LLM_PRESET=local` 的 preset，或 `http://127.0.0.1:8000/v1` 自动探测）。
`toolCallTimeoutMs: 1800000` 替换默认 60s，因为 `odr_run_once`/`odr_dream` 往往跑很多分钟。

更想手工 overlay？
`dsh --patch /ABS/PATH/TO/open-dream-rsi/plugins/deepseek_harness/odr.cordis.yml web`
插入同行并带显式绝对路径（远程 Streamable-HTTP 变体 `odr-http.cordis.yml` 指向 `mcp --http` 端点——URL 同样是凭证）。完整说明：[plugins/deepseek_harness/README.md](plugins/deepseek_harness/README.md)。

### 零配置（适用于上述全部）

`odr_run_once` 按此顺序自动解析大脑：

1. 服务器环境中的 `OPENAI_BASE_URL` / `OPENAI_API_KEY`，
2. **你的本地 goose 配置**（`~/.config/goose`：`active_provider` + `providers:` + `custom_providers/*.json` + `secrets.yaml` + macOS 钥匙串）——从磁盘读取，因此能扛住 Hermes/Codex/Claude Code 对子进程的环境清洗，
3. `http://127.0.0.1:8000/v1`（vLLM/Ollama 默认）。

服务模型自动发现（`GET /v1/models`），除非钉死。自托管端点默认关闭隐藏推理（`chat_template_kwargs.enable_thinking=false`——否则 thinking-on 的 Qwen 会烧光整个补全预算）；拒绝该标志的端点会自动干净重试一次。空任务队列是良性空操作并给提示，永不报错。

各宿主完整说明（含 goose 代理的 launchd keep-alive 与排错）：**[docs/integrations.md](docs/integrations.md)**。

---

## 提供商（Providers）

Agent 对接任意 **OpenAI 兼容** 的 `POST {base_url}/chat/completions` 服务。API Key **仅**从环境变量读取。

| 提供商 | Preset | Base URL | Key 环境变量 |
|---|---|---|---|
| OpenAI | `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| Cursor（经本地代理） | `cursor` | `http://127.0.0.1:3000/v1` | `CURSOR_API_KEY`（可选） |
| 本地（vLLM / Ollama / LM Studio） | `local` | `http://127.0.0.1:8000/v1` | `OPENAI_API_KEY` |
| 借用 goose 配置 | `goose` | 从 `~/.config/goose` 解析 | — |

Cursor **没有**公开的 OpenAI 兼容聊天端点（`crsr_…` Key 用于 Cloud Agents API；`api2.cursor.sh` 是 IDE 内部 gRPC），因此 `cursor` preset 指向基于 Cursor 登录的本地 OpenAI 兼容代理——见 **[docs/integrations.md → Cursor](docs/integrations.md)**。

优先级始终为：显式覆盖 → 环境变量 → **端点模型发现**（`GET /v1/models` 的第一个 id）→ preset 默认。本地服务重新部署会自动跟上——若钉死的模型端点已不再提供，会自愈：客户端重新发现一次并重试，而不是整轮失败。自托管推理端点自动带 `enable_thinking: false`；HTTP 400 拒绝该标志会触发自愈重试。

库级使用（无 CLI）：

```python
from open_dream_rsi import (DiscoveryTree, DreamEngine, ReplaySimulator,
                            DreamAgent, LLMConfig, OpenAICompatibleClient)

client = OpenAICompatibleClient(LLMConfig.from_preset("local"))
tree = DiscoveryTree()
dreamer = DreamEngine(simulator=ReplaySimulator(tree))
dreamer.run_offline_optimization(iterations=100)
agent = DreamAgent(dreamer=dreamer, client=client)
```

---

## 跨运行持久化什么（`--memory` 目录）

| 制品 | 文件 | 作用 |
|---|---|---|
| 做梦得到的策略参数 | `policies.json` | 下一轮从上一轮最优起步 |
| 已晋升的策略程序 | `policy_codes.json` | LLM 写的 `choose_action` 指导展开 |
| 配方库 | `recipes.json` | 已验证解法，回放作热启动 |
| 知识库 | `lessons.json` | 策展、经回放门控的教训（声明式文本） |
| 发现树 | `trees/` | 离线做梦始终有历史；节点可选带可安全回放的 Sentinel 事实 |
| 审计日志 | `events.jsonl` | 每次决策的追加记录 |

值得知道的开关：`--no-policy-code`、`--no-knowledge`、`--no-thoughts`、`--no-judge` 可消融四层可选机制；`--budget N` 限制每轮 API 调用（做梦仍免费）。

---

## 自动做梦：循环究竟何时跑？

缝合点是 `dream_once()`（CLI `odr dream`、MCP `odr_dream`，或宿主 hook）。**你不必设调度——模块按次决定档位**，因此每次任务后都触发也安全；没有新东西可学时不会额外花钱：

| 档位 | 触发条件 | 跑什么 |
|---|---|---|
| **maintenance** | 安静调用（无新证据、距上次做梦不久） | 摄入扫描（便宜）、ALM 过期/替代/归档/GC、skills 再发布——**零 LLM** |
| **full** | `min_new_evidence`（默认 3）条新的失败形态会话，或距上次完整做梦超过 `max_age_seconds`（默认 6 小时） | + 在线尝试、做梦、配对回放晋升门（`budget`，默认 20 次 API） |

忙日梦几次，安静日一次，空闲日从不；并发调用在单实例锁上折叠。**探测任务自动入队**：每个失败形态会话把*自己的请求*追加到 `tasks.json` 为 `probe:<session>`（`max_attempts=1`，排在手工任务之后），因此晋升门从不依赖手写任务文件——你已产生的证据就是对照方。

大规模安装只需两样制品，无需宿主胶水：

```bash
python3 -m pip install git+https://github.com/patrykorwat/open-dream-rsi  # 库 + CLI + MCP
cp -r plugins/hermes_odr_trigger $HERMES_HOME/plugins/odr-trigger
hermes plugins enable odr-trigger                # 重启以加载 hook
# 可选覆盖：
hermes config set plugins.entries.odr-trigger.settings.provider local
hermes config set plugins.entries.odr-trigger.settings.base_url http://.../v1
```

---

## 基准：goose × TravelPlanner（公开，外部评分）

这是本项目中**唯一的基准**——本标题以下其余内容都是机器自检。真实 Agent（goose v1.53）在官方 **TravelPlanner** 离线库（`osunlp/TravelPlanner`）上规划真实行程，经标准库 MCP 沙箱，由基准*自有*常识与硬约束评测器打分（评分路径无一属于我们），模型为 vLLM 上的 `Qwen3.8-Flash-Next`。本仓库与论文中所有研究共用同一端点与模型。

冻结协议：仅在 `train.csv`（45 题）上迭代；一次预注册评估跑在官方验证集第 102–181 行（79 题，60 硬约束），配置与开发划分相同；官方测试集从不触碰。各臂按题交错（配对，便于 McNemar）。gate 臂是*移除选择*而非说服：超过 45 次工具调用中的 36 次后沙箱拒绝服务（所有文本通道关闭）。

| split | arm | 交付计划 | 常识通过 | 硬约束通过 | 最终通过 |
|---|---|---|---|---|---|
| train (45) | cold | 15/45 (33%) | 4 | 3 | 6.7% |
| train (45) | gate | 41/45 (91%) | 18 | 9 | 20.0% |
| **eval (79)** | cold | 18/79 (23%) | 12 | 6 | 7.6% |
| **eval (79)** | gate | **74/79 (94%)** | 40 | 17 | **21.5%** |

配对交付的 McNemar：eval 56↔0 不一致，p=1.4e-17（train 28↔2，p=4.3e-7）。

![goose × TravelPlanner: delivery + failure anatomy](docs/figures/fig_tp.png)

为何有效：**每一个未交付的 cold 回合（两划分合计 91/91）都死在 harness 调用上限上，且沙箱数据已到手**——预算压力下的绑定失败是*终止*，不是错误恢复；先前测过的三条「文进上下文」通道（pull 工具、Stop-hook 备注、预算 nudge）转化了零个此类回合。拿掉继续探索的选项，是唯一推动交付的杠杆。诚实 caveat：选择 gate 而非说服，是在已知更早 101 题验证池结果的前提下做出的（selection 时代，已归档——eval 是预注册确认，非首次曝光）；gate 在 eval 上剩 5 次失败是仍穿过拒绝打到上限的回合。这些数字背后的一切都在本目录：`benchmarks/travelplanner/`——沙箱 MCP、回合 runner、官方评测包装、精确划分 CSV 与原始逐题记录。`python3 score.py results/submissions/…` 可从已提交记录复现上表到题；目录 README 写明前置与一键重跑。图由 `fixtures/tp_v3_summary.json` 经 `paper/make_figures.py` 重绘。论文 §Evaluation。

---

## 机器自检（确定性、无需 Key）

不是基准——行为回归契约：脚本化解算器使「模型臂」恒定，因此每个差值都可归因于库的门控、沙箱、晋升与检索逻辑。它们让坏掉的机制在 CI 失败，而不是悄悄改写已发表数字；从全新 clone 可在数秒内重跑。

### `bench` —— 做梦循环对照冷基线

| arm | 解题数 | API 调用 | 调用 / 题·轮 | 做梦迭代 |
|---|---|---|---|---|
| cold_baseline（每轮全新记忆） | 50 | 150 | 3.0 | 0 |
| dream_rsi_loop（策略 + 配方 + 做梦） | 50 | 69 | 1.38 | 3000 |

**同等解题质量下 API 调用减少 54%**（本提交复测：与已发表表一致）。

```bash
python -m open_dream_rsi bench --cycles 10 --markdown
```

### `bench-policy` —— 诱饵陷阱（探索契约）

解题率自检在简单套件上会饱和，分不清好探索与运气，因此 `bench-policy` 把每个修复藏在*低分*枝（过 1/3 测试）之后，前面是*看似合理的诱饵*（永远过 2/3）。分数贪心循环会锁死诱饵并饿死；逃脱需要结构性探索。五臂、同一脚本化解算器、同一预算：

```bash
python -m open_dream_rsi bench-policy --cycles 8 --format md
```

| arm | 解题 | 解题率 | API 调用 | 调用 / 解 | 策略调用 | 策展调用 |
|---|---|---|---|---|---|---|
| greedy | 0/120 | 0% | 480 | ∞ | 0 | 0 |
| epsilon_greedy | 29/120 | 24% | 405 | 13.97 | 0 | 0 |
| **evolved_policy** | **92/120** | **77%** | **252** | **2.74** | 27 | 0 |
| **knowledge_curator** | **88/120** | **73%** | 332 | 3.77 | 0 | 34 |
| **thought_guided** | **120/120** | **100%** | **164** | **1.37** | 0 | 0 |

- **evolved_policy** —— 循环按类别请 LLM 写策略程序，校验并回放打分，仅凭证据晋升。第 5 轮前达 100% 解题率，总调用却少于 ε 浪费在运气上的量。
- **knowledge_curator** —— ε-greedy 且零策略调用：相对 ε 基线的所有逃脱都来自记住的知识（73%，与门控策略大致持平，调用约为 ε 的 80%）。
- **thought_guided**（库默认）—— 每次尝试记录一行 `PLAN:`；想法一重复就离开该枝。强制 ε=0 且无策略/策展调用时，**从第 1 轮起**逃脱所有陷阱；仅账本消融（提示中有同样文本，但关闭选择）塌回基线——赢的是展开规则，不是提示长度。

![Decoy-trap suite: per-cycle solve rate, five arms](docs/screenshots/odr_policy_bench.png)

### `gate-replay` —— 某套教训会否被晋升？

把已上线的晋升规则（`lesson_gate_verdict`）应用到任意已记录回放 harness 的逐题结果对——无模型、无 GPU：

```bash
python -m open_dream_rsi gate-replay \
  --baseline cold=/tmp/arms.json?label=cold \
  --compare lessons=/tmp/arms.json?label=warm \
  --key task_id --format md
```

---

## 循环关于记忆学到了什么（诚实结果）

知识策展器把验证器失败蒸馏成短、已校验、去重的 **lessons**——纯文本，从不执行。然而真模型回放证明：新鲜教训*默认有害*：

- 结构完美的教训（过了所有 schema/上限门）在贪心解码上把解题率从 **18/20 降到 6/20**：「继续检查」类建议把贪心解码变成过探索循环。
- 同样事实改写成**声明式背景**（无祈使动词、显式停止条款）：18/20——与冷启动无区别（McNemar p=1.0）。危害在祈使语气，不在内容。
- 另外七种已发表注入策略（抽象工作流、示例、证据证书、遇错反应备注、提示末尾位置）相对同期冷基线配对增益为**零**；把安全文本移到提示末尾反而有害（11/20，p=0.008——近因效应让模型*按*那些在中段本无害的事实去行动）。

上线设计编码了这一切：教训是 `staging` 候选，须过配对回放门（净收益 ≥ 1、**零**「解→挂」回退、显式停止条款）后任何提示才能看见；提案提示把知识库框为声明式背景；按类别的 **headroom 判定**（近期解题率 ≥ 0.7 *且* 调用/解 ≤ 5 时跳过策展）在冷基线已赢的地方花零策展/门控调用。指导要付扰动税——循环只在有可度量 headroom 可对抗时才付。

---

## 学习 vs 生命周期 vs 历史

仓库严格分开四个平面：

| 平面 | 所有者 | 回答的问题 |
|---|---|---|
| **学习** | Dream-RSI 管道（做梦器、回放门、教训门） | 该制品是否*配得上*处于激活？ |
| **当前制品状态** | `ArtifactLifecycleManager`（`lifecycle.py`） | 关于该制品*现在*什么为真？ |
| **转移历史** | 追加型 `artifacts/events.jsonl` | 它如何到此？ |
| **不可变证据** | 发现树 + 事件日志 | 这些制品为何存在、由何种世界产生？ |

ALM 管理四类制品（`policy_parameters`、`policy_program`、`recipe`、`lesson`），经显式状态机（`CANDIDATE → VALIDATED → ACTIVE → STALE/SUPERSEDED/QUARANTINED → ARCHIVED`，终态 `REJECTED`），硬规则：激活*只能*是携带证据的显式晋升调用；每次状态变更在暴露新状态**之前**追加恰好一条不可变事件；物化视图必须始终等于 `rebuild_artifact_state(events)`（手改视图从日志重建，从不信任）；合并创建带谱系的新制品且从不就地改源；删除从不修剪事件日志。发现树与审计事件是**证据，不是制品**——ALM 拒绝注册它们。查看存储：
`odr artifacts [--type lesson --state ACTIVE --history]`。

---

## 导入真实会话：Hermes 与 Cursor（策展的第二扇门）

循环从自己产生的失败蒸馏教训。`sessions.py` 打开第二扇合法门：**宿主 Agent 的转录是你已经付过钱的证据**。导入走与循环收集失败*同一*管道——结构门、默认 staging、配对回放晋升。导入的任何东西都不被信任；激活仍须挣得。转录按**敌意输入**处理：只读打开、凭证擦除、防御性解析——从未过晋升门的教训**永不进入提示**。

### 来自 Hermes 会话

Hermes 把每场会话持久化到 `<HERMES_HOME>/state.db`（SQLite：`sessions` + `messages`）。先导出，再蒸馏：

```bash
python -m open_dream_rsi --memory .dream_rsi sessions export \
    --source hermes --db ~/.hermes/state.db --limit 50 \
    --out episodes.jsonl

# 查看「将会」蒸馏什么——无 LLM、无写入：
python -m open_dream_rsi --memory .dream_rsi sessions distill \
    episodes.jsonl --dry-run --limit 5

# 真实运行：staging 教训进入知识库（类别 = episode 的 cwd 名）
python -m open_dream_rsi --memory .dream_rsi sessions distill episodes.jsonl
```

下一轮 `odr loop` 对 staging 条目的门控与循环自产条目完全相同；`odr artifacts --type lesson --history` 显示每次激活背后的生命周期事件。

### 来自 Cursor 聊天

Cursor 把对话放在 `globalStorage/state.vscdb`（Linux：`~/.config/Cursor/User`，macOS：`~/Library/Application Support/Cursor/User`，Windows：`%APPDATA%\Cursor\User`），`workspaceStorage` 把聊天映射到项目文件夹作为类别。读取器处理未文档化布局与气泡排序规则；重大 Cursor 升级后请再验证。

```bash
python -m open_dream_rsi --memory .dream_rsi sessions export \
    --source cursor --out episodes.jsonl --limit 100
python -m open_dream_rsi --memory .dream_rsi sessions distill episodes.jsonl
```

注意：在线库使用 WAL——`immutable=1` 可能漏掉最新聊天，因此请退出 Cursor，或把 `state.vscdb`+`-wal`+`-shm` 一并复制后指向副本以得到完整快照。聊天史含粘贴过的一切：导出默认擦除凭证形态子串（仅本地处理可用 `--no-redact`）——分享前请审阅 `episodes.jsonl`。

### 策展教训作为 Hermes skills

```bash
python -m open_dream_rsi --memory .dream_rsi skills export \
    --out ~/.hermes/skills/odr-curated
```

仅渲染 **active**（经晋升门）教训为 Hermes 风格 skills——每类别一目录、声明式条目、从不用祈使命令（已测危害）。导出单向且整批重生：改 `lessons.json`（或再蒸馏），不要改 skills。

### 分享教训：基于 git 的 store

`share.py` 把策展知识变成可移植、可校验的 store，供其他实例（或其他人）消费——一目录 JSON，可选 git checkout，因此 GitHub 只是存储：

```bash
# 一次性：把 store 指到仓库（自动 clone/pull）
python -m open_dream_rsi --memory .dream_rsi lessons export \
    --store git@github.com:you/dream-rsi-lessons.git

# 消费侧——别人的 store 成为你的 staging：
python -m open_dream_rsi --memory .dream_rsi lessons import \
    --store https://github.com/you/dream-rsi-lessons.git
```

信任模型：**导出只运送挣得激活的内容**（staging 与拒绝教训在构造上排除、密钥擦除、sha256 清单）；**导入落地为 staging，从不为 active**（校验和不匹配则中止整次导入——检测到篡改，不信任；每条教训仍需*你的*配对回放门才能进提示）。本地目录同样工作（`--store ./store`）——git 只是传输。

### 完整 Hermes 接线（现状）

| 路径 | 方向 | 作用 |
|---|---|---|
| `odr mcp`（stdio）/ `--http` | Hermes → ODR | Hermes 以工具调用 `odr_run_once` / `odr_dream` / `odr_status` |
| `plugins/hermes_sentinel/` | Hermes → ODR 引擎 | Sentinel 观察工具结果、标注、门控 |
| `plugins/hermes_odr_trigger/` | Hermes → ODR 引擎 | 会话结束 hook 拉起分离的 `odr dream` 周期（无需 shell/cron） |
| `plugins/deepseek_harness/` | DeepSeek Harness（`dsh`）→ ODR | Cordis overlay：`@deepseek-ai/dsh-mcp-client` 背后的 ODR MCP |
| `plugins/cursor/` | Cursor → ODR（无 MCP） | Hooks（sentinel 备注、会话结束做梦）+ `task add` 队列——用于 MCP 被拦的 Cursor 安装 |
| `odr sessions export/distill` | Hermes → 策展 | 会话史变为 staging 教训 |
| `odr skills export` | 策展 → Hermes | active 教训变为可发现的 SKILL.md |
| `odr dream` | 任意触发 | 一轮自动化：证据 → 门 → ALM → skills |

---

## 安全模型

候选代码——解题代码与策略程序——均在硬化子进程中运行（`open_dream_rsi/sandbox.py`）：`python -I`、清洗环境（仅 `PATH`——API Key 永不过墙）、超时杀掉**整个进程组**，以及 POSIX 地址空间 / CPU / 线程数限制。策略代码在任何 spawn **之前**另过 AST 门：必须有入口点、无 import、无 dunder 访问、无 `__builtins__` 引用（名字加上构造键索引本身也是逃逸路径）。

**这是纵深防御，不是容器。** 子进程不限制文件系统或网络，AST 门也无法对未静态门控的代码给出完备证明。敌意任务源请设 `ODR_SANDBOX_CMD` 为 [bubblewrap](https://github.com/containers/bubblewrap)/nsjail 包装（如 `bwrap --unshare-all --die-with-parent --ro-bind / /`），或把整个循环放进容器。

---

## 实时看板

一条命令，无依赖，标准库 http 服务：

```bash
python -m open_dream_rsi dashboard                   # mock LLM，端口 8765
python -m open_dream_rsi dashboard --provider cursor # 真模型
python -m open_dream_rsi dashboard --host 0.0.0.0    # 局域网访问
```

深色单页 UI：KPI（轮次 / 已解 / API 预算 / 做梦迭代）、实时事件流、带得分条与代码展开的分题尝试板、按类别的做梦策略仪表，以及已学配方库。

---

## 定位

- **所实现的论文：** Dream-RSI（[arXiv:2609.14858](https://arxiv.org/abs/2609.14858)）；官方仓库 [zhengkid/Dream-RSI](https://github.com/zhengkid/Dream-RSI)，解说 [dream-rsi.com](https://dream-rsi.com/)。
- **同侪实现**（本仓库是独立开源之一，并非最早）：[TheAstrayDev/dream-rsi-sdk](https://github.com/TheAstrayDev/dream-rsi-sdk)（适配器 SDK，严格回放；路线图含 LLM 写策略），[robinber/dream-rsi-spark](https://github.com/robinber/dream-rsi-spark)（本地 Qwen + CUDA 核探索），以及 skill 变体（[hermes-dream-rsi](https://github.com/lesterppo/hermes-dream-rsi)、[dream-rsi-skill](https://github.com/Harkit2004/dream-rsi-skill)、[Pi-RSI](https://github.com/mailbobg/Pi-RSI)）。
  本仓库差异化：带预算守卫的**常驻自主 supervisor**、经反事实回放门控的 **LLM 写探索策略**（第 3 节闭环）、**公开基准测量**（goose × TravelPlanner）而非自评分主张、确定性诱饵陷阱自检套件、跨运行持久记忆，以及循环自身执行的关于记忆的**负结果**一节。
- **OpenRSI / Frontis-MA1**（[FrontisAI/OpenRSI](https://github.com/FrontisAI/OpenRSI)）是另一层：他们后训练*权重*；本库优化*冻结模型周围的探索策略*——无训练、无 GPU、无权重要求。互补：他们训练出的改进器可作为我们客户端背后的冻结 LLM。

---

## 模块架构

| 模块 | 角色 |
|---|---|
| `core.tree` | DiscoveryTree——假设、结果、动作、想法、Sentinel 事实、终止原因 |
| `core.simulator` | 不碰环境的回放模拟 |
| `core.dreamer` | 在已记录历史上的离线优化 |
| `core.agent` | 由奖励策略驱动的 LLM Agent |
| `core.policygen` | 策略生成、AST 门、rollout 打分、晋升 |
| `core.curator` | 教训蒸馏、headroom 判定、门控判定 |
| `core.judge` | 无测试（`criteria`）任务的完成判定 |
| `loop` | `AutoRSIRuntime`——自主 supervisor（可选 `sentinel_engine`） |
| `dream` | `dream_once`——自动化节奏缝：证据摄入、档位决策、门 + ALM、skills 发布；带锁 |
| `lifecycle` | `ArtifactLifecycleManager`——状态机、事件溯源历史、谱系、GC |
| `sessions` | Hermes `state.db` / Cursor `state.vscdb` 读取、擦除、教训→skills 桥 |
| `memory` | `DreamMemory`——策略、配方、教训、树、事件 |
| `sandbox` | `run_isolated`——唯一不可信代码执行边界 |
| `tools` | `CodeVerifier`——候选解的沙箱验证 |
| `llm` | OpenAI 兼容客户端（标准库 `urllib`；模型发现 + 404 自愈） |
| `cli` | `loop / status / dashboard / bench / bench-policy / gate-replay / mcp / proxy / sentinel` |
| `mcp`, `mcp_server` | 循环工具与 sentinel 检查的 MCP stdio 服务器 |
| `proxy` | 借用 goose 上游的本机环回 OpenAI 兼容代理 |
| `dashboard` | 零依赖实时 Web UI |
| `gate_replay` | 教训晋升规则的离线校验 |
| `sentinel` | 宿主无关错误类引擎 + `SentinelObservation` 世界契约 |
| `utils.goose` | goose 配置解析器（CLI + 桌面方言、钥匙串） |
| `plugins/*` | Hermes / Claude Code / goose / DeepSeek Harness / Cursor 适配器 |
| `benchmarks/travelplanner` | **基准**：沙箱 MCP、回合 runner、官方评测包装、划分 CSV、已发表原始记录 |

## 测试

```bash
python -m unittest discover -s tests          # 或: pytest tests/ -q
```

321 项测试（+10 子测试），仅标准库：基准行为契约（贪心必须解 0；门控臂必须压过 ε；rollout 必须仅前缀）、沙箱逃逸回归、门控语义、MCP 协议、goose 配置方言、sentinel 通道语义（nudge 每预算触发一次、与复发备注合并、干净调用计入预算；permute-gate 服务到 ceil(0.8·budget) 后永久拒绝，按会话；30 天生产回放须精确复现记录备注数）、模型发现与过期钉死自愈契约，以及结构化缝（跨会话计数永不进入 `to_world_dict()`；前沿特征归因模式——有元数据时胜过贪心、无则打平；vendored Hermes 引擎由 `test_vendored_engine_matches_upstream` 与上游逐字节钉死）。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
