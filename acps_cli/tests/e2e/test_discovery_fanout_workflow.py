"""端到端占位：discovery multi-forwarder fanout 工作流。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_multi_forwarder_fanout_requires_runtime_support() -> None:
    """为 fanout 运行时留出明确占位，避免误判为已覆盖。"""
    pytest.skip("discovery-server 当前仅支持 single-forwarder runtime，待实现多下游 fanout 聚合后启用此 e2e")
