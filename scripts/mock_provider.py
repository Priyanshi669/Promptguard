"""Local OpenAI-compatible mock provider for failover demonstrations.

Serves POST /v1/chat/completions on localhost. Candidate requests
(system-role first message) get a canned classification + summary; judge
requests get a canned GEval score blob. Logs arrival timestamps so request
spacing can be verified.

This is a TEST DOUBLE. Its scores and categories are synthetic and must
never be used as quality evidence. Start it with:

    uv run python scripts/mock_provider.py [port] [logfile]
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
LOG_PATH = sys.argv[2] if len(sys.argv) > 2 else "mock_provider.log"

CANDIDATE_RESPONSE = json.dumps(
    {
        "category": "balance_not_updated_after_bank_transfer",
        "summary": "Mock summary: customer asks about a pending transfer.",
    }
)

JUDGE_RESPONSE = json.dumps(
    {"score": 0.9, "reason": "Mock judge response (synthetic)."}
)


def log(message: str) -> None:
    with open(LOG_PATH, "a", encoding="utf-8") as file:
        file.write(f"{message}\n")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body_raw = self.rfile.read(length).decode("utf-8")
        body = json.loads(body_raw)

        first_role = body["messages"][0]["role"]
        is_candidate = first_role == "system"
        content = (
            CANDIDATE_RESPONSE if is_candidate else JUDGE_RESPONSE
        )
        log(
            f"{time.strftime('%H:%M:%S')} role={first_role} "
            f"model={body.get('model')}"
        )

        response = json.dumps(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": content}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args: object) -> None:
        return None


if __name__ == "__main__":
    log(f"--- mock provider started on port {PORT} ---")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
