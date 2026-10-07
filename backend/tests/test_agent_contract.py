"""Agent 契约声明解析与校验（app/contract.py）单元测试。

覆盖三类：合法声明、非法声明的可定位报错、zip / 目录两种读取入口的兼容分支。
本文件不依赖数据库（只测纯函数）。
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.contract import (
    MAX_MANIFEST_BYTES,
    MAX_SCHEMA_DEPTH,
    ContractError,
    load_contract_from_dir,
    load_contract_from_zip,
    parse_contract,
)

pytestmark = pytest.mark.no_db

MINIMAL = """\
api_version: 1
input:
  schema:
    type: object
    required: [text]
    properties:
      text: {type: string, title: 内容}
  examples:
    - name: 默认
      value: {text: hello}
output:
  schema:
    type: object
    properties:
      answer: {type: string}
  display:
    primary: answer
"""


def _zip(files: dict[str, str | bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)
    return zipfile.ZipFile(buf)


# ========== 合法声明 ==========


def test_minimal_contract_parsed():
    contract = parse_contract(MINIMAL)
    assert contract["api_version"] == 1
    assert contract["input"]["schema"]["properties"]["text"]["title"] == "内容"
    assert contract["input"]["examples"][0] == {"name": "默认", "value": {"text": "hello"}}
    assert contract["output"]["display"]["primary"] == "answer"


def test_api_version_defaults_to_current():
    contract = parse_contract("input:\n  schema:\n    type: object\n")
    assert contract["api_version"] == 1


def test_manifest_json_is_equivalent():
    """JSON 是 YAML 子集：同一个解析器要能吃 manifest.json。"""
    text = json.dumps(
        {"input": {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}}}
    )
    contract = parse_contract(text, "manifest.json")
    assert contract["input"]["schema"]["properties"]["a"]["type"] == "string"


def test_x_prefixed_extension_kept():
    contract = parse_contract("x-owner: miao\ninput:\n  schema: {type: object}\n")
    assert contract["x-owner"] == "miao"


def test_output_display_secondary_accepted():
    text = "output:\n  display:\n    primary: answer\n    secondary: [positions, model]\n"
    contract = parse_contract(text)
    assert contract["output"]["display"]["secondary"] == ["positions", "model"]


def test_flow_style_nested_items_accepted():
    """wubi 那种「对象数组里套对象数组」的写法必须能过。"""
    text = (
        "input:\n"
        "  schema:\n"
        "    type: object\n"
        "    required: [positions]\n"
        "    properties:\n"
        "      positions:\n"
        "        type: array\n"
        "        items:\n"
        "          type: object\n"
        "          required: [key, candidates]\n"
        "          properties:\n"
        "            key: {type: string}\n"
        "            candidates:\n"
        "              type: array\n"
        "              items:\n"
        "                type: object\n"
        "                required: [glyph]\n"
        "                properties:\n"
        "                  glyph: {type: string}\n"
    )
    contract = parse_contract(text)
    items = contract["input"]["schema"]["properties"]["positions"]["items"]
    assert items["required"] == ["key", "candidates"]
    assert items["properties"]["candidates"]["items"]["required"] == ["glyph"]


# ========== 非法声明：报错必须可定位 ==========


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("inputt:\n  schema: {type: object}\n", "未知字段 'inputt'"),
        ("api_version: 2\n", "不支持的 api_version=2"),
        ("api_version: one\n", "api_version 必须是整数"),
        ("- a\n- b\n", "顶层必须是键值映射"),
        ("", "内容为空"),
        ("input:\n  schema:\n    type: objct\n", "type 不支持 'objct'"),
        ("input:\n  schema:\n    type: array\n", "根类型必须是 object"),
        ("input:\n  schema:\n    type: object\n    required: [a]\n", "没有 properties"),
        (
            "input:\n  schema:\n    type: object\n"
            "    properties: {a: {type: string}}\n"
            "    required: [b]\n",
            "required 里的 b 不在 properties 中",
        ),
        (
            "input:\n  schema:\n    type: object\n"
            "    properties: {a: {type: string, title: 1}}\n",
            "title 必须是字符串",
        ),
        (
            "input:\n  schema:\n    type: object\n"
            "    properties: {a: {type: string, enum: 1}}\n",
            "enum 必须是数组",
        ),
        (
            "input:\n  schema:\n    type: object\n"
            "    properties: {a: {type: string, required: 1}}\n",
            "required 必须是字符串数组",
        ),
        ("input:\n  examples:\n    - name: x\n", "examples[0].value 缺失"),
        ("input:\n  examples:\n    - value: [1, 2]\n", "examples[0].value 必须是对象"),
        ("input:\n  examples: 1\n", "examples 必须是数组"),
        ("input:\n  foo: 1\n", "input.foo 不是可用字段"),
        ("output:\n  display:\n    primary: 3\n", "display.primary 必须是字符串"),
        ("output:\n  display:\n    secondary: a\n", "display.secondary 必须是字符串数组"),
        ("input: 3\n", "input 必须是键值映射"),
        ("name: 123\n", "name 必须是字符串"),
    ],
)
def test_invalid_contracts_are_rejected(text, expected):
    with pytest.raises(ContractError) as excinfo:
        parse_contract(text)
    message = str(excinfo.value)
    assert expected in message
    # 报错必须带文件名前缀，agent 作者才知道去改哪个文件
    assert message.startswith("agent.yaml: ")


def test_schema_depth_limit():
    node: dict = {"type": "object"}
    for _ in range(MAX_SCHEMA_DEPTH + 2):
        node = {"type": "object", "properties": {"a": node}}
    text = json.dumps({"input": {"schema": node}})
    with pytest.raises(ContractError) as excinfo:
        parse_contract(text, "manifest.json")
    assert "嵌套超过" in str(excinfo.value)


# ========== YAML 陷阱（实测 PyYAML 6.0.3 行为）==========


def test_boolean_like_key_in_properties_rejected():
    """properties 里的 `no:` 会被 PyYAML 解析成布尔 False，必须报错而不是静默入库。"""
    text = "input:\n  schema:\n    type: object\n    properties:\n      no: {type: string}\n"
    with pytest.raises(ContractError) as excinfo:
        parse_contract(text)
    assert "非字符串键" in str(excinfo.value)


def test_date_value_rejected():
    text = "input:\n  examples:\n    - value: {when: 2026-01-01}\n"
    with pytest.raises(ContractError) as excinfo:
        parse_contract(text)
    assert "不是 JSON 基本类型" in str(excinfo.value)


def test_yaml_syntax_error_reports_position():
    with pytest.raises(ContractError) as excinfo:
        parse_contract("input:\n  schema: [\n")
    assert "YAML 语法错误" in str(excinfo.value)


def test_quoted_values_survive_yaml_traps():
    """规范推荐的加引号写法必须原样保留（不能被解析成布尔/八进制）。"""
    text = (
        "input:\n"
        "  examples:\n"
        "    - value:\n"
        '        code: "0123"\n'
        '        key: "no"\n'
        "        count: 3\n"
    )
    value = parse_contract(text)["input"]["examples"][0]["value"]
    assert value == {"code": "0123", "key": "no", "count": 3}


# ========== zip 入口 ==========


def test_zip_without_manifest_returns_none():
    """兼容底线：存量 agent 包里没有声明文件时，绝不能报错。"""
    with _zip({"agent.py": "def invoke(i, c): return {}", "requirements.txt": ""}) as zf:
        assert load_contract_from_zip(zf) is None


def test_zip_reads_manifest():
    with _zip({"agent.py": "", "agent.yaml": MINIMAL}) as zf:
        assert load_contract_from_zip(zf)["api_version"] == 1


def test_zip_reads_manifest_json_alias():
    with _zip({"agent.py": "", "manifest.json": MINIMAL}) as zf:
        assert load_contract_from_zip(zf)["api_version"] == 1


def test_zip_both_manifests_rejected():
    with _zip({"agent.yaml": MINIMAL, "manifest.json": "{}"}) as zf:
        with pytest.raises(ContractError) as excinfo:
            load_contract_from_zip(zf)
        assert "只保留一个" in str(excinfo.value)


def test_zip_manifest_in_subdirectory_is_ignored():
    with _zip({"agent.yaml": MINIMAL, "docs/agent.yaml": "bad: ["}) as zf:
        assert load_contract_from_zip(zf)["api_version"] == 1


def test_zip_invalid_manifest_rejected():
    with _zip({"agent.yaml": "input:\n  schema:\n    type: objct\n"}) as zf:
        with pytest.raises(ContractError):
            load_contract_from_zip(zf)


def test_zip_manifest_size_limit():
    big = "api_version: 1\nx-blob: " + "a" * MAX_MANIFEST_BYTES
    with _zip({"agent.yaml": big}) as zf:
        with pytest.raises(ContractError) as excinfo:
            load_contract_from_zip(zf)
        assert "上限" in str(excinfo.value)


def test_zip_manifest_must_be_utf8():
    with _zip({"agent.yaml": b"\xff\xfe\x00bad"}) as zf:
        with pytest.raises(ContractError) as excinfo:
            load_contract_from_zip(zf)
        assert "UTF-8" in str(excinfo.value)


# ========== 目录入口（激活时自愈）==========


def test_dir_without_manifest_returns_none(tmp_path: Path):
    (tmp_path / "agent.py").write_text("", encoding="utf-8")
    assert load_contract_from_dir(tmp_path) is None


def test_dir_reads_manifest(tmp_path: Path):
    (tmp_path / "agent.yaml").write_text(MINIMAL, encoding="utf-8")
    assert load_contract_from_dir(tmp_path)["input"]["schema"]["type"] == "object"


def test_dir_both_manifests_rejected(tmp_path: Path):
    (tmp_path / "agent.yaml").write_text(MINIMAL, encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ContractError) as excinfo:
        load_contract_from_dir(tmp_path)
    assert "只保留一个" in str(excinfo.value)


def test_dir_invalid_manifest_rejected(tmp_path: Path):
    (tmp_path / "manifest.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract_from_dir(tmp_path)
