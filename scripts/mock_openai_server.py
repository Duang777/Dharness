from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _executor_reply(prompt: str) -> dict[str, object]:
    if "write-hello" not in prompt:
        return {
            "action": "execute",
            "rationale": "the requested file does not exist yet",
            "plan": ["write the exact content", "verify the exact content"],
            "commands": [
                {
                    "id": "write-hello",
                    "script": "printf '%s\\n' 'Hello, world!' > /app/hello.txt",
                    "purpose": "create the requested file",
                    "cwd": "/app",
                    "timeout_sec": 30,
                    "mode": "change",
                }
            ],
        }
    return {
        "action": "finish",
        "rationale": "the file was written and needs a fresh assertion",
        "summary": "created /app/hello.txt with the requested content",
        "checks": [
            {
                "id": "check-hello",
                "kind": "artifact",
                "script": (
                    "test \"$(cat /app/hello.txt)\" = 'Hello, world!' && "
                    "printf 'isolated\\n' > /app/verification-only.txt"
                ),
                "proves": "the file exists and contains exactly Hello, world!",
                "cwd": "/app",
                "timeout_sec": 30,
            }
        ],
        "coverage": [
            {
                "requirement": "create hello.txt with Hello, world! as its content",
                "check_ids": ["check-hello"],
            }
        ],
    }


def _review_reply() -> dict[str, object]:
    return {
        "verdict": "accept",
        "rationale": "the shell assertion checks both existence and exact content",
        "missing_requirements": [],
        "suggested_checks": [],
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request.get("messages", [])
        prompt = "\n".join(str(item.get("content", "")) for item in messages)
        payload = (
            _review_reply()
            if "read-only completion reviewer" in prompt
            else _executor_reply(prompt)
        )
        body = {
            "id": f"mock-{time.time_ns()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.get("model", "mock-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(payload),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mock OpenAI server listening on http://{args.host}:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
