"""执行事件上报 —— 本 agent 对**平台 SDK**（``miao_sdk``）的绑定层。

分工与本 agent 的取舍：

* **事件契约、sink 线程模型、``stream()`` 线程化桥接**都在平台 SDK 里
  （``backend/agent_templates/miao_sdk.py``，与 runner 同目录）。脚本模式下
  ``sys.path[0]`` 正是 runner 所在目录，所以运行时能直接 ``import miao_sdk``，
  本 agent **不需要**把 SDK 打进自己的 zip。
* 本层只做一件事：把 SDK 包装成包内 API，并在 **SDK 不可用时降级为空操作**。
  离线自检与老运行时都可能没有这个文件，缺了它绝不能让 agent 起不来。

本 agent 上报什么（与 SDK 的通用能力相比是**子集**，这是我们自己的取舍）：

* **只发工具调用** —— 三个真实的外部调用：``tables.lookup``（内置码表）、
  ``tavily.search``（联网检索）、``llm.decide``（模型判断）。
* **不发 thinking**：模型侧关掉了思维链（见 ``llm.py`` 的说明），没有可报的思考
  内容时就不编 —— 这也是 SDK 文档对 thinking 的要求。
* **校验不通过发 ``log(warn)``**，不硬塞成工具事件：工具 = 外部调用，
  规则校验是纯内存计算，把它伪装成工具只会污染「工具调用」这一列的含义。
"""
from __future__ import annotations

from contextlib import contextmanager

try:  # 平台 SDK：与 runner 同目录，由平台注入
    import miao_sdk as _sdk
except Exception:  # noqa: BLE001 —— 离线自检 / 老运行时：降级为空操作
    _sdk = None

#: SDK 是否可用。agent.py 据此决定走「带执行事件」的流式还是退回纯流式。
SDK_AVAILABLE = _sdk is not None

#: 本 agent 暴露的工具名（集中在这里，便于排查与文档对齐）
TOOL_TABLES = "tables.lookup"
TOOL_SEARCH = "tavily.search"
TOOL_LLM = "llm.decide"


class _NullTool:
    """SDK 不可用时的空句柄：接口与真句柄一致，什么都不做。"""

    __slots__ = ()

    def output(self, value) -> None:
        pass

    def fail(self, error) -> None:
        pass


def clear() -> None:
    """关闭当前线程的上报（非流式路径调用；无 SDK 时为空操作）。"""
    if _sdk is not None:
        _sdk.clear()


@contextmanager
def tool(name: str, input=None, **kwargs):
    """一次工具调用：进入发 ``tool_call``，退出发配对的 ``tool_result``。

    SDK 不可用时退化为空上下文（yield 一个空句柄），业务代码零改动。
    """
    if _sdk is None:
        yield _NullTool()
        return
    with _sdk.tool(name, input, **kwargs) as handle:
        yield handle


def log(text, level="info") -> None:
    """执行日志（降级、跳过、校验不通过等）。"""
    if _sdk is not None:
        _sdk.log(text, level=level)


def stream(work, *, render=None):
    """见 :func:`miao_sdk.stream`。SDK 缺失时由 ``agent.invoke`` 走兜底路径。"""
    return _sdk.stream(work, render=render)
