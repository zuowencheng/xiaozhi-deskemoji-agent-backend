"""统一工具管理器"""

from typing import Dict, List, Optional, Any
from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse
from .base import ToolType, ToolDefinition, ToolExecutor

# ============================================================================
# 工具屏蔽表（本地改造，非上游代码）—— 2026-10-02
# ----------------------------------------------------------------------------
# 设备端 MCP 自带的「定时器 / 日程」工具必须屏蔽，原因是实测踩坑：
#   用户说「30 秒之后提醒我喝水」→ LLM 选了 self.timer.create_countdown
#   → 屏幕上倒计时确实走了 → 到点**没有任何语音播报**。
# 因为设备端这些工具只在本地显示/记时，不会回调服务端 TTS，
# 属于"答应了但做不到"的陷阱。提醒统一由服务端 set_reminder +
# 提醒循环 + 8004 /notify 播报接管。
#
# 注意：不要靠提示词去"劝"模型别选这些工具——31 个工具里功能重叠时，
# 提示词约束力不够（实测已失败一次）。直接不暴露给 LLM 才干净。
# 提示词里也**不要**写"不要用 XX"，负面提示反而会让模型想起 XX。
# ============================================================================
_EXCLUDED_TOOL_PREFIXES = (
    "self_timer_",     # 设备端倒计时 / 延时任务
    "self_schedule_",  # 设备端日程事件
)


def _is_excluded(tool_name: str) -> bool:
    if not tool_name:
        return False
    name = tool_name.replace(".", "_")  # self.timer.xxx 与 self_timer_xxx 两种写法都拦
    return name.startswith(_EXCLUDED_TOOL_PREFIXES)


class ToolManager:
    """统一工具管理器，管理所有类型的工具"""

    def __init__(self, conn):
        self.conn = conn
        self.logger = setup_logging()
        self.executors: Dict[ToolType, ToolExecutor] = {}
        self._cached_tools: Optional[Dict[str, ToolDefinition]] = None
        self._cached_function_descriptions: Optional[List[Dict[str, Any]]] = None
        self._excluded_hits: List[str] = []

    def register_executor(self, tool_type: ToolType, executor: ToolExecutor):
        """注册工具执行器"""
        self.executors[tool_type] = executor
        self._invalidate_cache()
        self.logger.debug(f"注册工具执行器: {tool_type.value}")

    def _invalidate_cache(self):
        """使缓存失效"""
        self._cached_tools = None
        self._cached_function_descriptions = None

    def get_all_tools(self) -> Dict[str, ToolDefinition]:
        """获取所有工具定义"""
        if self._cached_tools is not None:
            return self._cached_tools

        all_tools = {}
        excluded = []
        for tool_type, executor in self.executors.items():
            try:
                tools = executor.get_tools()
                for name, definition in tools.items():
                    if _is_excluded(name):
                        excluded.append(name)
                        continue
                    if name in all_tools:
                        self.logger.warning(f"工具名称冲突: {name}")
                    all_tools[name] = definition
            except Exception as e:
                self.logger.error(f"获取{tool_type.value}工具时出错: {e}")

        if excluded:
            self._excluded_hits = sorted(excluded)
            self.logger.info(
                f"已屏蔽 {len(excluded)} 个设备端定时/日程工具（改由服务端 set_reminder 主动播报）: "
                + ", ".join(sorted(excluded))
            )

        self._cached_tools = all_tools
        return all_tools

    def get_function_descriptions(self) -> List[Dict[str, Any]]:
        """获取所有工具的函数描述（OpenAI格式）"""
        if self._cached_function_descriptions is not None:
            return self._cached_function_descriptions

        descriptions = []
        tools = self.get_all_tools()
        for tool_definition in tools.values():
            descriptions.append(tool_definition.description)

        self._cached_function_descriptions = descriptions
        return descriptions

    def has_tool(self, tool_name: str) -> bool:
        """检查是否存在指定工具"""
        tools = self.get_all_tools()
        return tool_name in tools

    def get_tool_type(self, tool_name: str) -> Optional[ToolType]:
        """获取工具类型"""
        tools = self.get_all_tools()
        tool_def = tools.get(tool_name)
        return tool_def.tool_type if tool_def else None

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行工具调用"""
        try:
            # 被屏蔽的工具：防 LLM 幻觉调用，引导回服务端方案
            if _is_excluded(tool_name):
                self.logger.warning(f"拦截被屏蔽的设备端工具调用: {tool_name}")
                return ActionResponse(
                    Action.REQLLM,
                    "提醒这类事请改用 set_reminder 来办，它会到点主动开口提醒；"
                    "请重新调用 set_reminder 设置提醒时间。",
                    None,
                )

            # 查找工具类型
            tool_type = self.get_tool_type(tool_name)
            if not tool_type:
                return ActionResponse(
                    action=Action.NOTFOUND,
                    response=f"工具 {tool_name} 不存在",
                )

            # 获取对应的执行器
            executor = self.executors.get(tool_type)
            if not executor:
                return ActionResponse(
                    action=Action.ERROR,
                    response=f"工具类型 {tool_type.value} 的执行器未注册",
                )

            # 执行工具
            self.logger.info(f"执行工具: {tool_name}，参数: {arguments}")
            result = await executor.execute(self.conn, tool_name, arguments)
            self.logger.debug(f"工具执行结果: {result}")
            return result

        except Exception as e:
            self.logger.error(f"执行工具 {tool_name} 时出错: {e}")
            return ActionResponse(action=Action.ERROR, response=str(e))

    def get_supported_tool_names(self) -> List[str]:
        """获取所有支持的工具名称"""
        tools = self.get_all_tools()
        return list(tools.keys())

    def refresh_tools(self):
        """刷新工具缓存"""
        self._invalidate_cache()
        self.logger.debug("工具缓存已刷新")

    def get_tool_statistics(self) -> Dict[str, int]:
        """获取工具统计信息"""
        stats = {}
        for tool_type, executor in self.executors.items():
            try:
                tools = executor.get_tools()
                stats[tool_type.value] = len(tools)
            except Exception as e:
                self.logger.error(f"获取{tool_type.value}工具统计时出错: {e}")
                stats[tool_type.value] = 0
        return stats
