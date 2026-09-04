import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent_foundry.contracts import Identity, Policy
from agent_foundry.http_tools import http_tool
from agent_foundry.tools_gateway import ToolRegistry


class _EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"path": self.path}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"received": json.loads(body)}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def echo_server():
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


def test_http_tool_get_with_path_placeholder_and_query_params(echo_server):
    tool = http_tool("get_order", "fetch an order", url=echo_server + "/orders/{order_id}")
    result = tool.fn(order_id="A100", verbose="true")
    assert result["path"] == "/orders/A100?verbose=true"


def test_http_tool_post_sends_json_body(echo_server):
    tool = http_tool("create_order", "create an order", url=echo_server + "/orders", method="POST")
    result = tool.fn(sku="widget", qty=3)
    assert result["received"] == {"sku": "widget", "qty": 3}


def test_http_tool_composes_through_registry_rbac(echo_server):
    tool = http_tool("get_status", "status", url=echo_server + "/status/{id}")
    registry = ToolRegistry()
    registry.register(tool)
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"get_status"}))
    result = registry.invoke("get_status", {"id": "42"}, identity=identity, policy=policy)
    assert result.ok and result.output["path"] == "/status/42"
