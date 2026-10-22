# Open Dream-RSI 学习走查：对话链路 × 代码主链

> 本文是一次完整源码走读后的**系统整理**：把「从入口到产物」的问答链路压成可复查的工程笔记。  
> 对照材料：仓库 `README.md`、`docs/rsi-tech-brief.md`，主链代码 `open_dream_rsi/loop.py` 及 `core/`、`memory.py`、`sandbox.py`。  
> 阅读前提：已知「冻结 LLM、周围 RSI」的一句话定位；本文不再复述入门概念，只固化**因果与边界**。

---

## 0. 一句话结论（学完应能复述）

Open Dream-RSI **不训练权重**。一轮 `run_once` 在真实（或 mock）LLM + 沙箱上长出 Discovery Tree，再在树上**零 API**梦温度/深度，可选地让 LLM 改写 `choose_action`（须回放打赢才晋升），可选地策展教训（须配对门控才进提示）。跨轮留下的可复用价值主要是：

| 产物 | 文件 | 优化对象 |
|------|------|----------|
| 策略参数 | `policies.json` | 探索锐度（温度）与打磨深度 |
| 策略程序 | `policy_codes.json` | 「选下一个节点」的 Python 逻辑 |
| 配方 | `recipes.json` | 同类任务已验证解法代码 |
| 教训 | `lessons.json` | 经门控的声明式失败背景 |
| 世界历史 | `trees/` | 做梦与回放的原料 |

常见误解纠正：**主循环结束后不是「只调了温度深度」**——那只是离线做梦最显眼的一块；配方与（门控后的）策略程序/教训才是跨任务省 API 的主体。

---

## 1. 学习链路（对话推进顺序 → 对应心智模型）

下列顺序复现了本次走读的认知台阶；每阶对应代码落点，便于回看。

```
启动/配置 → 任务形态(user/system/tests)
         → 提示组装与模型回复
         → 沙箱验证与记树
         → 父节点选择 / 前沿扩展
         → 节点字段与「世界」含义
         → 解题 vs 做梦 vs 策略程序 vs 教训（四条晋升路径）
         → 停止条件与跨轮持久化
         → 产物清单：到底优化了什么
```

| 阶 | 当时的问题内核 | 正确模型 |
|----|----------------|----------|
| A | 这是训练吗？提示在哪？ | 冻结 LLM；提示模板在 `loop.py`（`CANDIDATE_SYSTEM_PROMPT` / `REFINE_USER_TEMPLATE`） |
| B | 回复之后干什么？ | `CodeVerifier` → 沙箱跑测 → 挂 Discovery Tree |
| C | 树怎么长？父节点规则？ | `_next_expansion`：策略程序 →（可选）thought/lesson 转向 → ε-贪心 → 分最高前沿 |
| D | 做梦会不会再测？ | **不会**。`DreamEngine` + `ReplaySimulator` 只在已记录历史上反事实打分 |
| E | reward / 目标是什么？ | 在线：测试通过比例；离线：回放 objective（与策略门控同源思路） |
| F | 策略程序何时优化？ | **做梦之后**的 `_maybe_evolve_policy_code`（花钱 + 回放门） |
| G | 沙箱是否只用于测解？ | **否**：测候选码 + 跑 `choose_action` / 策略回放 |
| H | 跑完得到什么？ | 参数 + 配方 +（可选）策略程序/教训 + 树；**不是更强权重** |

---

## 2. 架构对齐：README 总图 × 代码调度

README 的双环：

```
在线：Frozen LLM 提议 → 沙箱验证 → DiscoveryTree
离线：ReplaySimulator 反事实打分 → 仅证据更好时晋升 → 下一轮热启动
```

代码调度入口是 `AutoRSIRuntime`（`loop.py`）：

```
run_once / run_forever
  └─ 对队列中每题 _work_on_task
        ├─ 在线：propose → verify → append tree（可多 attempt）
        ├─ 可选：save_recipe（更高分才替换）
        ├─ 离线：DreamEngine.run_offline_optimization → save_policy
        ├─ section 3：_maybe_evolve_policy_code → policy_codes.json
        └─ section 4：_maybe_curate_knowledge → lessons（staging→gate→active）
```

与 `docs/rsi-tech-brief.md` 的不变量一致：

1. 模型权重只读  
2. 晋升靠回放/验证证据，不靠模型自称  
3. 不可信代码进沙箱；策略程序另有 AST 门  
4. 教训默认危险，须过门才进提示  

---

## 3. 在线主链（花钱路径）

### 3.1 任务契约

