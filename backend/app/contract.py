"""Agent 契约声明解析与校验。

agent 代码包**根目录**可放置 `agent.yaml`（推荐，人写友好）或 `manifest.json`
（等价别名，机器生成场景），声明自己的入参 / 出参与「示例输入」。平台在
**上传版本**与**激活版本**两个时机解析它，随版本存入 `agent_versions.contract`，
供 Try Run 面板渲染表单。

三条硬规则（完整规范见 docs/agent-contract.md）：

1. 无声明文件 = 不识别契约（返回 ``None``），上传 / 激活 / 调用 / UI 全部维持原状；
2. 有声明文件但非法 = 抛 :class:`ContractError`，上传时以 400 拒绝 —— 显式声明必须
   可信，判据前置到上传时刻，而不是拖到运行时才暴露；
3. 只用 ``yaml.safe_load``（zip 内容不可信，禁止 ``yaml.load``），并限制文件大小。

契约是**版本**的属性：随 agent 版本一起存储、一起切换，因此不需要任何"清理旧
schema"的逻辑，也从根上杜绝旧契约残留误导 UI。
"""
from __future__ import annotations

import math
import zipfile
from pathlib import Path
from typing import Any

import yaml

# zip 根级精确文件名（不递归查找），与 agent.py 的校验口径保持一致
MANIFEST_NAMES: tuple[str, ...] = ("agent.yaml", "manifest.json")

# 声明文件大小上限：契约是给人写的小文件，超过这个量级基本可以断定不是契约
MAX_MANIFEST_BYTES = 64 * 1024

# 契约规范版本：不匹配直接拒绝，避免"旧平台读新契约"产生误判
CURRENT_API_VERSION = 1

SUPPORTED_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean"}
)

# schema 递归深度上限：畸形/恶意声明不能让平台递归到爆栈
MAX_SCHEMA_DEPTH = 8

# 顶层允许的业务字段；其余非 x- 前缀的键一律拒绝（写错字段名要立刻发现）
_TOP_LEVEL_KEYS = ("api_version", "name", "description", "input", "output")

# schema 节点里要求为字符串的字段
_SCHEMA_STRING_KEYS = ("title", "description", "format")

_IO_KEYS = ("input", "output")


class ContractError(Exception):
    """声明文件存在但不合法。

    ``message`` 必须包含**文件名**与**可定位的字段路径**（如
    ``agent.yaml: input.schema.properties.positions.items: 不支持的 type 'objct'``），
    让 agent 作者一眼知道改哪一行。
    """


def parse_contract(text: str, filename: str = MANIFEST_NAMES[0]) -> dict[str, Any]:
    """解析并校验声明文本，返回可安全写入 JSON 列的规范化契约。

    :raises ContractError: 语法错误或结构不合法（错误信息含文件名前缀）
    """
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ContractError(
            f"{filename}: 文件超过 {MAX_MANIFEST_BYTES // 1024}KB 上限"
        )

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"{filename}: YAML 语法错误（{_yaml_error_hint(exc)}）") from exc

    if raw is None:
        raise ContractError(f"{filename}: 内容为空（不想要契约请删除该文件）")
    if not isinstance(raw, dict):
        raise ContractError(
            f"{filename}: 顶层必须是键值映射，实际是 {type(raw).__name__}"
        )

    contract: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ContractError(
                f"{filename}: 顶层出现非字符串键 {key!r}"
                "（YAML 会把 yes / no / on / off 解析成布尔值，这类键名请加引号）"
            )
        if key.startswith("x-"):
            contract[key] = value  # 允许 x- 前缀自定义扩展
        elif key not in _TOP_LEVEL_KEYS:
            allowed = ", ".join(_TOP_LEVEL_KEYS)
            raise ContractError(
                f"{filename}: 未知字段 '{key}'（可用：{allowed}，或 x- 前缀自定义）"
            )
        else:
            contract[key] = value

    api_version = contract.get("api_version", CURRENT_API_VERSION)
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        raise ContractError(f"{filename}: api_version 必须是整数（如 1）")
    if api_version != CURRENT_API_VERSION:
        raise ContractError(
            f"{filename}: 不支持的 api_version={api_version}"
            f"（当前只支持 {CURRENT_API_VERSION}）"
        )
    contract["api_version"] = api_version

    for key in ("name", "description"):
        if key in contract and not isinstance(contract[key], str):
            raise ContractError(f"{filename}: {key} 必须是字符串")

    _validate_io(contract.get("input"), "input", filename, with_examples=True)
    _validate_io(contract.get("output"), "output", filename, with_examples=False)

    # 存库前做一次 JSON 基本类型体检：拦住 YAML 的日期 / 二进制 / NaN 等
    _assert_json_safe(contract, filename, path="")
    return contract


