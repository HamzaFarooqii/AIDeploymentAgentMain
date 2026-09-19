import re
import time
import requests
from typing import List, Dict, Optional

from ..config.settings import settings

# Matches a `key=...` query-param value (as Google's REST APIs use for the
# API key) so it can be stripped out of any error text before it's returned
# to a client or printed to logs - requests' own exception messages include
# the full request URL, which would otherwise leak the raw key.
_API_KEY_QUERY_PATTERN = re.compile(r"([?&]key=)[^&\s'\"]+")


def _redact_api_key(text: str) -> str:
    return _API_KEY_QUERY_PATTERN.sub(r"\1***REDACTED***", text)


def _request_with_retry(method: str, url: str, *, retries: int = 3, **kwargs) -> requests.Response:
    """
    requests.post with a short retry for transient connection-level failures
    (e.g. an intermittent network blip producing SSLEOFError or a plain
    ConnectionError). These mean the connection itself failed before any
    response was received - not the API rejecting the request - so a quick
    retry with a short backoff often just succeeds. Does not retry on HTTP
    error responses (4xx/5xx); those are handled by the caller.

    `method` is accepted for clarity at call sites but every call site here
    uses POST; calling requests.post directly (rather than requests.request)
    keeps this compatible with tests that patch app.LLM.llm_client.requests.post.
    """
    if retries < 1:
        retries = 1
    last_exc: Exception = requests.exceptions.ConnectionError("no attempts made")
    for attempt in range(retries):
        try:
            return requests.post(url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s
    raise last_exc

# LLM Configuration - loaded from settings
OLLAMA_URL = settings.OLLAMA_URL
MODEL_NAME = settings.LLM_MODEL_NAME
LLM_TEMPERATURE = settings.LLM_TEMPERATURE
LLM_TOP_P = settings.LLM_TOP_P
LLM_TIMEOUT = settings.LLM_TIMEOUT
DOCKER_LLM_PROVIDER = settings.DOCKER_LLM_PROVIDER
GEMINI_API_KEY = settings.GEMINI_API_KEY
GEMINI_API_BASE = settings.GEMINI_API_BASE.rstrip("/")
GEMINI_MODEL_NAME = settings.GEMINI_MODEL_NAME
GEMINI_MAX_OUTPUT_TOKENS = settings.GEMINI_MAX_OUTPUT_TOKENS
GEMINI_FALLBACK_MODEL_NAME = settings.GEMINI_FALLBACK_MODEL_NAME


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    prompt = ""
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            prompt += f"System: {content}\n\n"
        elif role == "user":
            prompt += f"User: {content}\n\n"
        elif content:
            prompt += f"{content}\n\n"
    return prompt + "Assistant:"


def _split_messages_for_gemini(messages: List[Dict[str, str]]) -> tuple[str, str]:
    system_parts: List[str] = []
    user_parts: List[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def _gemini_generation_config(custom_options: Optional[Dict] = None) -> Dict:
    config = {
        "temperature": LLM_TEMPERATURE,
        "topP": LLM_TOP_P,
        "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
    }
    if not custom_options:
        return config

    option_map = {
        "temperature": "temperature",
        "top_p": "topP",
        "topP": "topP",
        "max_output_tokens": "maxOutputTokens",
        "maxOutputTokens": "maxOutputTokens",
    }
    for src, dest in option_map.items():
        if src in custom_options:
            config[dest] = custom_options[src]
    return config


def get_docker_llm_provider() -> str:
    provider = str(DOCKER_LLM_PROVIDER or "ollama").strip().lower()
    return provider if provider in {"ollama", "gemini"} else "ollama"


def call_llama(messages: List[Dict[str, str]], custom_options: Optional[Dict] = None) -> str:
    """
    Call the LLM (via Ollama) with the given messages.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys.
                  Roles are typically 'system' and 'user'.
        custom_options: Optional dict to override default options.
    
    Returns:
        The LLM's response content as a string.
    """
    try:
        prompt = _messages_to_prompt(messages)
        
        # Build options with defaults
        options = {
            "temperature": LLM_TEMPERATURE,
            "top_p": LLM_TOP_P,
            "num_ctx": 16384,  # 8k context window
        }
        
        # Merge custom options if provided
        if custom_options:
            options.update(custom_options)
        
        resp = _request_with_retry(
            "POST",
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "options": options,
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()

        # Parse response
        response_text = resp.text.strip()
        
        # Handle newline-delimited JSON (ndjson)
        if '\n' in response_text:
            lines = response_text.strip().split('\n')
            data = None
            for line in lines:
                if line.strip():
                    import json
                    data = json.loads(line)
        else:
            data = resp.json()
        
        return data.get("response", "")
    except requests.exceptions.ConnectionError as e:
        return f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}. Details: {_redact_api_key(str(e))}"
    except requests.exceptions.Timeout:
        return f"ERROR: LLM request timed out after {LLM_TIMEOUT} seconds."
    except requests.exceptions.HTTPError as e:
        return f"ERROR: HTTP {e.response.status_code} - {e.response.reason}. URL: {OLLAMA_URL}"
    except Exception as e:
        import traceback
        return f"ERROR: LLM call failed - {_redact_api_key(str(e))}\nTraceback: {_redact_api_key(traceback.format_exc()[:500])}"


def call_llama_stream(messages: List[Dict[str, str]], custom_options: Optional[Dict] = None):
    """
    Streaming version of call_llama. Yields tokens as they're generated by Ollama.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        custom_options: Optional dict to override default options.
    
    Yields:
        dict: Each chunk with 'token' (str) and 'done' (bool) keys.
    """
    import json
    
    try:
        prompt = _messages_to_prompt(messages)
        
        # Build options with defaults
        options = {
            "temperature": LLM_TEMPERATURE,
            "top_p": LLM_TOP_P,
            "num_ctx": 8192,
        }
        
        if custom_options:
            options.update(custom_options)
        
        # Use streaming request
        resp = _request_with_retry(
            "POST",
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": True,  # Enable streaming
                "options": options,
            },
            timeout=LLM_TIMEOUT,
            stream=True,  # Enable response streaming
        )
        resp.raise_for_status()
        
        # Iterate over streamed lines
        for line in resp.iter_lines():
            if line:
                try:
                    data = json.loads(line.decode('utf-8'))
                    token = data.get("response", "")
                    done = data.get("done", False)
                    yield {"token": token, "done": done}
                    if done:
                        break
                except json.JSONDecodeError:
                    continue
                    
    except requests.exceptions.ConnectionError as e:
        yield {"token": f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}. Details: {_redact_api_key(str(e))}", "done": True, "error": True}
    except requests.exceptions.Timeout:
        yield {"token": f"ERROR: LLM request timed out after {LLM_TIMEOUT} seconds.", "done": True, "error": True}
    except requests.exceptions.HTTPError as e:
        yield {"token": f"ERROR: HTTP {e.response.status_code} - {e.response.reason}. URL: {OLLAMA_URL}", "done": True, "error": True}
    except Exception as e:
        import traceback
        yield {"token": f"ERROR: LLM stream failed - {_redact_api_key(str(e))}", "done": True, "error": True}


def _call_gemini_once(
    messages: List[Dict[str, str]],
    model_name: str,
    custom_options: Optional[Dict] = None,
) -> tuple[str, int]:
    """
    Single Gemini API call. Returns (response_text, http_status_code).
    On success http_status_code is 200; on error, the response_text starts with 'ERROR:'.
    """
    system_text, user_text = _split_messages_for_gemini(messages)
    model_path = model_name if model_name.startswith("models/") else f"models/{model_name}"
    url = f"{GEMINI_API_BASE}/{model_path}:generateContent"

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_text}],
            }
        ],
        "generationConfig": _gemini_generation_config(custom_options),
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    resp = _request_with_retry(
        "POST",
        url,
        params={"key": GEMINI_API_KEY},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=LLM_TIMEOUT,
    )

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if resp.status_code >= 400:
        detail = (data.get("error") or {}).get("message") or resp.text[:500]
        return f"ERROR: Gemini HTTP {resp.status_code} - {detail}", resp.status_code

    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        return f"ERROR: Gemini returned no candidates. Feedback: {feedback}", resp.status_code

    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    if not text.strip():
        finish_reason = candidates[0].get("finishReason")
        return f"ERROR: Gemini returned an empty response. Finish reason: {finish_reason}", resp.status_code
    return text, resp.status_code


