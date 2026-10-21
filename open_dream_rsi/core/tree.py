"""学习链路第 4 步：发现树 —— 把每一次「尝试」记成树节点，供离线做梦回放。

树 = 世界历史。节点上有代码/动作、测试得分、可选 PLAN 想法、Sentinel 事实。
做梦（``dreamer`` / ``simulator``）只能在这棵已记录的树上做反事实回放，
不能偷偷调用外部工具——所以「免费」。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TerminationReason(str, Enum):
    """这一集探索为什么停在边界节点上。

    回放必须分清「决策者自己停」和「运行时把选项拿走了」——对反事实打分含义相反。
    封闭词表；字符串值会写入磁盘上的树，属于持久化约定。
    """

    COMPLETED = "completed"          # 本题在此节点已解出
    POLICY_STOP = "policy_stop"      # 策略/Agent 选择停止探索
    TOOL_FAILURE = "tool_failure"    # 工具错误导致无法继续
    BUDGET_GATE = "budget_gate"      # API/尝试次数上限拿掉了继续的选项
    SENTINEL_BLOCK = "sentinel_block"  # 强制门（如 PermuteGate）拒绝继续服务
    UNKNOWN = "unknown"              # 未记录（旧树）或语义不清


@dataclass
class TreeNode:
    """树上的一个节点 = 一次尝试的快照。"""
    node_id: str
    action: str
    result: Any
    score: float
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)
    #: 模型写下本尝试时的一句计划（「为什么试这个」）。纯文本、从不执行；
    #: 但让分枝选择从「只看分数」变成「还能看是哪种想法」。
    thought: str = ""
    #: Sentinel 对该节点工具结果的结构化事实（回放安全形态）。
    sentinel: Optional[Dict[str, Any]] = None
    #: 本集停止时写在边界节点上；中间节点为 None。
    termination_reason: Optional[str] = None


class DiscoveryTree:
    """代理的发现历史，存成状态树。"""

    def __init__(self):
        self.nodes: Dict[str, TreeNode] = {}
        self.root_id: Optional[str] = None

    def add_node(self, node_id: str, action: str, result: Any, score: float,
                 parent_id: Optional[str] = None, thought: str = "",
                 sentinel: Optional[Dict[str, Any]] = None,
                 termination_reason: Optional[str] = None) -> TreeNode:
        """追加一次尝试：有父节点则挂到 children，否则成为根。"""
        node = TreeNode(node_id=node_id, action=action, result=result,
                        score=score, parent_id=parent_id,
                        thought=thought or "", sentinel=sentinel,
                        termination_reason=termination_reason)
        self.nodes[node_id] = node

        if parent_id and parent_id in self.nodes:
            self.nodes[parent_id].children.append(node_id)
        elif self.root_id is None:
            self.root_id = node_id

        return node

    def get_best_trajectory(self) -> List[TreeNode]:
        """按 score 最高的叶子回溯到根，得到当前最佳路径。"""
        if not self.nodes:
            return []
        best_node = max(self.nodes.values(), key=lambda n: n.score)

        trajectory = []
        curr: Optional[TreeNode] = best_node
        while curr:
            trajectory.append(curr)
            curr = self.nodes.get(curr.parent_id) if curr.parent_id else None

        return list(reversed(trajectory))
