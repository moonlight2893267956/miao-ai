"""
所有运行中 agent 的注册中心（单例）。

线程安全：mutation 操作受 asyncio.Lock 保护。
"""
import asyncio

from .manager import ManagedAgent


class AgentRegistry:
    _instance: "AgentRegistry | None" = None

    def __init__(self) -> None:
        self._agents: dict[str, ManagedAgent] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def instance(cls) -> "AgentRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, name: str) -> ManagedAgent | None:
        return self._agents.get(name)

    async def set(self, agent: ManagedAgent) -> None:
        async with self._lock:
            self._agents[agent.name] = agent

    async def remove(self, name: str) -> None:
        async with self._lock:
            if name in self._agents:
                del self._agents[name]

    def all(self) -> list[ManagedAgent]:
        return list(self._agents.values())

    def running_count(self) -> int:
        """当前 running 状态的 agent 数量。"""
        return sum(1 for a in self._agents.values() if a.status == "running")
