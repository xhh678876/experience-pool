from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import boto3
from botocore.client import Config

from .config import settings

_s3 = None


def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_key,
            aws_secret_access_key=settings.s3_secret,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
    return _s3


def put_trajectory(experience_id: UUID, trajectory: list[dict[str, Any]]) -> str:
    key = f"raw/{experience_id}.json"
    body = json.dumps({"trajectory": trajectory}).encode("utf-8")
    get_s3().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=body, ContentType="application/json"
    )
    return f"s3://{settings.s3_bucket}/{key}"


def get_trajectory(experience_id: UUID) -> dict[str, Any]:
    key = f"raw/{experience_id}.json"
    obj = get_s3().get_object(Bucket=settings.s3_bucket, Key=key)
    return json.loads(obj["Body"].read())
