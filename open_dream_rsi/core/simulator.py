"""学习链路第 5 步（下）：回放模拟器 —— 离线「走一步」不碰真环境。

做梦引擎问：若在某状态选某动作，历史树里有没有现成结果？
有则原样返回；没有则用简单启发式。整段过程零 API、零沙箱编译。
"""

from typing import Any, Tuple
from open_dream_rsi.core.tree import DiscoveryTree


class ReplaySimulator:
    """回放模拟器：在已记录的发现树上做离线步进。"""

    def __init__(self, tree: DiscoveryTree):
        self.tree = tree

    def step_offline(self, state_id: str, simulated_action: str) -> Tuple[Any, float]:
        """返回近似结果与做梦奖励（Offline Dream Step）。"""
        node = self.tree.nodes.get(state_id)
        if not node:
            return None, 0.0

        # 动作与树上记录一致 → 直接用历史真值
        if node.action == simulated_action:
            return node.result, node.score

        # 未见过的动作：用父子状态上的启发式分
        heuristic_score = node.score * 0.9
        return "simulated_trajectory_result", heuristic_score
