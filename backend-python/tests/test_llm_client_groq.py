"""
Unit tests for the Groq provider in app/LLM/llm_client.py (call_groq,
call_groq_stream, and the get_docker_llm_provider/call_docker_llm* routing
that now recognizes "groq" as a third valid provider alongside "ollama" and
"gemini").

These tests are fully mocked: `requests.post` is patched in every test, so
NO real HTTP call is ever made to Groq's API. As of this session there is no
real GROQ_API_KEY configured, so a live call would fail anyway - these tests
exist specifically so Groq support can be verified without one.

Mirrors the structure/conventions of test_llm_client.py:
- llm_client.py reads settings.* ONCE at import time into module-level
  constants (GROQ_API_KEY, GROQ_API_BASE, GROQ_MODEL_NAME, ...). The
  functions under test reference those module-level names directly, so
  tests patch `app.LLM.llm_client.<CONSTANT>` rather than
  `app.config.settings.settings.<CONSTANT>`.
- `requests.post` is patched as `app.LLM.llm_client.requests.post` since
  `_request_with_retry` calls `requests.post` directly (not
  `requests.request`), matching how call_llama/call_gemini are tested.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from app.LLM import llm_client


class FakeResponse:
    """Minimal stand-in for requests.Response used across these tests."""

    def __init__(self, status_code=200, json_data=None, text="", iter_lines_data=None,
                 raise_http_error=None, reason="OK"):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self.text = text
        self._iter_lines_data = iter_lines_data or []
        self._raise_http_error = raise_http_error
        self.reason = reason

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self._raise_http_error is not None:
            raise self._raise_http_error

    def iter_lines(self):
        return iter(self._iter_lines_data)


def _groq_success_payload(content="Hello from Groq", finish_reason="stop"):
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }


class TestCallGroq(unittest.TestCase):
    """call_groq (Groq OpenAI-compatible chat completions, non-streaming)."""

    @patch("app.LLM.llm_client.requests.post")
    def test_missing_api_key_short_circuits_without_network_call(self, mock_post):
        with patch("app.LLM.llm_client.GROQ_API_KEY", None):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: GROQ_API_KEY is not set"))
        mock_post.assert_not_called()

    @patch("app.LLM.llm_client.requests.post")
    def test_successful_response_parsing(self, mock_post):
        mock_post.return_value = FakeResponse(
            status_code=200, json_data=_groq_success_payload("Hello from Groq")
        )

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GROQ_MODEL_NAME", "llama-3.3-70b-versatile"), \
             patch("app.LLM.llm_client.GROQ_API_BASE", "https://api.groq.com/openai/v1"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "Hello from Groq")
        mock_post.assert_called_once()

        called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        self.assertEqual(called_url, "https://api.groq.com/openai/v1/chat/completions")

        sent_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_json["model"], "llama-3.3-70b-versatile")
        self.assertEqual(sent_json["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(sent_json["stream"], False)

        sent_headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(sent_headers["Authorization"], "Bearer test-key")

    @patch("app.LLM.llm_client.requests.post")
    def test_messages_passed_through_unchanged_no_splitting(self, mock_post):
        # Unlike Gemini (which needs system/user split into separate fields),
        # Groq is OpenAI-shaped and should receive the same messages list
        # this codebase already passes around internally, verbatim.
        mock_post.return_value = FakeResponse(status_code=200, json_data=_groq_success_payload("ok"))
        messages = [
            {"role": "system", "content": "You are a Docker expert."},
            {"role": "user", "content": "Generate a Dockerfile."},
        ]

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            llm_client.call_groq(messages)

        sent_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_json["messages"], messages)

    @patch("app.LLM.llm_client.requests.post")
    def test_custom_options_override_defaults(self, mock_post):
        mock_post.return_value = FakeResponse(status_code=200, json_data=_groq_success_payload("ok"))

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            llm_client.call_groq(
                [{"role": "user", "content": "hi"}],
                custom_options={"temperature": 0.0, "max_tokens": 2048},
            )

        sent_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_json["temperature"], 0.0)
        self.assertEqual(sent_json["max_tokens"], 2048)

    @patch("app.LLM.llm_client.requests.post")
    def test_empty_choices_returns_error_string(self, mock_post):
        mock_post.return_value = FakeResponse(status_code=200, json_data={"choices": []})

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Groq returned no choices"))

    @patch("app.LLM.llm_client.requests.post")
    def test_empty_content_returns_error_string(self, mock_post):
        mock_post.return_value = FakeResponse(
            status_code=200, json_data=_groq_success_payload("", finish_reason="length")
        )

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Groq returned an empty response"))
        self.assertIn("length", result)

    @patch("app.LLM.llm_client.time.sleep", return_value=None)
    @patch("app.LLM.llm_client.requests.post")
    def test_connection_error_retries_then_succeeds(self, mock_post, mock_sleep):
        # _request_with_retry gives transient connection failures a short
        # retry (up to 3 attempts); the 3rd attempt here succeeds.
        mock_post.side_effect = [
            requests.exceptions.ConnectionError("blip"),
            requests.exceptions.ConnectionError("blip again"),
            FakeResponse(status_code=200, json_data=_groq_success_payload("recovered")),
        ]

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "recovered")
        self.assertEqual(mock_post.call_count, 3)

    @patch("app.LLM.llm_client.time.sleep", return_value=None)
    @patch("app.LLM.llm_client.requests.post")
    def test_connection_error_returns_error_string_after_exhausting_retries(self, mock_post, mock_sleep):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GROQ_API_BASE", "https://api.groq.com/openai/v1"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Cannot connect to Groq API"))
        self.assertIn("https://api.groq.com/openai/v1", result)
        # All 3 retry attempts were exhausted before giving up.
        self.assertEqual(mock_post.call_count, 3)

    @patch("app.LLM.llm_client.time.sleep", return_value=None)
    @patch("app.LLM.llm_client.requests.post")
    def test_timeout_returns_error_string(self, mock_post, mock_sleep):
        # Timeout is also retried by _request_with_retry (1s/2s backoff);
        # time.sleep is patched so the retry loop runs instantly.
        mock_post.side_effect = requests.exceptions.Timeout()

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertEqual(
            result,
            f"ERROR: Groq request timed out after {llm_client.LLM_TIMEOUT} seconds.",
        )

    @patch("app.LLM.llm_client.requests.post")
    def test_http_error_returns_error_string(self, mock_post):
        http_err = requests.exceptions.HTTPError()
        http_err.response = MagicMock(status_code=401, reason="Unauthorized")
        mock_post.return_value = FakeResponse(status_code=401, raise_http_error=http_err)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "bad-key"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Groq HTTP 401 - Unauthorized"))

    @patch("app.LLM.llm_client.requests.post")
    def test_generic_exception_redacts_bearer_token(self, mock_post):
        # Defensive check: if the Authorization header value ever ended up
        # embedded in an exception message, it must be redacted before it's
        # returned. requests itself doesn't normally do this, so this test
        # simulates a hypothetical leak to prove the redaction path works.
        mock_post.side_effect = RuntimeError("boom while sending Authorization: Bearer sk-super-secret-value")

        with patch("app.LLM.llm_client.GROQ_API_KEY", "sk-super-secret-value"):
            result = llm_client.call_groq([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Groq call failed"))
        self.assertNotIn("sk-super-secret-value", result)
        self.assertIn("***REDACTED***", result)


class TestCallGroqStream(unittest.TestCase):
    """call_groq_stream (Groq SSE streaming)."""

    def _sse_lines(self, *lines):
        return [line.encode("utf-8") if isinstance(line, str) else line for line in lines]

    @patch("app.LLM.llm_client.requests.post")
    def test_missing_api_key_yields_single_error_chunk_without_network_call(self, mock_post):
        with patch("app.LLM.llm_client.GROQ_API_KEY", None):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["done"])
        self.assertTrue(chunks[0]["error"])
        self.assertTrue(chunks[0]["token"].startswith("ERROR: GROQ_API_KEY is not set"))
        mock_post.assert_not_called()

    @patch("app.LLM.llm_client.requests.post")
    def test_streams_multiple_chunks_and_stops_on_done(self, mock_post):
        lines = self._sse_lines(
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
        )
        mock_post.return_value = FakeResponse(status_code=200, iter_lines_data=lines)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(
            chunks,
            [
                {"token": "Hel", "done": False},
                {"token": "lo", "done": False},
                {"token": "", "done": True},
            ],
        )
        sent_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_json["stream"], True)
        self.assertTrue(mock_post.call_args.kwargs["stream"])

    @patch("app.LLM.llm_client.requests.post")
    def test_stops_on_finish_reason_without_explicit_done(self, mock_post):
        # Some Groq responses may end the stream via finish_reason without a
        # trailing [DONE] line; the generator must still terminate cleanly.
        lines = self._sse_lines(
            'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        )
        mock_post.return_value = FakeResponse(status_code=200, iter_lines_data=lines)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(
            chunks,
            [
                {"token": "Hi", "done": False},
                {"token": "", "done": True},
            ],
        )

    @patch("app.LLM.llm_client.requests.post")
    def test_skips_blank_lines_and_non_data_lines(self, mock_post):
        lines = self._sse_lines(
            "",
            ": keep-alive comment",
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        )
        mock_post.return_value = FakeResponse(status_code=200, iter_lines_data=lines)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(
            chunks,
            [
                {"token": "ok", "done": False},
                {"token": "", "done": True},
            ],
        )

    @patch("app.LLM.llm_client.requests.post")
    def test_malformed_json_chunk_is_skipped(self, mock_post):
        lines = self._sse_lines(
            "data: {not valid json",
            'data: {"choices":[{"delta":{"content":"still works"}}]}',
            "data: [DONE]",
        )
        mock_post.return_value = FakeResponse(status_code=200, iter_lines_data=lines)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(
            chunks,
            [
                {"token": "still works", "done": False},
                {"token": "", "done": True},
            ],
        )

    @patch("app.LLM.llm_client.time.sleep", return_value=None)
    @patch("app.LLM.llm_client.requests.post")
    def test_connection_error_yields_single_error_chunk(self, mock_post, mock_sleep):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["done"])
        self.assertTrue(chunks[0]["error"])
        self.assertTrue(chunks[0]["token"].startswith("ERROR: Cannot connect to Groq API"))

    @patch("app.LLM.llm_client.time.sleep", return_value=None)
    @patch("app.LLM.llm_client.requests.post")
    def test_timeout_yields_single_error_chunk(self, mock_post, mock_sleep):
        mock_post.side_effect = requests.exceptions.Timeout()

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["error"])
        self.assertIn("timed out", chunks[0]["token"])

    @patch("app.LLM.llm_client.requests.post")
    def test_http_error_yields_single_error_chunk(self, mock_post):
        http_err = requests.exceptions.HTTPError()
        http_err.response = MagicMock(status_code=429, reason="Too Many Requests")
        mock_post.return_value = FakeResponse(status_code=429, raise_http_error=http_err)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["error"])
        self.assertTrue(chunks[0]["done"])
        self.assertIn("429", chunks[0]["token"])

    @patch("app.LLM.llm_client.requests.post")
    def test_error_mid_stream_after_some_tokens_still_yields_error_chunk(self, mock_post):
        # If the connection drops partway through iter_lines(), requests
        # itself would raise from within the generator; simulate that by
        # having iter_lines raise after a couple of lines were consumed.
        class BrokenIterLinesResponse(FakeResponse):
            def iter_lines(self):
                yield b'data: {"choices":[{"delta":{"content":"partial"}}]}'
                raise requests.exceptions.ConnectionError("dropped mid-stream")

        mock_post.return_value = BrokenIterLinesResponse(status_code=200)

        with patch("app.LLM.llm_client.GROQ_API_KEY", "test-key"):
            chunks = list(llm_client.call_groq_stream([{"role": "user", "content": "hi"}]))

        # The partial token is yielded before the error terminates the stream.
        self.assertEqual(chunks[0], {"token": "partial", "done": False})
        self.assertTrue(chunks[-1]["error"])
        self.assertTrue(chunks[-1]["done"])
        self.assertTrue(chunks[-1]["token"].startswith("ERROR: Cannot connect to Groq API"))


class TestGetDockerLlmProviderGroq(unittest.TestCase):
    """get_docker_llm_provider() now accepts "groq" as a third valid value."""

    def test_groq_is_a_recognized_provider(self):
        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "groq"):
            self.assertEqual(llm_client.get_docker_llm_provider(), "groq")

    def test_groq_is_case_insensitive(self):
        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "GrOq"):
            self.assertEqual(llm_client.get_docker_llm_provider(), "groq")

    def test_unrecognized_provider_still_defaults_to_ollama(self):
        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "not-a-real-provider"):
            self.assertEqual(llm_client.get_docker_llm_provider(), "ollama")


class TestCallDockerLlmRoutingGroq(unittest.TestCase):
    """call_docker_llm/call_docker_llm_stream route to call_groq/call_groq_stream."""

    @patch("app.LLM.llm_client.call_gemini")
    @patch("app.LLM.llm_client.call_llama")
    @patch("app.LLM.llm_client.call_groq")
    def test_routes_to_groq_when_provider_is_groq(self, mock_groq, mock_llama, mock_gemini):
        mock_groq.return_value = "groq response"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "groq"):
            result = llm_client.call_docker_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "groq response")
        mock_groq.assert_called_once()
        mock_llama.assert_not_called()
        mock_gemini.assert_not_called()

    @patch("app.LLM.llm_client.call_gemini")
    @patch("app.LLM.llm_client.call_llama")
    @patch("app.LLM.llm_client.call_groq")
    def test_routes_to_groq_case_insensitively(self, mock_groq, mock_llama, mock_gemini):
        mock_groq.return_value = "groq response"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "GROQ"):
            result = llm_client.call_docker_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "groq response")
        mock_groq.assert_called_once()

    @patch("app.LLM.llm_client.call_gemini_stream")
    @patch("app.LLM.llm_client.call_llama_stream")
    @patch("app.LLM.llm_client.call_groq_stream")
    def test_stream_routes_to_groq_stream_when_provider_is_groq(self, mock_groq_stream, mock_llama_stream, mock_gemini_stream):
        mock_groq_stream.return_value = iter([{"token": "hi", "done": True}])

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "groq"):
            chunks = list(llm_client.call_docker_llm_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, [{"token": "hi", "done": True}])
        mock_groq_stream.assert_called_once()
        mock_llama_stream.assert_not_called()
        mock_gemini_stream.assert_not_called()

    @patch("app.LLM.llm_client.call_groq")
    def test_custom_options_forwarded_to_groq(self, mock_groq):
        mock_groq.return_value = "ok"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "groq"):
            llm_client.call_docker_llm(
                [{"role": "user", "content": "hi"}],
                custom_options={"temperature": 0.0},
            )

        mock_groq.assert_called_once_with(
            [{"role": "user", "content": "hi"}], custom_options={"temperature": 0.0}
        )


if __name__ == "__main__":
    unittest.main()
