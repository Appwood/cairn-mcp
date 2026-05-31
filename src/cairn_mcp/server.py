from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from cairn_mcp import __version__
from cairn_mcp.config import AdapterConfig, load_config
from cairn_mcp.prompts import (
    compare_before_after_deploy as compare_before_after_deploy_prompt,
    explain_job as explain_job_prompt,
    investigate_error as investigate_error_prompt,
    trace_request as trace_request_prompt,
)
from cairn_mcp.tools import LogAdapter


def create_mcp_server(
    *,
    config_path: str | None = None,
    config: AdapterConfig | None = None,
) -> FastMCP:
    adapter = LogAdapter(config or load_config(config_path))
    mcp = FastMCP("cairn")

    @mcp.tool()
    def search_logs(
        level: str | None = None,
        text: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        logger: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search read-only structured logs. Results are time-bounded, limited, and redacted.

        Pass the returned ``next_cursor`` back as ``cursor`` to page through more
        results (newest first). Free-text ``text`` is matched after redaction, so
        redacted secrets are never searchable.
        """
        return adapter.search_logs(
            level=level,
            text=text,
            start_time=start_time,
            end_time=end_time,
            service=service,
            logger=logger,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
            limit=limit,
            cursor=cursor,
        )

    @mcp.tool()
    def get_log_detail(id: str) -> dict[str, Any]:
        """Fetch one redacted log entry by ID, including traceback and attributes when available."""
        return adapter.get_log_detail(id=id)

    @mcp.tool()
    def get_correlation_logs(
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Fetch an ordered redacted sequence for one request_id, trace_id, or job_id.

        Entries are oldest-first; pass the returned ``next_cursor`` back as
        ``cursor`` to continue the timeline.
        """
        return adapter.get_correlation_logs(
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            cursor=cursor,
        )

    @mcp.tool()
    def get_log_stats(
        level: str | None = None,
        text: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        logger: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        time_bucket: str = "hour",
    ) -> dict[str, Any]:
        """Summarize matching logs by level, service, logger, and time bucket."""
        return adapter.get_log_stats(
            level=level,
            text=text,
            start_time=start_time,
            end_time=end_time,
            service=service,
            logger=logger,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
            time_bucket=time_bucket,
        )

    @mcp.prompt()
    def investigate_error(error_text: str = "", approximate_time: str = "") -> str:
        """Guide an agent through runtime error investigation from log evidence."""
        return investigate_error_prompt(error_text, approximate_time)

    @mcp.prompt()
    def trace_request(correlation_id: str = "", id_type: str = "request_id") -> str:
        """Guide an agent through reconstructing a correlated request timeline."""
        return trace_request_prompt(correlation_id, id_type)

    @mcp.prompt()
    def explain_job(job_id: str = "", approximate_time: str = "") -> str:
        """Guide an agent through explaining a background job outcome."""
        return explain_job_prompt(job_id, approximate_time)

    @mcp.prompt()
    def compare_before_after_deploy(deploy_time: str = "", window: str = "1h") -> str:
        """Guide an agent through comparing log signals around a deployment."""
        return compare_before_after_deploy_prompt(deploy_time, window)

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the cairn-mcp stdio server."
    )
    parser.add_argument("--config", help="Path to YAML configuration file.")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    create_mcp_server(config_path=args.config).run(transport="stdio")
