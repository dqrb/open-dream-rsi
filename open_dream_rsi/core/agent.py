"""学习链路第 3 步：用做梦学到的策略参数，向 LLM 要下一步动作 / 代码。

注意：这里不改模型权重。``DreamEngine.policy_parameters``（如 temperature）
只影响「怎么问模型」——探索更发散还是更贪心。
看板 / ``loop`` 里真正写解题代码的提示词，多在 ``loop.py`` 的
``CANDIDATE_SYSTEM_PROMPT`` + ``REFINE_USER_TEMPLATE``；本文件是更通用的 Agent 封装。
"""

from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from open_dream_rsi.core.tree import DiscoveryTree
from open_dream_rsi.core.dreamer import DreamEngine


@runtime_checkable
class ChatClient(Protocol):
    """LLM 客户端最小接口（真客户端 OpenAICompatibleClient、测试 StubClient、看板 MockLLM）。"""

    def chat(self, messages: List[Dict[str, str]], model: Optional[str] = None,
             temperature: float = 0.7, max_tokens: int = 1024) -> str: ...


class DreamAgent:
    """由离线做梦得到的策略驱动的 LLM Agent。

    Args:
        dreamer:       持有 ``policy_parameters`` 的做梦引擎。
        client:        OpenAI 兼容客户端，或测试用 StubClient。
        system_prompt: 系统提示（任务域说明）。
    """

    def __init__(
        self,
        dreamer: Optional[DreamEngine] = None,
        client: Optional[ChatClient] = None,
        system_prompt: str = (
            "You are an agent optimizing a programming-task solution. "
            "Reply with a single action from the list: explore | refine | verify | commit."
        ),
    ):
        self.dreamer = dreamer
        if client is None:
            from open_dream_rsi.llm import StubClient  # lazy import — no import cycle

            client = StubClient()
        self.client: ChatClient = client  # type: ignore[assignment]
        self.system_prompt = system_prompt

    @property
    def policy(self) -> Dict[str, float]:
        """当前做梦策略参数（没有 dreamer 时为空字典）。"""
        return self.dreamer.policy_parameters if self.dreamer else {}

    def propose_action(self, context: str) -> str:
        """按做梦得到的 temperature 问 LLM，返回下一步动作名。"""
        temperature = float(self.policy.get("temperature", 0.7))
        reply = self.client.chat(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": context},
            ],
            temperature=temperature,
        )
        return reply.strip().lower().splitlines()[0] if reply else "explore"

    def execute_step(
        self,
        tree: DiscoveryTree,
        state_id: str,
        scorer: Callable[[str], Tuple[object, float]],
    ) -> str:
        """在线一步：LLM 提议动作 → scorer 打分 → 新节点挂到发现树上。

        ``scorer(action) -> (result, score)`` 会真正调工具（测代码等）。
        返回新节点的 ``node_id``。
        """
        node = tree.nodes[state_id]
        context = f"State: {node.result!r}\nCurrent score: {node.score}\nChoose an action."
        action = self.propose_action(context)
        result, score = scorer(action)
        new_id = f"{state_id}/{action}-{len(tree.nodes)}"
        tree.add_node(new_id, action=action, result=result, score=score, parent_id=state_id)
        return new_id
