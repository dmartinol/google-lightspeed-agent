"""Build google-genai HttpRetryOptions from application settings."""

from google.genai import types

from lightspeed_agent.config.settings import Settings


def http_retry_options_from_settings(settings: Settings) -> types.HttpRetryOptions:
    """Map Settings to SDK retry options (exponential backoff with jitter).

    See: https://cloud.google.com/vertex-ai/generative-ai/docs/retry-strategy
    """
    return types.HttpRetryOptions(
        attempts=settings.gemini_http_retry_attempts,
        initial_delay=settings.gemini_http_retry_initial_delay,
        max_delay=settings.gemini_http_retry_max_delay,
        exp_base=settings.gemini_http_retry_exp_base,
        jitter=settings.gemini_http_retry_jitter,
    )
