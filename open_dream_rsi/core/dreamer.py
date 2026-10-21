"""学习链路第 5 步（上）：离线做梦 —— 在已记录的发现树上爬山调探索参数。

不花 API：只在历史树上「假如用这组参数选分枝，会不会更好」。

两个参数组成参数化探索策略：

* ``temperature``       — 对当前可见前沿的 softmax 锐度
  （``T→0`` 贪心；``T`` 大则近乎均匀乱探）。
* ``exploration_depth`` — 同一节点最多再打磨几次；超过就被迫开新分枝。

目标函数与门控 LLM 写的策略代码相同（反事实 rollout）；参数只有在「历史里
会打开更好分枝」时才得分。看板里的 DREAM ITS 大体对应这里的迭代次数。
"""

import math
import random
from typing import Any, Dict, List

from open_dream_rsi.core.policygen import _rollout_objective, _simulate_step_rewards, rollout_world_payload
from open_dream_rsi.core.simulator import ReplaySimulator

#: Hard bounds for policy parameters — dreaming explores inside a safe region.
TEMP_MIN, TEMP_MAX = 0.1, 1.5
DEPTH_MIN, DEPTH_MAX = 1.0, 6.0
#: Seeded episodes per candidate evaluation (deterministic: hill climbing
#: must compare candidates on identical trajectories, issue #1 review).
EPISODES = 6
#: Base seed for the episode RNGs.
DREAM_SEED = 1234


class DreamEngine:
    """离线探索策略优化引擎（Offline Dreaming）。"""

    def __init__(self, simulator: ReplaySimulator):
        self.simulator = simulator
        # 在线下一轮会读这两个数来「怎么探」
        self.policy_parameters: Dict[str, float] = {"temperature": 0.7, "exploration_depth": 3.0}
        #: 专用 RNG：变异与回合抽样都有种子，同树做梦可复现（基准/测试用）
        self._rng = random.Random(DREAM_SEED)

    def run_offline_optimization(self, iterations: int = 50) -> Dict[str, float]:
        """在模拟历史上爬山，优化 ``policy_parameters``。"""
        # 现任种子爬山：只有模拟分严格更高才替换（与第 3 节晋升规则一致）
        best_policy = self.policy_parameters.copy()
        best_score = self._evaluate_policy_in_dream(best_policy)

        for _ in range(iterations):
            # 1. 微扰参数（钳在安全区间）
            candidate_policy = {
                "temperature": min(TEMP_MAX, max(TEMP_MIN, self.policy_parameters["temperature"] + self._rng.uniform(-0.1, 0.1))),
                "exploration_depth": min(DEPTH_MAX, max(DEPTH_MIN, self.policy_parameters["exploration_depth"] + self._rng.uniform(-0.5, 0.5))),
            }

            # 2. 在回放模拟器里打分（不调真工具）
            simulated_score = self._evaluate_policy_in_dream(candidate_policy)

            # 3. 更好就留下
            if simulated_score > best_score:
                best_score = simulated_score
                best_policy = candidate_policy
                self.policy_parameters = candidate_policy

        self.policy_parameters = best_policy
        return best_policy

    # -- the simulated policy family ------------------------------------------------

    @staticmethod
    def _pick(visible: List[Dict[str, Any]], rng: random.Random,
              temperature: float, depth: float) -> str:
        """One choice of the parameterised policy on a visible frontier.

        Softmax over as-of-now outcomes with ``temperature`` sharpness, over
        the pool of nodes whose recorded re-expansion count is below
        ``exploration_depth`` (falls back to the full frontier when the cap
        would leave nothing to pick).
        """
        pool = [n for n in visible if n["children"] < depth] or visible
        vals = [n["outcome"] for n in pool]
        spread = max(vals) - min(vals)
        if spread <= 1e-9:
            return rng.choice(pool)["node_id"]
        top = max(vals)
        weights = [math.exp((v - top) / max(temperature, 1e-6)) for v in vals]
        total = sum(weights)
        draw = rng.random() * total
        acc = 0.0
        for node, w in zip(pool, weights):
            acc += w
            if draw <= acc:
                return node["node_id"]
        return pool[-1]["node_id"]

    def _evaluate_policy_in_dream(self, policy: Dict[str, float]) -> float:
        """做梦打分：用候选参数在记录历史上 rollout，看「选分枝」好不好。

        分数反映参数导致的*选择*，不是参数本身的固定函数。
        """
        if not self.simulator.tree.nodes:
            return float("-inf")
        t = min(TEMP_MAX, max(TEMP_MIN, float(policy["temperature"])))
        depth = min(DEPTH_MAX, max(DEPTH_MIN, float(policy["exploration_depth"])))
        world = rollout_world_payload(self.simulator.tree)
        total = 0.0
        for ep in range(EPISODES):
            rng = random.Random(f"{DREAM_SEED}:{ep}")
            rewards, picks, _invalid, err = _simulate_step_rewards(
                world, lambda visible, step: self._pick(visible, rng, t, depth))
            if err or not rewards:
                return float("-inf")
            total += _rollout_objective(rewards, picks)
        return total / EPISODES
