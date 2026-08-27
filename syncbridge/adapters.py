from __future__ import annotations

import json
import urllib.request


def send_rest(url: str, token: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"destination returned HTTP {response.status}")


def send_notion(database_id: str, token: str, payload: dict) -> None:
    properties = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)):
            properties[key] = {"rich_text": [{"text": {"content": str(value)}}]}
    body = {"parent": {"database_id": database_id}, "properties": properties}
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"Notion returned HTTP {response.status}")
