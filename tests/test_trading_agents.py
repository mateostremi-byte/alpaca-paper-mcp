import json
from decimal import Decimal

import pytest

from trading_agents import AgentError, StructuredModel, agent_gate, json_safe


def test_json_safe_converts_nested_values():
    assert json_safe({"x": [Decimal("1.5")]}) == {"x": ["1.5"]}


def test_output_text_rejects_incomplete_response():
    with pytest.raises(AgentError):
        StructuredModel._output_text({"status": "incomplete", "output": []})


def test_output_text_extracts_structured_payload():
    expected = {"verdict": "VETO"}
    payload = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(expected)}],
            }
        ],
    }
    assert json.loads(StructuredModel._output_text(payload)) == expected


def approved_agents(**overrides):
    payload = {
        "news": {"label": "NEUTRAL"},
        "bull": {"case": "trend"},
        "bear": {"case": "pullback"},
        "risk": {
            "verdict": "APPROVE",
            "confidence": 0.85,
            "invalidation_price": 29.0,
        },
    }
    payload.update(overrides)
    return payload


def test_agent_gate_accepts_complete_conservative_approval():
    assert agent_gate(approved_agents(), ask=Decimal("30")) == (True, "approved")


@pytest.mark.parametrize("label", ["ADVERSE", "UNKNOWN"])
def test_agent_gate_mechanically_blocks_bad_or_unknown_news(label):
    agents = approved_agents(news={"label": label})
    assert agent_gate(agents, ask=Decimal("30"))[0] is False


def test_agent_gate_blocks_low_confidence_or_invalid_stop():
    low_confidence = approved_agents(
        risk={"verdict": "APPROVE", "confidence": 0.79, "invalidation_price": 29.0}
    )
    invalid_stop = approved_agents(
        risk={"verdict": "APPROVE", "confidence": 0.90, "invalidation_price": 29.99}
    )
    assert agent_gate(low_confidence, ask=Decimal("30"))[0] is False
    assert agent_gate(invalid_stop, ask=Decimal("30"))[0] is False
