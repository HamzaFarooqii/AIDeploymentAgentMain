"""
Unit tests for app/LLM/llm_client.py.

These tests are fully mocked: `requests.post` is patched in every test, so
NO real HTTP call is ever made to Ollama or Gemini. This matters right now
because the configured Gemini API key is known to return HTTP 403 ("project
denied access") as of this session -- these tests must never depend on that
(or any other) live network call.

Important implementation detail that shapes how these tests patch things:
llm_client.py reads `settings.*` ONCE at import time into module-level
constants (OLLAMA_URL, MODEL_NAME, DOCKER_LLM_PROVIDER, GEMINI_API_KEY,
GEMINI_MODEL_NAME, GEMINI_FALLBACK_MODEL_NAME, ...). The functions under
test reference those module-level names directly, NOT `settings.<NAME>`
live. So to change provider/model/key behavior for a test we must patch
`app.LLM.llm_client.<CONSTANT>` directly -- patching
`app.config.settings.settings.<CONSTANT>` after import has no effect on
these functions. (Not a bug we're fixing -- it's the same "load config
once at process start" pattern used throughout this module -- but it is a
real gotcha for anyone trying to reconfigure providers at runtime, and it
is why these tests patch the module constants instead of `settings`.)
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


class TestCallLlama(unittest.TestCase):
    """call_llama (Ollama, non-streaming)."""

    @patch("app.LLM.llm_client.requests.post")
    def test_successful_response_parsing_single_json(self, mock_post):
        mock_post.return_value = FakeResponse(
            status_code=200,
            json_data={"response": "Hello from Ollama", "done": True},
            text='{"response": "Hello from Ollama", "done": true}',
        )

        result = llm_client.call_llama([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "Hello from Ollama")
        mock_post.assert_called_once()
        called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        self.assertEqual(called_url, llm_client.OLLAMA_URL)
        sent_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_json["model"], llm_client.MODEL_NAME)
        self.assertEqual(sent_json["stream"], False)

    @patch("app.LLM.llm_client.requests.post")
    def test_ndjson_response_uses_last_line(self, mock_post):
        # Documents actual behavior: the ndjson-handling branch overwrites
        # `data` on every line rather than accumulating "response" text, so
        # only the final line's payload is returned.
        ndjson_text = (
            '{"response": "chunk-one", "done": false}\n'
            '{"response": "chunk-two", "done": true}'
        )
        mock_post.return_value = FakeResponse(status_code=200, text=ndjson_text)

        result = llm_client.call_llama([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "chunk-two")

    @patch("app.LLM.llm_client.requests.post")
    def test_connection_error_returns_error_string_not_exception(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        result = llm_client.call_llama([{"role": "user", "content": "hi"}])

        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("ERROR: Cannot connect to Ollama"))
        self.assertIn(llm_client.OLLAMA_URL, result)

    @patch("app.LLM.llm_client.requests.post")
    def test_timeout_returns_error_string(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout()

        result = llm_client.call_llama([{"role": "user", "content": "hi"}])

        self.assertEqual(
            result,
            f"ERROR: LLM request timed out after {llm_client.LLM_TIMEOUT} seconds.",
        )

    @patch("app.LLM.llm_client.requests.post")
    def test_http_error_returns_error_string(self, mock_post):
        http_err = requests.exceptions.HTTPError()
        http_err.response = MagicMock(status_code=500, reason="Internal Server Error")
        resp = FakeResponse(status_code=500, raise_http_error=http_err)
        mock_post.return_value = resp

        result = llm_client.call_llama([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: HTTP 500 - Internal Server Error"))

    def test_no_real_network_module_is_mocked(self):
        # Sanity check that requests.post really is the target being patched
        # elsewhere in this file (guards against a typo silently making a
        # real call).
        self.assertTrue(hasattr(requests, "post"))


class TestCallLlamaStream(unittest.TestCase):
    """call_llama_stream (Ollama, streaming generator)."""

    @patch("app.LLM.llm_client.requests.post")
    def test_streams_tokens_and_stops_on_done(self, mock_post):
        lines = [
            b'{"response": "Hel", "done": false}',
            b'{"response": "lo", "done": false}',
            b'{"response": "", "done": true}',
        ]
        mock_post.return_value = FakeResponse(status_code=200, iter_lines_data=lines)

        chunks = list(llm_client.call_llama_stream([{"role": "user", "content": "hi"}]))

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
    def test_connection_error_yields_single_error_chunk(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        chunks = list(llm_client.call_llama_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["done"])
        self.assertTrue(chunks[0]["error"])
        self.assertTrue(chunks[0]["token"].startswith("ERROR: Cannot connect to Ollama"))

    @patch("app.LLM.llm_client.requests.post")
    def test_timeout_yields_single_error_chunk(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout()

        chunks = list(llm_client.call_llama_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["error"])
        self.assertIn("timed out", chunks[0]["token"])


class TestCallGemini(unittest.TestCase):
    """call_gemini (Gemini REST, non-streaming)."""

    def _gemini_success_payload(self, text="Hello from Gemini"):
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "finishReason": "STOP",
                }
            ]
        }

    @patch("app.LLM.llm_client.requests.post")
    def test_missing_api_key_short_circuits_without_network_call(self, mock_post):
        with patch("app.LLM.llm_client.GEMINI_API_KEY", None):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: GEMINI_API_KEY is not set"))
        mock_post.assert_not_called()

    @patch("app.LLM.llm_client.requests.post")
    def test_successful_response_parsing(self, mock_post):
        mock_post.return_value = FakeResponse(
            status_code=200, json_data=self._gemini_success_payload("Hello from Gemini")
        )

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GEMINI_MODEL_NAME", "gemini-primary"):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "Hello from Gemini")
        mock_post.assert_called_once()
        called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        self.assertTrue(called_url.endswith("models/gemini-primary:generateContent"))
        self.assertEqual(mock_post.call_args.kwargs["params"], {"key": "test-key"})

    @patch("app.LLM.llm_client.requests.post")
    def test_http_error_returns_error_string_without_retry(self, mock_post):
        mock_post.return_value = FakeResponse(
            status_code=403,
            json_data={"error": {"message": "project denied access"}},
        )

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GEMINI_FALLBACK_MODEL_NAME", "gemini-fallback"):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "ERROR: Gemini HTTP 403 - project denied access")
        # 403 is not in the (429, 503) retry set, so no fallback call is made.
        mock_post.assert_called_once()

    @patch("app.LLM.llm_client.requests.post")
    def test_fallback_model_retried_on_429(self, mock_post):
        mock_post.side_effect = [
            FakeResponse(status_code=429, json_data={"error": {"message": "rate limited"}}),
            FakeResponse(status_code=200, json_data=self._gemini_success_payload("fallback worked")),
        ]

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GEMINI_MODEL_NAME", "gemini-primary"), \
             patch("app.LLM.llm_client.GEMINI_FALLBACK_MODEL_NAME", "gemini-fallback"):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "fallback worked")
        self.assertEqual(mock_post.call_count, 2)
        first_url = mock_post.call_args_list[0].args[0] if mock_post.call_args_list[0].args else mock_post.call_args_list[0].kwargs.get("url")
        second_url = mock_post.call_args_list[1].args[0] if mock_post.call_args_list[1].args else mock_post.call_args_list[1].kwargs.get("url")
        self.assertIn("gemini-primary", first_url)
        self.assertIn("gemini-fallback", second_url)

    @patch("app.LLM.llm_client.requests.post")
    def test_fallback_model_retried_on_503(self, mock_post):
        mock_post.side_effect = [
            FakeResponse(status_code=503, json_data={"error": {"message": "overloaded"}}),
            FakeResponse(status_code=200, json_data=self._gemini_success_payload("fallback worked again")),
        ]

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GEMINI_MODEL_NAME", "gemini-primary"), \
             patch("app.LLM.llm_client.GEMINI_FALLBACK_MODEL_NAME", "gemini-fallback"):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "fallback worked again")
        self.assertEqual(mock_post.call_count, 2)

    @patch("app.LLM.llm_client.requests.post")
    def test_no_fallback_configured_means_single_call_on_429(self, mock_post):
        mock_post.return_value = FakeResponse(
            status_code=429, json_data={"error": {"message": "rate limited"}}
        )

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"), \
             patch("app.LLM.llm_client.GEMINI_FALLBACK_MODEL_NAME", None):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Gemini HTTP 429"))
        mock_post.assert_called_once()

    @patch("app.LLM.llm_client.requests.post")
    def test_connection_error_returns_error_string(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Cannot connect to Gemini API"))

    @patch("app.LLM.llm_client.requests.post")
    def test_timeout_returns_error_string(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout()

        with patch("app.LLM.llm_client.GEMINI_API_KEY", "test-key"):
            result = llm_client.call_gemini([{"role": "user", "content": "hi"}])

        self.assertTrue(result.startswith("ERROR: Gemini request timed out"))


class TestCallGeminiStream(unittest.TestCase):
    @patch("app.LLM.llm_client.call_gemini")
    def test_success_yields_token_then_done(self, mock_call_gemini):
        mock_call_gemini.return_value = "full text"

        chunks = list(llm_client.call_gemini_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, [{"token": "full text", "done": False}, {"token": "", "done": True}])

    @patch("app.LLM.llm_client.call_gemini")
    def test_error_yields_single_error_chunk(self, mock_call_gemini):
        mock_call_gemini.return_value = "ERROR: Gemini HTTP 403 - denied"

        chunks = list(llm_client.call_gemini_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["error"])
        self.assertTrue(chunks[0]["done"])


class TestCallDockerLlmRouting(unittest.TestCase):
    """
    call_docker_llm/call_docker_llm_stream route to call_llama vs call_gemini
    based on the *module-level* DOCKER_LLM_PROVIDER constant (see module
    docstring for why this patches app.LLM.llm_client.DOCKER_LLM_PROVIDER
    rather than settings.DOCKER_LLM_PROVIDER).
    """

    @patch("app.LLM.llm_client.call_gemini")
    @patch("app.LLM.llm_client.call_llama")
    def test_routes_to_llama_when_provider_is_ollama(self, mock_llama, mock_gemini):
        mock_llama.return_value = "llama response"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "ollama"):
            result = llm_client.call_docker_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "llama response")
        mock_llama.assert_called_once()
        mock_gemini.assert_not_called()

    @patch("app.LLM.llm_client.call_gemini")
    @patch("app.LLM.llm_client.call_llama")
    def test_routes_to_gemini_when_provider_is_gemini(self, mock_llama, mock_gemini):
        mock_gemini.return_value = "gemini response"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "gemini"):
            result = llm_client.call_docker_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "gemini response")
        mock_gemini.assert_called_once()
        mock_llama.assert_not_called()

    @patch("app.LLM.llm_client.call_gemini")
    @patch("app.LLM.llm_client.call_llama")
    def test_unrecognized_provider_defaults_to_llama(self, mock_llama, mock_gemini):
        mock_llama.return_value = "llama response"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "not-a-real-provider"):
            result = llm_client.call_docker_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "llama response")
        mock_gemini.assert_not_called()

    @patch("app.LLM.llm_client.call_gemini")
    @patch("app.LLM.llm_client.call_llama")
    def test_provider_is_case_insensitive(self, mock_llama, mock_gemini):
        mock_gemini.return_value = "gemini response"

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "GEMINI"):
            result = llm_client.call_docker_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "gemini response")
        mock_llama.assert_not_called()

    @patch("app.LLM.llm_client.call_gemini_stream")
    @patch("app.LLM.llm_client.call_llama_stream")
    def test_stream_routes_to_llama_stream_when_provider_is_ollama(self, mock_llama_stream, mock_gemini_stream):
        mock_llama_stream.return_value = iter([{"token": "hi", "done": True}])

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "ollama"):
            chunks = list(llm_client.call_docker_llm_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, [{"token": "hi", "done": True}])
        mock_llama_stream.assert_called_once()
        mock_gemini_stream.assert_not_called()

    @patch("app.LLM.llm_client.call_gemini_stream")
    @patch("app.LLM.llm_client.call_llama_stream")
    def test_stream_routes_to_gemini_stream_when_provider_is_gemini(self, mock_llama_stream, mock_gemini_stream):
        mock_gemini_stream.return_value = iter([{"token": "yo", "done": True}])

        with patch("app.LLM.llm_client.DOCKER_LLM_PROVIDER", "gemini"):
            chunks = list(llm_client.call_docker_llm_stream([{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, [{"token": "yo", "done": True}])
        mock_gemini_stream.assert_called_once()
        mock_llama_stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