def call_gemini(messages: List[Dict[str, str]], custom_options: Optional[Dict] = None) -> str:
    """
    Call Gemini for Docker generation. This is intentionally separate from
    call_llama so Terraform and other Ollama paths keep their existing behavior.

    If the primary model returns 429 or 503 and a fallback model is configured,
    the request is retried once with the fallback model.
    """
    if not GEMINI_API_KEY:
        return "ERROR: GEMINI_API_KEY is not set. Set it in backend-python/.env to use Gemini."

    try:
        result, status = _call_gemini_once(messages, GEMINI_MODEL_NAME, custom_options)

        if status in (429, 503) and GEMINI_FALLBACK_MODEL_NAME and GEMINI_FALLBACK_MODEL_NAME != GEMINI_MODEL_NAME:
            print(f"Gemini primary model ({GEMINI_MODEL_NAME}) returned {status}. Retrying with fallback ({GEMINI_FALLBACK_MODEL_NAME})...")
            result, status = _call_gemini_once(messages, GEMINI_FALLBACK_MODEL_NAME, custom_options)

        return result
    except requests.exceptions.ConnectionError as e:
        return f"ERROR: Cannot connect to Gemini API at {GEMINI_API_BASE}. Details: {_redact_api_key(str(e))}"
    except requests.exceptions.Timeout:
        return f"ERROR: Gemini request timed out after {LLM_TIMEOUT} seconds."
    except Exception as e:
        import traceback
        return f"ERROR: Gemini call failed - {_redact_api_key(str(e))}\nTraceback: {_redact_api_key(traceback.format_exc()[:500])}"


def call_gemini_stream(messages: List[Dict[str, str]], custom_options: Optional[Dict] = None):
    """
    Minimal streaming adapter: Gemini is called once and emitted as one SSE token.
    The frontend already accepts token chunks, so this preserves the endpoint shape.
    """
    response = call_gemini(messages, custom_options=custom_options)
    if response.startswith("ERROR:"):
        yield {"token": response, "done": True, "error": True}
        return
    yield {"token": response, "done": False}
    yield {"token": "", "done": True}


def call_docker_llm(messages: List[Dict[str, str]], custom_options: Optional[Dict] = None) -> str:
    if get_docker_llm_provider() == "gemini":
        return call_gemini(messages, custom_options=custom_options)
    return call_llama(messages, custom_options=custom_options)


def call_docker_llm_stream(messages: List[Dict[str, str]], custom_options: Optional[Dict] = None):
    if get_docker_llm_provider() == "gemini":
        yield from call_gemini_stream(messages, custom_options=custom_options)
        return
    yield from call_llama_stream(messages, custom_options=custom_options)
