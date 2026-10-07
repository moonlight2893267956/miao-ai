"""版本上传 / 激活路径的契约解析集成测试。

覆盖：无声明照常 201（兼容底线）、合法声明随版本落库、非法声明 400 且报错可定位、
契约按版本隔离（v2 不带声明不会继承 v1 的契约）。
COS 上传用 monkeypatch 打桩：这里只关心契约解析与落库，不产生真实对象存储请求。
"""
import io
import uuid
import zipfile

import pytest
from httpx import AsyncClient

VALID_YAML = """\
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
  display:
    primary: answer
"""

INVALID_YAML = "input:\n  schema:\n    type: objct\n"

AGENT_PY = "def invoke(input, config=None):\n    return {'answer': 'ok'}\n"


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf.getvalue()


# 无声明文件：存量 agent 的形态
ZIP_NO_CONTRACT = _zip_bytes({"agent.py": AGENT_PY, "requirements.txt": ""})
# 合法声明
ZIP_VALID = _zip_bytes({"agent.py": AGENT_PY, "agent.yaml": VALID_YAML})
# 声明写错：type 拼错
ZIP_INVALID = _zip_bytes({"agent.py": AGENT_PY, "agent.yaml": INVALID_YAML})
# 两个声明文件同时存在
ZIP_BOTH = _zip_bytes(
    {"agent.py": AGENT_PY, "agent.yaml": VALID_YAML, "manifest.json": "{}"}
)


@pytest.fixture
def stub_cos(monkeypatch) -> list[str]:
    """打桩 COS 上传，返回被调用过的对象 key 列表。"""
    uploads: list[str] = []

    def fake_upload(tmp_path, key):  # noqa: ANN001 - 与 storage.upload_zip 同签名
        uploads.append(key)
        return key

    monkeypatch.setattr("app.api.versions.upload_zip", fake_upload)
    return uploads


def _unique_name() -> str:
    return f"test-ct-{uuid.uuid4().hex[:8]}"


async def _upload(ac: AsyncClient, name: str, version: str, content: bytes):
    return await ac.post(
        f"/api/v1/agents/{name}/versions",
        data={"version": version},
        files={"file": ("agent.zip", content, "application/zip")},
    )


@pytest.mark.asyncio
async def test_upload_without_contract_succeeds(logged_in_client: AsyncClient, stub_cos):
    """兼容底线：没有 agent.yaml 的包必须照常上传，契约为 None。"""
    name = _unique_name()
    await logged_in_client.post("/api/v1/agents", json={"name": name})

    r = await _upload(logged_in_client, name, "v1", ZIP_NO_CONTRACT)

    assert r.status_code == 201, r.text
    assert r.json()["contract"] is None
    assert stub_cos, "校验通过后应继续走 COS 上传"


@pytest.mark.asyncio
async def test_upload_valid_contract_persisted(logged_in_client: AsyncClient, stub_cos):
    name = _unique_name()
    await logged_in_client.post("/api/v1/agents", json={"name": name})

    r = await _upload(logged_in_client, name, "v1", ZIP_VALID)

    assert r.status_code == 201, r.text
    contract = r.json()["contract"]
    assert contract["api_version"] == 1
    assert contract["input"]["schema"]["properties"]["text"]["title"] == "内容"
    assert contract["output"]["display"]["primary"] == "answer"

    # 列表接口同样要带上契约（前端从版本列表里取生效契约）
    r = await logged_in_client.get(f"/api/v1/agents/{name}/versions")
    assert r.status_code == 200
    assert r.json()[0]["contract"]["api_version"] == 1


@pytest.mark.asyncio
async def test_upload_invalid_contract_rejected_before_cos(
    logged_in_client: AsyncClient, stub_cos
):
    """非法声明必须 400 拒绝，且校验前置 —— 不能先把文件传到 COS 再报错。"""
    name = _unique_name()
    await logged_in_client.post("/api/v1/agents", json={"name": name})

    r = await _upload(logged_in_client, name, "v1", ZIP_INVALID)

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "agent.yaml" in detail
    assert "type 不支持 'objct'" in detail
    assert stub_cos == [], "非法声明不应产生 COS 上传"


@pytest.mark.asyncio
async def test_upload_both_manifest_files_rejected(
    logged_in_client: AsyncClient, stub_cos
):
    name = _unique_name()
    await logged_in_client.post("/api/v1/agents", json={"name": name})

    r = await _upload(logged_in_client, name, "v1", ZIP_BOTH)

    assert r.status_code == 400
    assert "只保留一个" in r.json()["detail"]


@pytest.mark.asyncio
async def test_contract_is_per_version(logged_in_client: AsyncClient, stub_cos):
    """契约随版本：v2 不带声明文件时不能继承 v1 的契约。"""
    name = _unique_name()
    await logged_in_client.post("/api/v1/agents", json={"name": name})

    r1 = await _upload(logged_in_client, name, "v1", ZIP_VALID)
    assert r1.status_code == 201
    assert r1.json()["contract"] is not None

    r2 = await _upload(logged_in_client, name, "v2", ZIP_NO_CONTRACT)
    assert r2.status_code == 201
    assert r2.json()["contract"] is None


@pytest.mark.asyncio
async def test_upload_still_requires_agent_py(logged_in_client: AsyncClient, stub_cos):
    """原有校验不被破坏：zip 里没有 agent.py 仍然 400。"""
    name = _unique_name()
    await logged_in_client.post("/api/v1/agents", json={"name": name})

    r = await _upload(logged_in_client, name, "v1", _zip_bytes({"agent.yaml": VALID_YAML}))

    assert r.status_code == 400
    assert "agent.py" in r.json()["detail"]