`tasks.json` 一项至少含：`task_id`、`category`、`prompt`、`tests`（`call`/`expected`）、`max_attempts`。  
`category` 是记忆分区键：策略、配方、策略程序、教训都按类持久化。

### 3.2 提示里真正注入的「学习物」

用户模板（`REFINE_USER_TEMPLATE`）把上一轮世界折叠进单次补全：

| 槽位 | 来源 | 作用 |
|------|------|------|
| `recipe` | `memory.get_recipe(category)` | 同类最佳已验证码，热启动 |
| `branch` | 当前扩展父节点上的代码 | 局部分枝上下文 |
| `ideas` | 同枝已试 PLAN/结果 | 避免死主意原样复读 |
| `policy` | `policies.json` 参数 hint | 温度语义：「该换思路还是打磨」 |
| `lessons` | **仅 active** 教训 | 声明式背景，非祈使指令 |
| `feedback` | 上次失败详情 | 直接纠错信号 |

系统侧固定为「只回一个完整 Python fenced block」；开启 thoughts 时追加 `PLAN:` 一行，写入树节点语义标签。

### 3.3 验证与记树

- 模型出码 → `verifier.run(code, task.tests)` → 子进程沙箱，得分多为通过比例。  
- 每次尝试追加树节点（代码、score、thought、feedback 等）。  
- 未解则带 feedback 再提议，受 `max_attempts` 与 API budget 约束。  
- 更高分解可 `save_recipe`：同类只留冠军码 + score。

**父节点何时定？** 在「再问模型」之前由 `_next_expansion` 选定；先选枝、再在该枝上 propose，不是测完才回溯选父。

### 3.4 停止与「解题」

- 解题：验证器判定 solved（通常满分阈值）。  
- 否则：attempt 用尽或预算门。  
- 一轮队列跑完 = 看板一次 Cycle；daemon 的 interval 只是再醒，记忆跨醒保留。

---

## 4. 离线做梦（零 API）：只动参数，不重测

落点：`DreamEngine` + `ReplaySimulator(tree)`，在 `_work_on_task` 在线段之后调用。

| 参数 | 语义 |
|------|------|
| `temperature` | 前沿 softmax 锐度：低≈贪心，高≈更随机换枝 |
| `exploration_depth` | 同一节点允许再打磨的次数上限 |

机制要点：

- 在**已记录**节点序列上爬山 / 反事实打分，**不**再调 LLM、**不**再跑用户测试。  
- 仅当模拟分严格优于现任才 `save_policy`。  
- 历史分数有用：树是可回放世界模型；策略好坏 =「若当初用这套选枝规则，累计回报如何」。

这解释了「为什么感觉只优化了温度和深度」——**这一段确实只优化这两个标量**；其它产物在别的 section。

---

## 5. 策略程序（section 3）：做梦之后的第二次「改进」

落点：`_maybe_evolve_policy_code`（紧接 `dream_done`）。

```
LLM 生成/改写 choose_action(frontier, step)
  → AST 静态门（core/policygen）
  → PolicySandbox 子进程执行
  → 在当前树上 rollout_score，现任按「当前树」重打分
  → 打赢才 save_policy_code → policy_codes.json
```

使用时机：在线选枝 `_next_expansion` 优先跑已晋升程序；崩溃/非法选择 → 退回贪心（及可选 ε / thought / lesson 路径）。

跳过条件（工程上很重要）：

- `--no-policy-code` 或预算耗尽  
- 题已解且该类已有策略（冷启动仍会争取首份）  
- 树过浅（`<2` 扩展步）或相对上次晋升增长不足（`POLICY_REGROW_STEPS`）

与做梦的对比：做梦免费改数字；策略程序**再花 API**，改的是选枝代码，且必须过回放门。

---

## 6. 教训策展（section 4）：默认可关，且默认不进提示

落点：`_maybe_curate_knowledge` / `KnowledgeCurator`。

路径：失败蒸馏 → staging → `lesson_gate` 配对回放 → 仅 **active** 进入 `format_lessons` 槽。  
Headroom：冷基线已够便宜的类别可跳过，避免浪费调用。  
设计取向：声明式背景；祈使式教训在实证上易把解码拖进过探索（README/简报中的负结果）。

---

## 7. 沙箱：两条执行边界（对话末段澄清）

| 用途 | 组件 | 何时 |
|------|------|------|
| 测候选解法 | `CodeVerifier` + 代码沙箱 | 每次 LLM 出解题码之后 |
| 跑策略程序 | `PolicySandbox` | 选下一节点；策略生成后的回放打分 |

