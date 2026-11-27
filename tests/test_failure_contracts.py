import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage
from report_clerk.analysis import agent as claude
from report_clerk.config import ClaudeConfig, Settings
from report_clerk.services import DataSources

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('outcome', ['success', 'budget', 'empty', 'oversized'])
def test_analysis_results_have_explicit_success_or_failure(monkeypatch, outcome):
    @contextmanager
    def options(*args): yield object()
    async def query(**kwargs):
        if outcome == 'empty': return
        yield ResultMessage(subtype='error_max_budget_usd' if outcome == 'budget' else 'success',
                            duration_ms=1, duration_api_ms=1, is_error=outcome == 'budget',
                            num_turns=1, session_id='test', result='x' * 20_001 if outcome == 'oversized' else 'analysis')
    monkeypatch.setattr(claude, 'agent_options', options)
    monkeypatch.setattr(claude, 'query', query)
    call = claude._analyse(ClaudeConfig(), DataSources(Settings(), ROOT / 'mock'), 'request', 'title', '{}', [])
    if outcome == 'success': assert asyncio.run(call) == 'analysis'
    else:
        with pytest.raises((RuntimeError, ValueError)): asyncio.run(call)


def test_analysis_oversized_input_rejected_before_sdk():
    with pytest.raises(ValueError, match='input limit'):
        asyncio.run(claude._analyse(ClaudeConfig(), DataSources(Settings()), '', '', 'x' * 200_001, []))
