"""Run the real OpenAI-compatible live evaluation profile locally.

Credentials and endpoint settings are read from the process environment only.
The script never writes them to disk or includes them in output.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "packages" / "nl2data-openai" / "src"))

# Imports intentionally follow runtime path setup for a source checkout.
# isort: off
from nl2data_core.ai.context import SemanticReference  # noqa: E402
from nl2data_core.ai.config import ModelConfig  # noqa: E402
from nl2data_core.ai.evaluation.cases import build_ai_dataset  # noqa: E402
from nl2data_core.planning.validation import AuthorizedView  # noqa: E402
from nl2data_openai.config import OpenAIProviderConfig  # noqa: E402
from nl2data_openai.live_evaluation import run_live_openai_evaluation  # noqa: E402
# isort: on


FINGERPRINT = "sha256:" + "a" * 64

VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order"}),
    field_ids=frozenset({"order_id", "amount", "status", "created_at"}),
    catalog_fingerprint=FINGERPRINT,
)

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg"}),
    ),
    "status": SemanticReference(field_id="status", label="Order status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
}


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def timeout_seconds() -> float:
    value = os.environ.get("OPENAI_TIMEOUT_SECONDS", "60")
    try:
        timeout = float(value)
    except ValueError as error:
        raise SystemExit("OPENAI_TIMEOUT_SECONDS must be a positive number") from error
    if timeout <= 0 or timeout > 3600:
        raise SystemExit("OPENAI_TIMEOUT_SECONDS must be between 0 and 3600")
    return timeout


def selected_cases(dataset):
    requested = os.environ.get("OPENAI_LIVE_CASES", "normal-intent")
    case_ids = tuple(case_id.strip() for case_id in requested.split(",") if case_id.strip())
    selected = tuple(case for case in dataset.cases if case.case_id in case_ids)
    if not selected:
        raise SystemExit(f"no matching cases for OPENAI_LIVE_CASES={requested!r}")
    return dataset.model_copy(update={"cases": selected})


def report_progress(event: str, case_id: str, result) -> None:
    if event == "start":
        print(f"case={case_id} status=starting", flush=True)
        return
    details = result.error.details if result.error is not None else {}
    code = result.error.code.value if result.error is not None else "none"
    print(
        f"case={case_id} status={result.availability.value} code={code} "
        f"status_code={details.get('status_code', details.get('last_status_code', 'none'))} "
        f"cause_type={details.get('cause_type', details.get('last_cause_type', 'none'))}",
        flush=True,
    )


async def main() -> int:
    api_key = required_environment("OPENAI_API_KEY")
    base_url = required_environment("OPENAI_BASE_URL")
    model_name = required_environment("OPENAI_MODEL")
    timeout = timeout_seconds()
    dataset = selected_cases(build_ai_dataset())

    print(
        f"starting live AI evaluation: provider=openai model={model_name} "
        f"credentials=provided endpoint=provided cases={len(dataset.cases)} "
        f"timeout={timeout:g}s",
        flush=True,
    )
    print(
        "provider timeout and case selection are controlled by environment variables",
        flush=True,
    )

    report = await run_live_openai_evaluation(
        dataset=dataset,
        run_id="local-openai-live",
        view=VIEW,
        provider_config=OpenAIProviderConfig(
            model_name=model_name,
            base_url=base_url,
            merge_developer_into_system=True,
            timeout_seconds=timeout,
        ),
        semantic_references=REFERENCES,
        api_key_resolver=lambda: api_key,
        model_config=ModelConfig(timeout_seconds=timeout),
        progress_callback=report_progress,
    )

    print(
        f"provider={report.provider_name} model={report.model_name} "
        f"verified={report.verified_count} "
        f"unavailable={report.unavailable_count} "
        f"skipped={report.skipped_count}"
    )
    return 0 if report.verified_count == len(report.results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
