"""ModelClient 파싱 테스트 (네트워크 호출 없이)."""

from mantis.llm.openai_provider import ModelClient, ModelResponse, ToolCall


def test_parse_response_text_only():
    """텍스트만 있는 응답 파싱."""
    client = ModelClient(base_url="http://fake", model="test")
    data = {
        "choices": [
            {"message": {"content": "안녕하세요!", "role": "assistant"}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    resp = client._parse_response(data)
    assert resp.text == "안녕하세요!"
    assert resp.has_tool_calls is False
    assert resp.usage["prompt_tokens"] == 10


def test_parse_response_with_tool_calls():
    """도구 호출이 포함된 응답 파싱."""
    client = ModelClient(base_url="http://fake", model="test")
    data = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "weather",
                                "arguments": '{"city": "서울"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {},
    }
    resp = client._parse_response(data)
    assert resp.has_tool_calls is True
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "weather"
    assert resp.tool_calls[0].arguments == {"city": "서울"}
    assert resp.tool_calls[0].id == "call_123"


def test_parse_response_multiple_tool_calls():
    """다중 도구 호출 파싱."""
    client = ModelClient(base_url="http://fake", model="test")
    data = {
        "choices": [
            {
                "message": {
                    "content": "확인해볼게요",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {"name": "tool_a", "arguments": "{}"},
                        },
                        {
                            "id": "tc_2",
                            "type": "function",
                            "function": {"name": "tool_b", "arguments": '{"x": 1}'},
                        },
                    ],
                }
            }
        ],
        "usage": {},
    }
    resp = client._parse_response(data)
    assert resp.text == "확인해볼게요"
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].name == "tool_a"
    assert resp.tool_calls[1].name == "tool_b"
    assert resp.tool_calls[1].arguments == {"x": 1}


def test_parse_response_malformed_arguments():
    """잘못된 JSON arguments 처리."""
    client = ModelClient(base_url="http://fake", model="test")
    data = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {"name": "broken", "arguments": "not json"},
                        }
                    ],
                }
            }
        ],
        "usage": {},
    }
    resp = client._parse_response(data)
    assert resp.tool_calls[0].arguments == {}


def test_model_response_properties():
    """ModelResponse 프로퍼티."""
    resp = ModelResponse(text="hello", tool_calls=[])
    assert resp.has_tool_calls is False

    resp2 = ModelResponse(
        text=None,
        tool_calls=[ToolCall(id="1", name="test", arguments={})],
    )
    assert resp2.has_tool_calls is True
