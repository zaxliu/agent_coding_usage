from llm_usage.collectors import build_claude_collector, build_opencode_collector


def test_claude_tool_name_is_claude_code():
    collector = build_claude_collector()
    assert collector.name == "claude_code"


def test_opencode_tool_name():
    collector = build_opencode_collector()
    assert collector.name == "opencode"
