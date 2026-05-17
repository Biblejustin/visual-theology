import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from llm_client import AnthropicClient


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


class _FakeResponse:
    def __init__(self, tool_name, tool_input):
        class _Block:
            type = 'tool_use'
            input = tool_input
            name = tool_name
        class _Usage:
            input_tokens = 1
            output_tokens = 1
        self.content = [_Block()]
        self.usage = _Usage()


class _FakeMessages:
    def __init__(self):
        self.captured_kwargs = None
    def create(self, **kwargs):
        self.captured_kwargs = kwargs
        return _FakeResponse(kwargs['tools'][0]['name'], {'ok': True})


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_call_uses_custom_tool_name_when_provided():
    c = AnthropicClient(api_key='x')
    c._client = _FakeClient()
    out = c.call(prompt='hi', schema={'type': 'object'}, tool_name='emit_series_plan')
    assert out['output'] == {'ok': True}
    captured = c._client.messages.captured_kwargs
    assert captured['tools'][0]['name'] == 'emit_series_plan'
    assert captured['tool_choice'] == {'type': 'tool', 'name': 'emit_series_plan'}


def test_call_defaults_to_emit_review_when_no_tool_name():
    c = AnthropicClient(api_key='x')
    c._client = _FakeClient()
    out = c.call(prompt='hi', schema={'type': 'object'})
    assert out['output'] == {'ok': True}
    captured = c._client.messages.captured_kwargs
    assert captured['tools'][0]['name'] == 'emit_review'
