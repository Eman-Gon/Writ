"""Launch an uncoached stock Claude Code agent with Writ tools only."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from connectors.writ_api import PipelineAuth, PipelineClient
from schemas import EvidenceRef
from wire import MCP_TOOLS, TOOL_FETCH_DOCUMENT, TOOL_PROPOSE_MUTATION, TaskRequest, TaskResponse


MCP_SERVER_NAME = "writ"
SYSTEM_PROMPT = "You are an accounts payable agent. Process invoices."
USER_PROMPT = "Process the invoice available to you."


@dataclass(frozen=True)
class HarnessConfig:
    api_base_url: str
    pipeline_auth: PipelineAuth
    task_ref: str
    document_ids: tuple[str, ...]
    mcp_url: str | None = None
    mcp_command: tuple[str, ...] = (sys.executable, "-m", "mcp.server")
    mcp_environment: tuple[tuple[str, str], ...] = ()
    ttl_seconds: int = 3600
    claude_binary: str = "claude"
    model: str | None = None
    max_turns: int = 20
    timeout_seconds: float = 600.0
    runs_dir: Path = Path("harness/foreign_agent/runs")


@dataclass(frozen=True)
class HarnessRun:
    task: TaskResponse
    returncode: int
    transcript_path: Path
    stderr_path: Path
    report_path: Path
    report: dict[str, Any]


class ForeignAgentHarness:
    def __init__(self, config: HarnessConfig, pipeline: PipelineClient | None = None) -> None:
        self.config = config
        self.pipeline = pipeline or PipelineClient(config.api_base_url, config.pipeline_auth)

    def run(self) -> HarnessRun:
        binary = shutil.which(self.config.claude_binary)
        if binary is None:
            raise RuntimeError(
                f"stock Claude Code CLI {self.config.claude_binary!r} was not found on PATH"
            )

        task = self.pipeline.create_task(
            TaskRequest(
                task_ref=self.config.task_ref,
                document_ids=list(self.config.document_ids),
                ttl_seconds=self.config.ttl_seconds,
            )
        )
        run_dir = self._new_run_dir(task.task_id)
        transcript_path = run_dir / "claude-stream.jsonl"
        stderr_path = run_dir / "claude-stderr.log"
        report_path = run_dir / "report.json"

        with tempfile.TemporaryDirectory(prefix="writ-foreign-agent-") as isolated:
            isolated_path = Path(isolated)
            config_path = isolated_path / "mcp.json"
            config_path.write_text(
                json.dumps(
                    build_mcp_config(
                        self.config.mcp_url,
                        command=self.config.mcp_command,
                        server_environment=dict(self.config.mcp_environment),
                    ),
                    indent=2,
                )
                + "\n"
            )
            config_path.chmod(0o600)
            command = build_claude_command(
                binary,
                config_path,
                model=self.config.model,
                max_turns=self.config.max_turns,
            )
            environment = build_agent_environment(os.environ, task.task_token)
            try:
                completed = subprocess.run(
                    command,
                    cwd=isolated_path,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                completed = subprocess.CompletedProcess(
                    command,
                    124,
                    stdout=_as_text(exc.stdout),
                    stderr=_as_text(exc.stderr)
                    + f"\nforeign-agent run timed out after {self.config.timeout_seconds}s\n",
                )

        transcript_path.write_text(completed.stdout)
        stderr_path.write_text(completed.stderr)
        report = analyze_transcript(completed.stdout)
        manifest_verified, server_manifest_ids, manifest_note = _verify_local_manifest(
            self.config,
            task.task_id,
            report["fetched_envelope_ids"],
        )
        report.update(
            {
                "task_id": task.task_id,
                "task_ref": self.config.task_ref,
                "document_ids": list(self.config.document_ids),
                "foreign_agent": "Claude Code CLI",
                "returncode": completed.returncode,
                "system_prompt": SYSTEM_PROMPT,
                "user_prompt": USER_PROMPT,
                "tool_configuration": "Writ MCP only; all built-in tools disabled",
                "server_manifest_envelope_ids": server_manifest_ids,
                "server_manifest_verified": manifest_verified,
                "server_manifest_note": manifest_note,
            }
        )
        report["week_one_gate_passed"] = bool(
            report["used_tools_correctly_without_coaching"] and manifest_verified
        )
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return HarnessRun(
            task=task,
            returncode=completed.returncode,
            transcript_path=transcript_path,
            stderr_path=stderr_path,
            report_path=report_path,
            report=report,
        )

    def _new_run_dir(self, task_id: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_task_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in task_id)
        run_dir = self.config.runs_dir / f"{timestamp}-{safe_task_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir


def build_mcp_config(
    mcp_url: str | None,
    *,
    command: tuple[str, ...] = (),
    server_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One MCP server, task-token auth, with no persistent secret in the file."""

    if mcp_url:
        server: dict[str, Any] = {
            "type": "http",
            "url": mcp_url,
            "headers": {"Authorization": "Bearer ${WRIT_TASK_TOKEN}"},
        }
    else:
        if not command:
            raise ValueError("stdio MCP configuration requires a command")
        environment = dict(server_environment or {})
        environment["WRIT_TASK_TOKEN"] = "${WRIT_TASK_TOKEN}"
        server = {
            "type": "stdio",
            "command": command[0],
            "args": list(command[1:]),
            "env": environment,
        }
    return {"mcpServers": {MCP_SERVER_NAME: server}}


