"""
腾讯云 COS 连接 smoke test。

不依赖 backend：直接用 boto3 验证上传/下载/删除。

跑法（在 miao-ai 根目录）：
  uv run python infra/cos_smoke_test.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 从根目录 .env 读凭证
ROOT = Path(__file__).parents[1]
load_dotenv(ROOT / ".env")

required = [
    "TENCENT_SECRET_ID",
    "TENCENT_SECRET_KEY",
    "TENCENT_REGION",
    "TENCENT_BUCKET",
    "COS_ENDPOINT",
]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"❌ 缺少环境变量：{missing}")
    sys.exit(1)

import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url=os.getenv("COS_ENDPOINT"),
    aws_access_key_id=os.getenv("TENCENT_SECRET_ID"),
    aws_secret_access_key=os.getenv("TENCENT_SECRET_KEY"),
    region_name=os.getenv("TENCENT_REGION"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)

bucket = os.getenv("TENCENT_BUCKET")
key = "miao-smoke-test.txt"
content = b"hello from miao-ai - phase 0 verification"

# 1. 上传
print("→ 上传测试文件…")
s3.put_object(Bucket=bucket, Key=key, Body=content)
print(f"✅ 上传 {key}")

# 2. 列出
print("→ 列出对象…")
resp = s3.list_objects_v2(Bucket=bucket)
objs = resp.get("Contents", [])
print(f"✅ bucket 中现有 {len(objs)} 个对象")
for o in objs:
    print(f"   - {o['Key']}  ({o['Size']} bytes)")

# 3. 下载
print("→ 下载测试文件…")
obj = s3.get_object(Bucket=bucket, Key=key)
downloaded = obj["Body"].read()
assert downloaded == content, "下载内容与上传不符！"
print(f"✅ 下载内容一致：{downloaded.decode()}")

# 4. 删除
print("→ 清理测试文件…")
s3.delete_object(Bucket=bucket, Key=key)
print(f"✅ 删除 {key}")

print("\n🎉 COS smoke test 全部通过")
