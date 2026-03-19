"""대화 컨텍스트 테스트."""

from mantis.context.conversation import ConversationContext, Message


def test_message_to_dict():
    msg = Message(role="user", content="안녕")
    d = msg.to_dict()
    assert d == {"role": "user", "content": "안녕"}


def test_message_to_dict_tool():
    msg = Message(role="tool", content='{"ok": true}', tool_call_id="tc_1", name="echo")
    d = msg.to_dict()
    assert d["role"] == "tool"
    assert d["tool_call_id"] == "tc_1"
    assert d["name"] == "echo"


def test_context_empty():
    ctx = ConversationContext()
    assert len(ctx) == 0
    assert ctx.to_messages() == []


def test_context_with_system_prompt():
    ctx = ConversationContext(system_prompt="너는 도우미야")
    msgs = ctx.to_messages()
    assert len(msgs) == 1
    assert msgs[0] == {"role": "system", "content": "너는 도우미야"}


def test_context_add_messages():
    ctx = ConversationContext(system_prompt="시스템")
    ctx.add_user("안녕하세요")
    ctx.add_assistant(content="안녕하세요!")

    msgs = ctx.to_messages()
    assert len(msgs) == 3
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"


def test_context_add_tool_result():
    ctx = ConversationContext()
    ctx.add_user("날씨 알려줘")
    ctx.add_assistant(
        content=None,
        tool_calls=[{"id": "tc_1", "type": "function", "function": {"name": "weather"}}],
    )
    ctx.add_tool_result(tool_call_id="tc_1", name="weather", content='{"temp": 20}')

    msgs = ctx.to_messages()
    assert len(msgs) == 3
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "tc_1"


def test_context_clear():
    ctx = ConversationContext()
    ctx.add_user("테스트")
    ctx.add_assistant(content="응답")
    assert len(ctx) == 2
    ctx.clear()
    assert len(ctx) == 0


def test_context_system_prompt_setter():
    ctx = ConversationContext(system_prompt="원본")
    assert ctx.system_prompt == "원본"
    ctx.system_prompt = "변경됨"
    msgs = ctx.to_messages()
    assert msgs[0]["content"] == "변경됨"