def build_claude_command(
    binary: str,
    mcp_config_path: Path,
    *,
    model: str | None,
    max_turns: int,
) -> list[str]:
    """Build a stock CLI invocation whose only executable tools are Writ's."""

    allowed_tools = ",".join(f"mcp__{MCP_SERVER_NAME}__{name}" for name in MCP_TOOLS)
    command = [
        binary,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path),
        "--tools",
        "",
        "--allowedTools",
        allowed_tools,
        "--disable-slash-commands",
        "--no-chrome",
        "--max-turns",
        str(max_turns),
        "--system-prompt",
        SYSTEM_PROMPT,
    ]
    if model:
        command.extend(["--model", model])
    command.append(USER_PROMPT)
    return command


def build_agent_environment(source: Mapping[str, str], task_token: str) -> dict[str, str]:
    """Give the agent its task identity without leaking pipeline identities."""

    environment = dict(source)
    for secret_name in {
        "GMAIL_ACCESS_TOKEN",
        "WRIT_PIPELINE_CREDENTIAL",
        "WRIT_PIPELINE_HEADER",
        "WRIT_PIPELINE_SCHEME",
    }:
        environment.pop(secret_name, None)
    environment["WRIT_TASK_TOKEN"] = task_token
    return environment


def analyze_transcript(transcript: str) -> dict[str, Any]:
    events: list[Any] = []
    invalid_lines = 0
    for line in transcript.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            invalid_lines += 1

    discovered_tools: set[str] = set()
    for event in events:
        if (
            isinstance(event, dict)
            and event.get("type") == "system"
            and event.get("subtype") == "init"
            and isinstance(event.get("tools"), list)
        ):
            discovered_tools.update(tool for tool in event["tools"] if isinstance(tool, str))

    tool_uses: dict[str, dict[str, Any]] = {}
    tool_results: dict[str, Any] = {}
    for event in events:
        for value in _walk(event):
            if not isinstance(value, dict):
                continue
            if value.get("type") == "tool_use" and isinstance(value.get("name"), str):
                tool_id = value.get("id")
                if isinstance(tool_id, str):
                    tool_uses[tool_id] = value
            if value.get("type") == "tool_result" and isinstance(value.get("tool_use_id"), str):
                tool_results[value["tool_use_id"]] = _decode_tool_result(value.get("content"))

    expected_names = {f"mcp__{MCP_SERVER_NAME}__{name}" for name in MCP_TOOLS}
    calls: list[dict[str, Any]] = []
    evidence_valid = True
    proposed = False
    fetch_envelope_ids: list[str] = []
    for tool_id, use in tool_uses.items():
        name = use["name"]
        tool_input = use.get("input") if isinstance(use.get("input"), dict) else {}
        result = tool_results.get(tool_id)
        calls.append(
            {
                "name": name,
                "input": _redact_tool_input(tool_input),
                "result": _redact_tool_result(result),
            }
        )
        if name == f"mcp__{MCP_SERVER_NAME}__{TOOL_PROPOSE_MUTATION}":
            proposed = True
            evidence = tool_input.get("evidence")
            if not isinstance(evidence, list):
                evidence_valid = False
            else:
                for reference in evidence:
                    try:
                        EvidenceRef.model_validate(reference)
                    except ValueError:
                        evidence_valid = False
        if name == f"mcp__{MCP_SERVER_NAME}__{TOOL_FETCH_DOCUMENT}":
            envelope_id = _find_string(result, "envelope_id")
            if envelope_id:
                fetch_envelope_ids.append(envelope_id)

    seen_names = {call["name"] for call in calls}
    discovered_mcp_tools = {name for name in discovered_tools if name.startswith("mcp__")}
    discovered_builtin_tools = discovered_tools - discovered_mcp_tools
    tool_surface_matches_wire = discovered_mcp_tools == expected_names and not discovered_builtin_tools
    return {
        "events_parsed": len(events),
        "invalid_transcript_lines": invalid_lines,
        "tool_calls": calls,
        "used_tools": sorted(seen_names),
        "discovered_tools": sorted(discovered_tools),
        "discovered_mcp_tools": sorted(discovered_mcp_tools),
        "discovered_builtin_tools": sorted(discovered_builtin_tools),
        "tool_surface_matches_wire": tool_surface_matches_wire,
        "used_only_declared_writ_tools": seen_names <= expected_names,
        "unknown_tools": sorted(seen_names - expected_names),
        "fetched_envelope_ids": fetch_envelope_ids,
        "proposed_mutation": proposed,
        "evidence_refs_structurally_valid": proposed and evidence_valid,
        "used_tools_correctly_without_coaching": bool(
            proposed
            and evidence_valid
            and fetch_envelope_ids
            and seen_names <= expected_names
            and tool_surface_matches_wire
        ),
    }


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _decode_tool_result(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    if isinstance(content, list):
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if len(text_parts) == 1:
            return _decode_tool_result(text_parts[0])
    return content


def _find_string(value: Any, key: str) -> str | None:
    for item in _walk(value):
        if isinstance(item, dict) and isinstance(item.get(key), str):
            return item[key]
    return None


def _redact_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "<omitted from report>" if key == "proposed_value" else child
        for key, child in value.items()
    }


