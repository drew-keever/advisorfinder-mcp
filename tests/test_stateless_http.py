"""The server runs in stateless HTTP mode: no MCP session lives on the server,
so a restart or deploy cannot leave clients with an unknown Mcp-Session-Id
(404 "Session not found", reported by the Claude directory as
McpSessionTerminated). Checked in the source because main() starts a real
server."""
import ast
import inspect

from advisorfinder_mcp import server


def test_main_runs_http_transport_stateless():
    tree = ast.parse(inspect.getsource(server.main))
    runs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert len(runs) == 1
    kwargs = {k.arg: k.value for k in runs[0].keywords}
    assert isinstance(kwargs.get("stateless_http"), ast.Constant)
    assert kwargs["stateless_http"].value is True
