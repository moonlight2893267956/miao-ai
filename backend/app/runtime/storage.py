"""
腾讯云 COS 封装（boto3 S3 兼容 API）。

只负责 agent zip 的上传/下载。
"""
from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config

from ..config import settings


@lru_cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.cos_endpoint,
        aws_access_key_id=settings.tencent_secret_id,
        aws_secret_access_key=settings.tencent_secret_key,
        region_name=settings.tencent_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def upload_zip(local_path: Path, key: str) -> str:
    """上传 zip 到 COS，返回 object key。"""
    _client().upload_file(str(local_path), settings.tencent_bucket, key)
    return key


def download_zip(key: str, dest: Path) -> None:
    """从 COS 下载到本地 dest 路径。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _client().download_file(settings.tencent_bucket, key, str(dest))


def get_zip_stream(key: str) -> Generator[bytes, None, None]:
    """从 COS 流式读取 zip 对象，逐 chunk 生成字节。

    使用 boto3 get_object() 返回 StreamingBody，
    通过 iter_chunks() 逐块 yield，避免将整个文件加载到内存。
    """
    response = _client().get_object(Bucket=settings.tencent_bucket, Key=key)
    for chunk in response["Body"].iter_chunks(chunk_size=64 * 1024):
        yield chunk