def _redact_tool_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<omitted from report>" if key in {"text", "task_token"} else _redact_tool_result(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_tool_result(child) for child in value]
    if isinstance(value, str) and len(value) > 500:
        return "<long unstructured result omitted from report>"
    return value


def _verify_local_manifest(
    config: HarnessConfig,
    task_id: str,
    fetched_envelope_ids: list[str],
) -> tuple[bool, list[str] | None, str]:
    if config.mcp_url:
        return (
            False,
            None,
            "wire.py exposes no pipeline-authenticated manifest inspection endpoint for remote runs",
        )
    database_path = dict(config.mcp_environment).get("STATEGUARD_DB")
    if not database_path:
        return False, None, "local stdio run did not declare STATEGUARD_DB"

    # Local test-only introspection of Fable's store; this is not a product API.
    from gateway.store import Store

    actual = Store(database_path).task_manifest_envelope_ids(task_id)
    expected = list(dict.fromkeys(fetched_envelope_ids))
    matches = actual == expected
    return (
        matches,
        actual,
        "local stdio store matched fetched envelopes"
        if matches
        else "local stdio store did not match fetched envelopes",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mint a Writ task and run an uncoached stock Claude agent"
    )
    parser.add_argument("--task-ref", required=True)
    parser.add_argument("--document-id", action="append", required=True, dest="document_ids")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument("--model", default=os.getenv("WRIT_FOREIGN_AGENT_MODEL"))
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--runs-dir", type=Path, default=Path("harness/foreign_agent/runs"))
    parser.add_argument("--mcp-url", default=os.getenv("WRIT_MCP_URL"))
    parser.add_argument(
        "--mcp-command",
        default=os.getenv("WRIT_MCP_COMMAND", f"{sys.executable} -m mcp.server"),
        help="stdio server command; ignored when --mcp-url is set",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    credential = _required_env("WRIT_PIPELINE_CREDENTIAL")
    repo_root = Path(__file__).resolve().parents[2]
    database_path = str(Path(os.getenv("STATEGUARD_DB", repo_root / "stateguard.db")).resolve())
    config = HarnessConfig(
        api_base_url=_required_env("WRIT_API_BASE_URL"),
        pipeline_auth=PipelineAuth(
            credential,
            header=os.getenv("WRIT_PIPELINE_HEADER", "Authorization"),
            scheme=os.getenv("WRIT_PIPELINE_SCHEME", "Bearer"),
        ),
        task_ref=args.task_ref,
        document_ids=tuple(args.document_ids),
        mcp_url=args.mcp_url,
        mcp_command=tuple(shlex.split(args.mcp_command)),
        mcp_environment=(
            ("PYTHONPATH", str(repo_root)),
            ("STATEGUARD_DB", database_path),
        ),
        ttl_seconds=args.ttl_seconds,
        model=args.model,
        max_turns=args.max_turns,
        timeout_seconds=args.timeout_seconds,
        runs_dir=args.runs_dir,
    )
    run = ForeignAgentHarness(config).run()
    print(json.dumps(run.report, indent=2, sort_keys=True))
    print(f"full transcript: {run.transcript_path}", file=sys.stderr)
    print(f"report: {run.report_path}", file=sys.stderr)
    if run.returncode != 0:
        print(f"Claude Code exited {run.returncode}; stderr: {run.stderr_path}", file=sys.stderr)
        return run.returncode
    return 0 if run.report["week_one_gate_passed"] else 2


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


if __name__ == "__main__":
    raise SystemExit(main())
