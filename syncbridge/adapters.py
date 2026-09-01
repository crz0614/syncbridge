from __future__ import annotations

import json
import urllib.error
import urllib.request
from contextlib import contextmanager


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Credentials and customer data belong only to the configured endpoint.
        return None


@contextmanager
def _open_destination(req):
    try:
        with urllib.request.build_opener(_RejectRedirects()).open(req, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"destination returned HTTP {response.status}")
            yield response
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        # Persist status only: URLs/reason phrases can contain secrets or PII.
        raise RuntimeError(f"destination returned HTTP {code}") from None
    except urllib.error.URLError:
        raise RuntimeError("destination connection failed") from None


def send_rest(url: str, token: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with _open_destination(req):
        pass


def _notion_request(method: str, path: str, token: str, body: dict | None = None):
    req = urllib.request.Request(
        "https://api.notion.com/v1" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
    )
    with _open_destination(req) as response:
        return json.loads(response.read() or b"{}")


def notion_properties(payload: dict) -> dict:
    properties = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)):
            properties[key] = {"rich_text": [{"text": {"content": str(value)}}]}
    return properties


def send_notion(database_id: str, token: str, payload: dict, key_property: str | None = None) -> None:
    properties = notion_properties(payload)
    if key_property and key_property in payload:
        result = _notion_request("POST", f"/databases/{database_id}/query", token,
            {"filter": {"property": key_property, "rich_text": {"equals": str(payload[key_property])}}, "page_size": 1})
        if result.get("results"):
            _notion_request("PATCH", f"/pages/{result['results'][0]['id']}", token, {"properties": properties})
            return
    _notion_request("POST", "/pages", token, {"parent": {"database_id": database_id}, "properties": properties})


def get_notion_schema(database_id: str, token: str) -> dict:
    return _notion_request("GET", f"/databases/{database_id}", token)
