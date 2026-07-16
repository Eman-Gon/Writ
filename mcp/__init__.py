"""Writ MCP surface — the agent's identity boundary.

NAMING HAZARD, read before importing MCP client SDKs inside this repo: this
package shadows the PyPI `mcp` package for any process with the repo root on
sys.path. The server here is self-contained JSON-RPC and does not need the
SDK; a foreign-agent MCP *client* using the SDK must run without the repo
root on its path (own venv / own directory).
"""