因此：**「问完模型测码」一定会用沙箱，但沙箱不只干这一件事。**  
硬化共性：隔离子进程、擦除密钥环境、超时；策略侧另加 AST。敌意任务源应再叠 `ODR_SANDBOX_CMD` / 容器（见简报 §5）。

---

## 8. 发现树：世界模型，不是聊天日志

- 按任务持久化在 `trees/`；节点含父子、代码摘要、score、thought、失败反馈等。  
- 在线：搜索轨迹；离线：做梦与策略/教训门控的共享「世界」。  
- Sentinel（若接入）：可回放的错误类事实，加强终止/归因，不改变「冻结模型」前提。

父节点选择不是「永远最大分」：默认贪心前沿，但可被晋升策略程序、ε 探索、thought 族逃离、lesson 授权逃离改写——消融开关见 README（`--no-policy-code` 等）。

---

## 9. 产物与「优化了什么」——对照 README 持久化表

跨 `--memory` 目录（与 README「跨运行持久化什么」一致）：

| 制品 | 是否本轮必变 | 优化的是 |
|------|--------------|----------|
| `policies.json` | 梦到更好才写 | 探索参数（T / depth） |
| `policy_codes.json` | 回放晋升才写 | 选枝程序 |
| `recipes.json` | 更高分解才写 | **用户问题的已验证答案码**（配方 = 结果本体，不是题面） |
| `lessons.json` | 过门才 active | 可注入提示的失败知识 |
| `trees/` | 几乎每尝试都长 | 世界历史 |
| `events.jsonl` | 追加 | 审计 |

**配方澄清（对话原话对齐）：**  
配方就是某 `category` 下沙箱验证通过且分数最高的那段解法代码（`code` + `score`）。下次同类题塞进 `recipe` 槽热启动——这是「少交 API 税」的主要来源之一，常比温度微调更直观。

**明确没有发生的事：** 权重更新、通用「模型变聪明」、把任意开放域聊天自动变成可证明更强。

---

## 10. 评估口径（避免把自检当成模型涨点）

| 类型 | 含义 |
|------|------|
| `bench` / `bench-policy` | 脚本化契约：门控、沙箱、做梦行为回归 |
| Dashboard + mock | 教学闭环演示 |
| goose × TravelPlanner | README 声明的**唯一**公开外部评分基准 |

脚本套件**不承载**模型质量声明（README 评估政策）。

---

## 11. 模块阅读地图（深读顺序）

建议按因果，而非按目录字母：

1. `loop.py` — `run_once` → `_work_on_task` → `_next_expansion` / `_maybe_evolve_policy_code` / `_maybe_curate_knowledge`  
2. `memory.py` — 四类制品的读写契约  
3. `core/tree.py` — 世界结构  
4. `core/dreamer.py` + `core/simulator.py` — 参数做梦  
5. `core/policygen.py` — 策略程序生成、AST、rollout、沙箱 choose  
6. `core/curator.py` — 教训与门控语义  
7. `sandbox.py` + `tools.py` — 解题码执行边界  
8. `lifecycle.py` — ALM 记账（不替代晋升证据）  
9. `cli.py` / `dashboard.py` / `mcp.py` — 宿主面  

消融与预算：`--no-policy-code`、`--no-knowledge`、`--no-thoughts`、`--no-judge`、`--budget`；做梦始终不计入 API 预算。

---

## 12. 学习成果清单（可自测）

学完应能不看笔记回答：

1. RSI 改的是过程还是权重？四类持久化制品各解决什么问题？  
2. 在线一步的数据流：提示槽 → 模型 → 哪类沙箱 → 树节点字段？  
3. 做梦优化哪些变量？为何历史分可作目标且不再测？  
4. 策略程序在流水线哪一节触发？晋升条件与运行回退是什么？  
5. 教训为何不能「写进文件就算学会」？  
6. 配方与「用户问题结果」的关系？  
7. 沙箱的两条用途？  
8. 为何不能说「跑完主循环模型变强了」？

若以上均可对照文件路径答出，则本次对话链路的认知目标已闭环。

---

## 13. 与仓库内其它文档的分工

| 文档 | 角色 |
|------|------|
| `README.md` | 产品说明、接入、基准与开关 |
| `docs/rsi-tech-brief.md` | 调研简报：问题背景、信任边界、适用边界 |
| **本文** | **走读笔记**：对话推进的因果链 + 代码落点 + 误解纠正 |

后续若改主链语义（晋升规则、做梦目标、门控），应先改代码与测试，再同步 README/简报/本文中对应小节，避免三套叙述漂移。

---

*整理自对本仓库 Open Dream-RSI 的源码走读与问答闭环；命令与标识符保持与上游英文 API 一致，叙述使用中文以便复查。*
