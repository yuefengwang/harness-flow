"""Minimal OpenCode API demo — create session, send message, print response.

Note: Uses requests instead of opencode_ai SDK due to httpx 502 compatibility
issue with opencode serve. The API contract is identical.
"""

import requests

SERVER = "http://127.0.0.1:4096"
TIMEOUT = 60
PROVIDER = "opencode"
MODEL = "mimo-v2.5-free"

session_resp = requests.post(f"{SERVER}/session", timeout=10)
session_id = session_resp.json()["id"]
print(f"[OK] Session created: {session_id[:16]}...")

try:
    chat_resp = requests.post(
        f"{SERVER}/session/{session_id}/message",
        json={
            # opencode 要求 model 为对象；传字符串会得到 HTTP 400。
            "model": {"providerID": PROVIDER, "modelID": MODEL},
            "parts": [{"type": "text", "text": "Hello, say hi in one sentence."}],
        },
        timeout=TIMEOUT,
    )
    chat_resp.raise_for_status()
    data = chat_resp.json()

    for part in data.get("parts", []):
        if part.get("type") == "text":
            print(f"[AGENT] {part['text']}")
        elif part.get("type") == "reasoning":
            print(f"[REASONING] {part['text'][:80]}...")

except requests.Timeout:
    print("[ERROR] Timeout")
except requests.HTTPError as e:
    print(f"[ERROR] HTTP {e.response.status_code}: {e.response.text[:200]}")
except Exception as e:
    print(f"[ERROR] {e}")

requests.post(f"{SERVER}/session/{session_id}/abort", timeout=10)
print("[OK] Session aborted, done.")