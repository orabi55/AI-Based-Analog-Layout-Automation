"""
Tests for ai_agent/llm/tool_runner.py

The LangChain LLM is mocked via monkeypatching get_langchain_llm so these
tests exercise the FC-vs-[CMD] routing logic without hitting any real API.

Coverage:
- FC path: tool_calls present → dispatched through dispatcher → updated nodes returned
- Fallback path: text-only response → fc_used=False, original nodes preserved
- Alibaba/Qwen: bind_tools is SKIPPED entirely, so even a response with
  tool_calls is treated as text-only (the dispatcher is never called)
- Schema conversion: TOOL_REGISTRY entries → OpenAI tool format dicts
- Dispatcher errors are absorbed (never raised back to the caller)
"""

import os
import sys

_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest


# ---------------------------------------------------------------------------
# Mock LLM infrastructure
# ---------------------------------------------------------------------------

class _MockResponse:
    """Stand-in for a LangChain AIMessage."""
    def __init__(self, content="", tool_calls=None, additional_kwargs=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.additional_kwargs = additional_kwargs or {}


class _MockLLM:
    """Stand-in for a LangChain BaseChatModel."""
    def __init__(self, response):
        self._response = response
        # Capture state so tests can assert on bind_tools behaviour
        self.bind_tools_called = False
        self.bound_tool_names = []
        self.last_invoke_messages = None

    def bind_tools(self, tools):
        self.bind_tools_called = True
        self.bound_tool_names = [
            (t.get("function", {}) or {}).get("name", "") for t in tools
        ]
        return self  # subsequent .invoke goes to this same mock

    def invoke(self, messages):
        self.last_invoke_messages = messages
        return self._response


@pytest.fixture
def patch_llm(monkeypatch):
    """Returns a setter that installs a mock LLM under get_langchain_llm."""
    holder = {}

    def _install(response):
        mock = _MockLLM(response)
        holder["mock"] = mock

        def fake_factory(selected_model, task_weight="light"):
            return mock
        monkeypatch.setattr(
            "ai_agent.llm.factory.get_langchain_llm",
            fake_factory,
        )
        # tool_runner imports it locally — also patch that local view
        monkeypatch.setattr(
            "ai_agent.llm.tool_runner.get_langchain_llm",
            fake_factory,
            raising=False,
        )
        return mock

    return _install, holder


@pytest.fixture
def two_nodes():
    return [
        {"id": "M1", "type": "nmos",
         "geometry": {"x": 0.0,   "y": 0.0,   "width": 0.294, "height": 0.568}},
        {"id": "M2", "type": "pmos",
         "geometry": {"x": 0.294, "y": 0.668, "width": 0.294, "height": 0.568}},
    ]


# ---------------------------------------------------------------------------
# Schema conversion
# ---------------------------------------------------------------------------

class TestSchemaConversion:
    def test_to_openai_tool_shape(self):
        from ai_agent.llm.tool_runner import _to_openai_tool
        anth = {
            "name": "move_device",
            "description": "Move a device.",
            "input_schema": {"type": "object", "properties": {"device": {"type": "string"}},
                              "required": ["device"]},
        }
        out = _to_openai_tool(anth)
        assert out["type"] == "function"
        assert out["function"]["name"] == "move_device"
        assert out["function"]["description"] == "Move a device."
        assert out["function"]["parameters"]["required"] == ["device"]

    def test_every_registry_entry_converts_cleanly(self):
        from ai_agent.llm.tool_runner import _to_openai_tool
        from ai_agent.tools.schemas import TOOL_REGISTRY
        for tool in TOOL_REGISTRY:
            converted = _to_openai_tool(tool)
            assert converted["type"] == "function"
            assert converted["function"]["name"] == tool["name"]
            # Parameters mirror input_schema
            assert converted["function"]["parameters"] is tool["input_schema"]


# ---------------------------------------------------------------------------
# FC path — tool_calls present
# ---------------------------------------------------------------------------

class TestFCPath:
    def test_fc_dispatches_move_device(self, patch_llm, two_nodes):
        install, _ = patch_llm
        response = _MockResponse(
            content="Moving M1.",
            tool_calls=[{"name": "move_device",
                         "args": {"device": "M1", "x": 1.0, "y": 0.0}}],
        )
        mock = install(response)

        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools(
            chat_messages=[{"role": "user", "content": "move M1 to (1,0)"}],
            selected_model="Gemini",
            nodes=two_nodes,
        )

        assert mock.bind_tools_called
        assert result["fc_used"] is True
        assert result["tools_bound"] is True
        assert len(result["tool_results"]) == 1
        assert result["tool_results"][0].success
        # The updated_nodes should reflect the move
        m1 = next(n for n in result["updated_nodes"] if n["id"] == "M1")
        assert abs(m1["geometry"]["x"] - 1.0) < 1e-9

    def test_fc_with_multiple_calls_threads_state(self, patch_llm, two_nodes):
        install, _ = patch_llm
        response = _MockResponse(
            content="",
            tool_calls=[
                {"name": "move_device", "args": {"device": "M1", "x": 0.5, "y": 0.0}},
                {"name": "move_device", "args": {"device": "M2", "x": 0.5, "y": 0.668}},
            ],
        )
        install(response)

        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools(
            chat_messages=[{"role": "user", "content": "move both"}],
            selected_model="VertexGemini",
            nodes=two_nodes,
        )
        assert result["fc_used"]
        assert len(result["tool_results"]) == 2
        m1 = next(n for n in result["updated_nodes"] if n["id"] == "M1")
        m2 = next(n for n in result["updated_nodes"] if n["id"] == "M2")
        assert abs(m1["geometry"]["x"] - 0.5) < 1e-9
        assert abs(m2["geometry"]["x"] - 0.5) < 1e-9

    def test_fc_synthesizes_replace_layout_command_for_chat_panel(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(
            tool_calls=[{"name": "swap_devices",
                         "args": {"device_a": "M1", "device_b": "M2"}}],
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert len(result["cmd_blocks"]) == 1
        cmd = result["cmd_blocks"][0]
        assert cmd["action"] == "replace_layout"
        assert cmd["source_actions"] == ["swap_devices"]
        assert {n["id"] for n in cmd["nodes"]} == {"M1", "M2"}
        m1 = next(n for n in cmd["nodes"] if n["id"] == "M1")
        assert abs(m1["geometry"]["x"] - two_nodes[1]["geometry"]["x"]) < 1e-9

    def test_fc_passes_terminal_nets_to_detection_tools(self, patch_llm):
        install, _ = patch_llm
        install(_MockResponse(
            tool_calls=[{"name": "detect_differential_pairs", "args": {}}],
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        diff_nodes = [
            {"id": "M1", "type": "nmos",
             "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "M2", "type": "nmos",
             "geometry": {"x": 0.294, "y": 0.0, "width": 0.294, "height": 0.568}},
        ]
        terminal_nets = {
            "M1": {"S": "TAIL", "G": "INP", "D": "OUTP"},
            "M2": {"S": "TAIL", "G": "INN", "D": "OUTN"},
        }
        result = run_llm_with_tools(
            [],
            selected_model="Gemini",
            nodes=diff_nodes,
            terminal_nets=terminal_nets,
        )
        assert result["fc_used"]
        assert result["tool_results"][0].metrics["diff_pairs"] == [["M1", "M2"]]

    def test_fc_returns_summary_when_no_text(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(
            content="",
            tool_calls=[{"name": "list_devices", "args": {}}],
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert "Tool calls executed" in result["text"]
        assert "list_devices" in result["text"]

    def test_fc_unknown_tool_does_not_raise(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(
            tool_calls=[{"name": "totally_made_up_tool", "args": {}}],
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert result["fc_used"]
        assert not result["tool_results"][0].success
        assert "Unknown tool" in result["tool_results"][0].message


# ---------------------------------------------------------------------------
# Fallback path — text-only response
# ---------------------------------------------------------------------------

class TestCMDFallback:
    def test_no_tool_calls_returns_text(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(content="Here is a [CMD]{...}[/CMD] block in text."))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert result["fc_used"] is False
        assert result["text"] == "Here is a [CMD]{...}[/CMD] block in text."
        assert result["tool_results"] == []
        assert result["cmd_blocks"] == []

    def test_fallback_preserves_original_nodes(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(content="Just chatting, no tools."))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert result["updated_nodes"] == two_nodes


# ---------------------------------------------------------------------------
# Alibaba / Qwen — bind_tools must be SKIPPED
# ---------------------------------------------------------------------------

class TestAlibabaSkipsTools:
    def test_alibaba_skips_bind_tools(self, patch_llm, two_nodes):
        install, _ = patch_llm
        # Even though the mock returns tool_calls, Alibaba is in the skip list,
        # so bind_tools must NOT be called and FC must NOT be triggered.
        mock = install(_MockResponse(
            content="[CMD]{\"action\":\"move\",\"device\":\"M1\",\"x\":1,\"y\":0}[/CMD]",
            tool_calls=[{"name": "move_device", "args": {"device": "M1", "x": 1, "y": 0}}],
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Alibaba", nodes=two_nodes)

        assert mock.bind_tools_called is False, \
            "Alibaba MUST NOT have bind_tools called on it"
        assert result["tools_bound"] is False
        assert result["fc_used"] is False, \
            "tool_calls must be ignored when tools weren't bound"
        # The text-with-CMD-block is preserved for the existing parser
        assert "[CMD]" in result["text"]

    def test_alibaba_text_response_works(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(content="Plain text reply from Qwen."))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Alibaba", nodes=two_nodes)
        assert result["fc_used"] is False
        assert result["text"] == "Plain text reply from Qwen."

    def test_provider_skip_list_contains_alibaba(self):
        from ai_agent.llm.tool_runner import PROVIDERS_WITHOUT_TOOLS
        assert "Alibaba" in PROVIDERS_WITHOUT_TOOLS


# ---------------------------------------------------------------------------
# Tool call extraction edge cases
# ---------------------------------------------------------------------------

class TestExtractToolCalls:
    def test_openai_style_function_payload(self, patch_llm, two_nodes):
        """OpenAI delivers args as a JSON-encoded string under .function.arguments."""
        install, _ = patch_llm
        install(_MockResponse(
            content="",
            additional_kwargs={
                "tool_calls": [
                    {"function": {"name": "move_device",
                                  "arguments": '{"device":"M1","x":1.5,"y":0.0}'}}
                ]
            },
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert result["fc_used"]
        m1 = next(n for n in result["updated_nodes"] if n["id"] == "M1")
        assert abs(m1["geometry"]["x"] - 1.5) < 1e-9

    def test_anthropic_content_block_text_extracted(self, patch_llm, two_nodes):
        install, _ = patch_llm
        install(_MockResponse(
            content=[{"type": "text", "text": "I moved it."}],
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="VertexClaude", nodes=two_nodes)
        assert result["text"] == "I moved it."

    def test_malformed_args_string_safe(self, patch_llm, two_nodes):
        """If args string is unparseable, tool gets empty args and is dispatched anyway."""
        install, _ = patch_llm
        install(_MockResponse(
            additional_kwargs={"tool_calls": [
                {"function": {"name": "list_devices", "arguments": "this is not json"}}
            ]},
        ))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        # Should not raise — empty args dispatched to list_devices (no required args)
        assert result["fc_used"]
        assert result["tool_results"][0].success


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_invoke_exception_returns_error_text(self, monkeypatch, two_nodes):
        class _BoomLLM:
            def bind_tools(self, tools):
                return self
            def invoke(self, messages):
                raise RuntimeError("API meltdown")

        def fake_factory(selected_model, task_weight="light"):
            return _BoomLLM()

        monkeypatch.setattr("ai_agent.llm.tool_runner.get_langchain_llm",
                             fake_factory, raising=False)
        monkeypatch.setattr("ai_agent.llm.factory.get_langchain_llm", fake_factory)

        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        assert result["fc_used"] is False
        assert "API meltdown" in result["text"]
        assert result["updated_nodes"] == two_nodes  # original nodes preserved

    def test_bind_tools_failure_falls_back_gracefully(self, monkeypatch, two_nodes):
        class _NoBindLLM:
            def bind_tools(self, tools):
                raise NotImplementedError("provider doesn't bind")
            def invoke(self, messages):
                return _MockResponse(content="Plain text only.")

        def fake_factory(selected_model, task_weight="light"):
            return _NoBindLLM()

        monkeypatch.setattr("ai_agent.llm.tool_runner.get_langchain_llm",
                             fake_factory, raising=False)
        monkeypatch.setattr("ai_agent.llm.factory.get_langchain_llm", fake_factory)

        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=two_nodes)
        # Should fall back to text-only
        assert result["tools_bound"] is False
        assert result["fc_used"] is False
        assert result["text"] == "Plain text only."

    def test_empty_nodes_safe(self, patch_llm):
        install, _ = patch_llm
        install(_MockResponse(content="hi"))
        from ai_agent.llm.tool_runner import run_llm_with_tools
        result = run_llm_with_tools([], selected_model="Gemini", nodes=[])
        assert result["fc_used"] is False
        assert result["updated_nodes"] == []


# ---------------------------------------------------------------------------
# Multi-turn Function Calling tests
# ---------------------------------------------------------------------------

class TestMultiTurnFunctionCalling:
    def test_multi_turn_feeds_tool_results_back(self, monkeypatch, two_nodes):
        from langchain_core.messages import AIMessage, ToolMessage
        from ai_agent.llm.tool_runner import run_llm_with_tools

        first_resp = _MockResponse(
            tool_calls=[{"name": "detect_circuit_type", "args": {}, "id": "call_123"}],
        )
        second_resp = _MockResponse(
            content="This is a latch circuit."
        )

        mock_llm = _MockMultiTurnLLM(first_resp, second_resp)

        def fake_factory(selected_model, task_weight="light"):
            return mock_llm

        monkeypatch.setattr("ai_agent.llm.tool_runner.get_langchain_llm", fake_factory, raising=False)
        monkeypatch.setattr("ai_agent.llm.factory.get_langchain_llm", fake_factory)

        result = run_llm_with_tools(
            [{"role": "user", "content": "Explain this circuit"}],
            selected_model="Gemini",
            nodes=two_nodes,
        )

        assert result["fc_used"]
        assert result["text"] == "This is a latch circuit."
        assert len(mock_llm.invocations) == 2

        # Verify second invocation messages structure
        second_msgs = mock_llm.invocations[1]
        assert len(second_msgs) == 3  # user + assistant (tool_call) + tool (result)
        assert isinstance(second_msgs[1], _MockResponse)  # original AIMessage
        assert isinstance(second_msgs[2], ToolMessage)
        assert second_msgs[2].tool_call_id == "call_123"
        # Since the tool result returned "Circuit type: generic" from mock, check that
        assert "generic" in second_msgs[2].content.lower()



class _MockMultiTurnLLM:
    def __init__(self, first_resp, second_resp):
        self.first_resp = first_resp
        self.second_resp = second_resp
        self.invocations = []
        self.bind_tools_called = False

    def bind_tools(self, tools):
        self.bind_tools_called = True
        return self

    def invoke(self, messages):
        self.invocations.append(messages)
        if len(self.invocations) == 1:
            return self.first_resp
        return self.second_resp