def load_contract_from_zip(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """从上传的 zip 根级读取声明并解析。

    :returns: 契约 dict；**无声明文件时返回 None**（调用方据此走兼容分支）
    :raises ContractError: 声明存在但不合法
    """
    found = [name for name in MANIFEST_NAMES if name in set(zf.namelist())]
    if not found:
        return None
    filename = _pick_single(found)

    info = zf.getinfo(filename)
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ContractError(
            f"{filename}: 文件超过 {MAX_MANIFEST_BYTES // 1024}KB 上限"
        )
    return parse_contract(_decode_utf8(zf.read(filename), filename), filename)


def load_contract_from_dir(directory: Path) -> dict[str, Any] | None:
    """从已解包的 agent 工作目录重新读取声明（激活时自愈用）。

    激活会清空工作目录后重新解包，因此这里解析到的永远是"实际部署的代码"对应的
    契约；目录里没有声明文件时返回 None，由调用方清空旧契约。
    """
    existing = [directory / name for name in MANIFEST_NAMES if (directory / name).is_file()]
    if not existing:
        return None
    if len(existing) > 1:
        names = " 与 ".join(item.name for item in existing)
        raise ContractError(f"同时存在 {names}，请只保留一个声明文件（推荐 agent.yaml）")
    path = existing[0]
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ContractError(
            f"{path.name}: 文件超过 {MAX_MANIFEST_BYTES // 1024}KB 上限"
        )
    return parse_contract(_decode_utf8(path.read_bytes(), path.name), path.name)


# ========== 内部实现 ==========


def _pick_single(found: list[str]) -> str:
    if len(found) > 1:
        raise ContractError(
            f"同时存在 {' 与 '.join(found)}，请只保留一个声明文件（推荐 agent.yaml）"
        )
    return found[0]


def _decode_utf8(raw: bytes, filename: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{filename}: 必须是 UTF-8 编码（{exc}）") from exc


def _yaml_error_hint(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc)
    if mark is not None:
        # YAML 行列从 0 起算，转成编辑器口径（1 起）
        return f"第 {mark.line + 1} 行第 {mark.column + 1} 列：{problem}"
    return str(problem)


def _validate_io(
    node: Any, path: str, filename: str, *, with_examples: bool
) -> None:
    """校验 input / output 区块。"""
    if node is None:
        return
    if not isinstance(node, dict):
        raise ContractError(f"{filename}: {path} 必须是键值映射")

    allowed = ("schema", "examples") if with_examples else ("schema", "display")
    for key in node:
        if not isinstance(key, str):
            raise ContractError(
                f"{filename}: {path} 出现非字符串键 {key!r}（键名请加引号）"
            )
        if key not in allowed and not key.startswith("x-"):
            raise ContractError(
                f"{filename}: {path}.{key} 不是可用字段（可用：{', '.join(allowed)}）"
            )

    schema = node.get("schema")
    if schema is not None:
        _validate_schema(schema, f"{path}.schema", filename, depth=0)
        root_type = schema.get("type")
        if root_type not in (None, "object"):
            raise ContractError(
                f"{filename}: {path}.schema 根类型必须是 object，实际是 {root_type!r}"
            )

    if with_examples:
        _validate_examples(node.get("examples"), path, filename)
    else:
        _validate_display(node.get("display"), path, filename)


def _validate_schema(node: Any, path: str, filename: str, *, depth: int) -> None:
    """校验受控 JSON Schema 子集（结构校验，不做语义求值）。"""
    if depth > MAX_SCHEMA_DEPTH:
        raise ContractError(f"{filename}: {path} 嵌套超过 {MAX_SCHEMA_DEPTH} 层")
    if not isinstance(node, dict):
        raise ContractError(f"{filename}: {path} 必须是键值映射")

    node_type = node.get("type")
    if node_type is not None and node_type not in SUPPORTED_TYPES:
        supported = ", ".join(sorted(SUPPORTED_TYPES))
        raise ContractError(
            f"{filename}: {path}.type 不支持 {node_type!r}（可用：{supported}）"
        )

    for key in _SCHEMA_STRING_KEYS:
        if key in node and not isinstance(node[key], str):
            raise ContractError(f"{filename}: {path}.{key} 必须是字符串")

    properties = node.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise ContractError(f"{filename}: {path}.properties 必须是键值映射")
        for name, child in properties.items():
            if not isinstance(name, str):
                raise ContractError(
                    f"{filename}: {path}.properties 出现非字符串键 {name!r}"
                    "（键名是 yes / no / on / off 这类时请加引号）"
                )
            _validate_schema(child, f"{path}.properties.{name}", filename, depth=depth + 1)

    required = node.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise ContractError(f"{filename}: {path}.required 必须是字符串数组")
        if properties is None:
            raise ContractError(
                f"{filename}: {path}.required 存在，但同级没有 properties 可供对照"
            )
        missing = [item for item in required if item not in properties]
        if missing:
            raise ContractError(
                f"{filename}: {path}.required 里的 {', '.join(missing)} 不在 properties 中"
            )

    if "enum" in node and not isinstance(node["enum"], list):
        raise ContractError(f"{filename}: {path}.enum 必须是数组")

    if "items" in node:
        _validate_schema(node["items"], f"{path}.items", filename, depth=depth + 1)


def _validate_examples(examples: Any, path: str, filename: str) -> None:
    if examples is None:
        return
    if not isinstance(examples, list):
        raise ContractError(f"{filename}: {path}.examples 必须是数组")
    for index, item in enumerate(examples):
        where = f"{path}.examples[{index}]"
        if not isinstance(item, dict):
            raise ContractError(
                f"{filename}: {where} 必须是键值映射（形如 {{name: 名称, value: {{...}}}}）"
            )
        if "value" not in item:
            raise ContractError(f"{filename}: {where}.value 缺失（示例的实际入参对象）")
        if not isinstance(item["value"], dict):
            raise ContractError(f"{filename}: {where}.value 必须是对象")
        if "name" in item and not isinstance(item["name"], str):
            raise ContractError(f"{filename}: {where}.name 必须是字符串")


def _validate_display(display: Any, path: str, filename: str) -> None:
    if display is None:
        return
    if not isinstance(display, dict):
        raise ContractError(f"{filename}: {path}.display 必须是键值映射")
    primary = display.get("primary")
    if primary is not None and not isinstance(primary, str):
        raise ContractError(f"{filename}: {path}.display.primary 必须是字符串")
    secondary = display.get("secondary")
    if secondary is not None and (
        not isinstance(secondary, list)
        or not all(isinstance(item, str) for item in secondary)
    ):
        raise ContractError(
            f"{filename}: {path}.display.secondary 必须是字符串数组"
        )


def _assert_json_safe(value: Any, filename: str, *, path: str) -> None:
    """深度检查契约只含 JSON 基本类型，避免入库时才炸。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(
                    f"{filename}: {path or '顶层'} 出现非字符串键 {key!r}"
                    "（YAML 会把 yes / no / on / off 解析成布尔值，这类键名请加引号）"
                )
            _assert_json_safe(item, filename, path=f"{path}.{key}" if path else key)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_safe(item, filename, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ContractError(f"{filename}: {path} 的值不能是 NaN / Infinity")
    raise ContractError(
        f"{filename}: {path} 的值 {value!r} 不是 JSON 基本类型"
        "（YAML 会把 2026-01-01 解析成日期等，这类值请加引号）"
    )
