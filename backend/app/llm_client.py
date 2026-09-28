"""Groq LLM client. The ONE public interface is `generate_response(prompt)`."""
import logging

import groq
from groq import AsyncGroq

from .config import Settings, settings as default_settings

logger = logging.getLogger("sre.llm")

SYSTEM_PROMPT = """You are an SRE Incident Response Agent with access to organizational incident memory.

Analyze production incidents using:

- Current incident information
- Previous similar incidents
- Root causes
- Successful fixes
- Failed troubleshooting attempts
- Deployment history
- Infrastructure context
- Engineer feedback

Never invent historical incidents.

If historical memory does not contain relevant information, clearly state that.

Do not blindly repeat troubleshooting actions that previously failed.

Prioritize evidence-backed recommendations.

Clearly separate historical facts from current inference.

Provide practical and concise guidance for the engineer handling the incident."""


class LLMError(Exception):
    """Raised for any LLM failure. `kind` is one of: not_configured, authentication,
    rate_limit, timeout, connection, invalid_model, bad_request, api_error, empty_response."""

    def __init__(self, message: str, kind: str = "api_error"):
        super().__init__(message)
        self.kind = kind


class LLMClient:
    def __init__(self, settings: Settings = default_settings, client: AsyncGroq | None = None):
        self.settings = settings
        self.model = settings.groq_model
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.settings.groq_api_key) or self._client is not None

    def _get_client(self) -> AsyncGroq:
        if self._client is None:
            if not self.settings.groq_api_key:
                raise LLMError("GROQ_API_KEY is not configured", "not_configured")
            self._client = AsyncGroq(api_key=self.settings.groq_api_key, timeout=self.settings.llm_timeout_seconds)
        return self._client

    async def generate_response(
        self,
        prompt: str,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 1800,
    ) -> str:
        """Send one prompt to Groq and return the text. Raises LLMError on any failure."""
        client = self._get_client()
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        kwargs = dict(model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            try:
                resp = await client.chat.completions.create(**kwargs)
            except groq.BadRequestError as exc:
                # some models reject JSON mode; retry once as plain text
                if json_mode and "json" in str(exc).lower():
                    kwargs.pop("response_format", None)
                    resp = await client.chat.completions.create(**kwargs)
                else:
                    raise
        except LLMError:
            raise
        except groq.AuthenticationError as exc:
            raise LLMError("Groq authentication failed (check GROQ_API_KEY)", "authentication") from exc
        except groq.PermissionDeniedError as exc:
            raise LLMError("Groq denied access for this key/model", "authentication") from exc
        except groq.RateLimitError as exc:
            raise LLMError("Groq rate limit reached", "rate_limit") from exc
        except groq.APITimeoutError as exc:
            raise LLMError("Groq request timed out", "timeout") from exc
        except groq.APIConnectionError as exc:
            raise LLMError("Could not connect to Groq", "connection") from exc
        except groq.NotFoundError as exc:
            raise LLMError(f"Groq model '{self.model}' was not found", "invalid_model") from exc
        except groq.BadRequestError as exc:
            kind = "invalid_model" if "model" in str(exc).lower() else "bad_request"
            raise LLMError(f"Groq rejected the request ({kind})", kind) from exc
        except groq.APIStatusError as exc:
            raise LLMError(f"Groq API error (HTTP {exc.status_code})", "api_error") from exc
        except Exception as exc:  # never let an SDK surprise crash the app
            logger.exception("Unexpected LLM failure")
            raise LLMError(f"Unexpected LLM failure: {type(exc).__name__}", "api_error") from exc

        try:
            text = (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError):
            text = ""
        if not text:
            raise LLMError("Groq returned an empty response", "empty_response")
        return text
