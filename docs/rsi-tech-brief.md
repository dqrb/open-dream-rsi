# RSI 技术调研简报：Open Dream-RSI

> 对象：开源库 [open-dream-rsi](https://github.com/patrykorwat/open-dream-rsi)（实现 Zheng 等 *Dream-RSI / Evolving Worlds* 思路的独立开源实现）  
> 范围：机制、流程、信任边界与工程取舍；不含部署运维手册  
> 结论先行：**不训练、不改权重**；改进发生在冻结 LLM **周围**的探索策略、验证配方与（经门控的）教训记忆。

---

## 1. 问题背景：为何需要「模型周围」的 RSI

典型 Agent 会话的税：失败 → 归因 → 会话结束 → **经验清零**。下一轮仍从冷启动付 API 成本。

Recursive Self-Improvement（RSI）常见两条路：

| 路线 | 改什么 | 成本与风险 |
|------|--------|------------|
| 权重向 RSI（如部分 OpenRSI / 后训练） | 模型参数 | GPU、数据、对齐与灾难性遗忘风险 |
| **Evolving Worlds / Dream-RSI** | 探索策略、世界模型（历史树）、可复用解与教训 | 可在冻结 API/本地模型上跑；关键是「什么可晋升」 |

本项目属第二条：**LLM 冻结**；循环改进的是 *process around the model*。

论文锚点：Dream-RSI（arXiv:2609.14858）—*Recursive Self-Improvement through Evolving Worlds*。本库为独立开源实现，并补上论文未完全落地的工程闭环（LLM 写策略代码 + 回放门控、教训策展 + 配对回放门控、常驻 supervisor、公开基准 goose×TravelPlanner 等）。

---

## 2. 一句话架构

```
在线：冻结 LLM 提议 → 沙箱验证 → 写入 Discovery Tree
离线：在树上反事实回放（做梦）→ 仅当证据更好时晋升参数/策略程序/教训
持久：policies / recipes / lessons / trees → 下一轮热启动
```

核心不变量：

1. **模型权重只读。**  
2. **晋升靠回放证据，不靠模型自称。**  
3. **不可信代码（解题代码、策略程序）经沙箱；策略程序另有 AST 门。**  
4. **教训默认危险**：须过结构化校验与配对回放门，才可进入提示。

---

## 3. 端到端流程（一轮 = `run_once`）

对任务队列中**每一题**串行执行；队列跑完即一轮结束。看板「Cycles」即自主醒来次数。

### 3.1 在线阶段（花钱 / 可 mock）

| 步骤 | 含义 | 产物 |
|------|------|------|
| 组装提示 | 题目、测试、已有配方、分枝上下文、策略 hint、（已激活的）教训、失败反馈 | Prompt |
| LLM 提议 | 返回完整 Python（可选 `PLAN:` 一行想法） | 候选代码 |
| 沙箱验码 | 隔离子进程跑 `tests`，得分 = 通过比例 | score / errors |
| 记树 | 挂到 Discovery Tree 节点（代码摘要、得分、thought、可选 Sentinel 事实） | `trees/<task>.json` |
| 未解则重试 | 失败详情写入 feedback，受 `max_attempts` / API budget 约束 | 下一节点 |

热启动：同类 `recipes.json` 中的已验证解、做梦得到的 `policies.json` 参数，减少从零探索。

### 3.2 离线做梦（零外部 API）

在**已记录**的 Discovery Tree 上，对参数化探索策略做爬山：

- `temperature`：前沿选择的 softmax 锐度（低→贪心，高→更随机）  
- `exploration_depth`：同一节点可再打磨次数上限  

目标与策略代码门控一致：反事实 rollout（前缀世界 + 记录子奖励等），**仅当模拟分严格优于现任**才替换参数。迭代次数体现在看板「Dream its」。

另可选「用代码做梦」（论文 §3）：LLM 生成 `choose_action(frontier, step)` → AST 静态门 → 硬化子进程执行 → **回放打分** → 打赢现任才晋升到 `policy_codes.json`；崩溃则回退贪心基线，不断环。

### 3.3 知识策展（可选，默认开启）

失败被蒸馏为短文本 **lesson**（声明式背景，忌祈使句——实测祈使式教训会把贪心解码拖进过探索）。写入顺序：

1. 结构化/去重 → **staging**  
2. `lesson_gate`：配对回放（净收益、零「解→挂」回退、显式停止条款等）  
3. 仅 **active** 进入后续提案提示；可导出为 Hermes skills 等  

Headroom 判定：冷基线已够好的类别可跳过策展，避免浪费 API。

### 3.4 一轮结束

写事件日志、ALM 维护（制品状态机）、按 interval 休眠后再醒。记忆目录跨重启保留，故 daemon 与 `--once` 等价于「同一实例的不同唤醒方式」。

---

## 4. 模块地图（学习阅读顺序）

| 模块 | 职责 |
|------|------|
| `cli` / `__main__` | 入口：`loop` / `dashboard` / `bench` / `mcp` … |
| `loop.AutoRSIRuntime` | 主调度：`run_once` / `_work_on_task` |
| `core.agent` | 策略温度驱动的 LLM 调用封装 |
| `core.tree` | Discovery Tree（世界历史） |
| `core.dreamer` + `simulator` | 离线参数做梦与回放步进 |
| `core.policygen` | LLM 策略程序：生成、AST、rollout、晋升 |
| `core.curator` | 教训蒸馏与门控语义 |
| `memory.DreamMemory` | `policies` / `recipes` / `lessons` / `trees` / `events` |
| `sandbox` + `tools.CodeVerifier` | 不可信代码执行边界 |
| `lifecycle` | 制品状态机（CANDIDATE→…→ACTIVE/ARCHIVED），事件溯源 |
| `dashboard` | 零依赖实时看板（默认可 mock LLM） |

集成面：MCP（`odr_*` 工具）、Hermes/Cursor/Goose 等插件；会话导入仅作**敌意输入证据**，仍须同一晋升管道。

---

## 5. 信任与安全模型（调研要点）

- **解题代码**：非 AST 全门控；依赖 `python -I`、擦除环境（无 API Key）、超时杀进程组、POSIX 资源限制；Windows 保留最小 OS 环境变量。敌意任务源应叠加 `ODR_SANDBOX_CMD`（bwrap/nsjail）或容器。  
- **策略代码**：AST 门（无 import、无 dunder 逃逸等）+ 同一沙箱 + 回放晋升。  
- **教训**：默认 staging；导入外来 store 亦落地为 staging；checksum 防篡改。  
- **ALM**：学习管道决定「是否值得」；ALM 只做生命周期记账，不当第二套晋升引擎。

---

## 6. 评估口径（避免误读）

| 类型 | 含义 | 能否当作「模型变强」 |
|------|------|----------------------|
| `bench` / `bench-policy` | 脚本化解算器，测机器（门控、沙箱、做梦） | **否**；行为回归契约 |
| goose × TravelPlanner | 唯一公开、外部评分的正式基准 | 可引用交付率等，须带协议与 caveat |
| Dashboard mock | 脚本化先错后对，演示闭环 | 仅教学 |

项目自述：性能主张锚定在 TravelPlanner 一表；脚本套件不承载模型质量声明。

---

## 7. 与「表层理解」的对齐与修正

表层理解通常是：

> 问一次模型 → 拿真实结果 → 离线改参数在树上多跑 → 更好参数替换旧的 → 教训逐渐写入提示词。

**正确内核：** 在线用真实验证积累世界；离线在树上廉价优化探索；只晋升证据更好者。

**需补齐的细节：**

1. 在线常为**多轮**试错，非单次问答。  
2. 离线还可晋升 **LLM 写的策略程序**，规则同为回放门控。  
3. 教训**不自动**进提示，须过 gate；且设计上偏向声明式背景。  
4. 另持久化 **recipes**（验证通过的解），对同类任务热启动，这是 API 变少的重要来源之一。

---

## 8. 工程画像

- 语言：Python 3.10+，**运行时零第三方依赖**（标准库）。  
- 接口：任意 OpenAI 兼容 `chat/completions`；可 mock / 本地 vLLM·Ollama。  
- 典型命令：`pip install -e .` → `python -m open_dream_rsi dashboard` 或 `bench` / `loop --once`。  
- License：MIT。

---

## 9. 调研结论与适用边界

**适合：** 希望在**不碰权重**前提下，让可测试任务（尤其带单测的代码类）跨会话累积「怎么探、什么解有效、什么教训可信」的团队；以及把 Dream-RSI 类论文落到可审计、可回归的工程库。

**不适合当作：** 通用聊天记忆插件；或「装上就会让任意模型自动变强」的银弹。无测试、难验证的开放域任务依赖 completion judge，证据链弱于测例驱动路径。

**差异化摘要：** 冻结模型 + 发现树世界模型 + 零成本做梦 + **回放门控的策略代码与教训** + 常驻预算守卫 supervisor + 诚实的负结果（教训注入危害）写进产品行为。

---

## 10. 建议后续深读

1. 仓库 `README.md`（流程总图、基准表、负结果节）  
2. `paper/main.pdf`（问题形式化与评估协议）  
3. 代码主链：`loop.py` → `core/tree.py` → `core/dreamer.py` → `memory.py` → `core/policygen.py` / `core/curator.py`  
4. 自检：`python -m open_dream_rsi bench --cycles 5`（无 Key）

---

*简报版本：基于本仓库 Open Dream-RSI 0.2.x 学习与源码阅读整理，供内部 RSI 技术调研使用。*
