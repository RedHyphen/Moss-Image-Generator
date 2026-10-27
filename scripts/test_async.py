#!/usr/bin/env python3
"""Run pytest-asyncio tests when the plugin is unavailable.

This intentionally invokes only the async-marked tests with their lightweight
pytest monkeypatch and temporary-path fixtures. It is a fallback for minimal
environments; the normal `pytest -q` run remains authoritative when
pytest-asyncio is installed.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.tests import test_api, test_inference, test_storage  # noqa: E402


ASYNC_TESTS = (
    (test_api.test_authenticated_api_flow_and_protected_routes, True, False),
    (test_api.test_public_client_is_rejected_by_network_guard, False, False),
    (test_api.test_ipv4_mapped_ipv6_client_matches_trusted_ipv4_subnet, False, False),
    (test_inference.test_vllm_image_contract, True, True),
    (test_inference.test_vllm_layout_payload_is_compiled_at_inference_boundary, True, True),
    (test_inference.test_optional_local_router_contract, True, False),
    (test_inference.test_vllm_health_requires_health_and_model_discovery, True, False),
    (test_storage.test_upload_is_normalized_to_png, False, False),
    (test_storage.test_upload_rejects_oversize_file, False, False),
    (test_storage.test_upload_rejects_oversize_dimensions_before_decode, False, False),
)


async def run_test(function, needs_monkeypatch: bool, needs_tmp_path: bool) -> None:
    monkeypatch = pytest.MonkeyPatch() if needs_monkeypatch else None
    temporary = tempfile.TemporaryDirectory(prefix="moss-async-test-") if needs_tmp_path else None
    kwargs = {}
    if monkeypatch is not None:
        kwargs["monkeypatch"] = monkeypatch
    if temporary is not None:
        kwargs["tmp_path"] = Path(temporary.name)
    try:
        result = function(**kwargs)
        if inspect.isawaitable(result):
            await result
    finally:
        if monkeypatch is not None:
            monkeypatch.undo()
        if temporary is not None:
            temporary.cleanup()


async def main() -> None:
    for function, needs_monkeypatch, needs_tmp_path in ASYNC_TESTS:
        await run_test(function, needs_monkeypatch, needs_tmp_path)
        print(f"passed {function.__module__}.{function.__name__}")
    print(f"direct_async_tests={len(ASYNC_TESTS)} passed")


if __name__ == "__main__":
    asyncio.run(main())
