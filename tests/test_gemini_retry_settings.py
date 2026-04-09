"""Tests for Gemini HTTP retry settings and HttpRetryOptions mapping."""

import os

import pytest
from google.adk.models import Gemini
from pydantic import ValidationError

from lightspeed_agent.config import Settings, get_settings
from lightspeed_agent.core.gemini_retry import http_retry_options_from_settings


def test_http_retry_options_from_settings_defaults():
    s = Settings(google_api_key="test-key")
    opts = http_retry_options_from_settings(s)
    assert opts.attempts == 5
    assert opts.initial_delay == 1.0
    assert opts.max_delay == 60.0
    assert opts.exp_base == 2.0
    assert opts.jitter == 1.0


def test_gemini_model_receives_http_retry_options_from_settings():
    """Contract: ADK Gemini carries the same HttpRetryOptions we build from Settings."""
    s = Settings(
        google_api_key="test-key",
        gemini_http_retry_attempts=3,
        gemini_http_retry_initial_delay=2.5,
        gemini_http_retry_max_delay=30.0,
        gemini_http_retry_exp_base=3.0,
        gemini_http_retry_jitter=0.5,
    )
    opts = http_retry_options_from_settings(s)
    gemini = Gemini(model="gemini-2.5-flash", retry_options=opts)
    assert gemini.retry_options is not None
    assert gemini.retry_options.model_dump() == opts.model_dump()


def test_http_retry_options_from_settings_custom():
    s = Settings(
        google_api_key="test-key",
        gemini_http_retry_attempts=3,
        gemini_http_retry_initial_delay=2.5,
        gemini_http_retry_max_delay=30.0,
        gemini_http_retry_exp_base=3.0,
        gemini_http_retry_jitter=0.5,
    )
    opts = http_retry_options_from_settings(s)
    assert opts.attempts == 3
    assert opts.initial_delay == 2.5
    assert opts.max_delay == 30.0
    assert opts.exp_base == 3.0
    assert opts.jitter == 0.5


@pytest.mark.parametrize(
    "invalid_kwargs",
    [
        pytest.param({"gemini_http_retry_attempts": 0}, id="attempts_lt_1"),
        pytest.param({"gemini_http_retry_initial_delay": 0}, id="initial_delay_not_gt_0"),
        pytest.param({"gemini_http_retry_initial_delay": -0.5}, id="initial_delay_negative"),
        pytest.param({"gemini_http_retry_max_delay": 0}, id="max_delay_not_gt_0"),
        pytest.param({"gemini_http_retry_max_delay": -1.0}, id="max_delay_negative"),
        pytest.param({"gemini_http_retry_exp_base": 0}, id="exp_base_not_gt_0"),
        pytest.param({"gemini_http_retry_exp_base": -2.0}, id="exp_base_negative"),
        pytest.param({"gemini_http_retry_jitter": -0.01}, id="jitter_negative"),
    ],
)
def test_gemini_http_retry_settings_validation(invalid_kwargs):
    """Field constraints: attempts ge=1; delays and exp_base gt=0; jitter ge=0."""
    with pytest.raises(ValidationError):
        Settings(google_api_key="test-key", **invalid_kwargs)


def test_settings_reads_retry_env(monkeypatch):
    monkeypatch.setenv("GEMINI_HTTP_RETRY_ATTEMPTS", "8")
    monkeypatch.setenv("GEMINI_HTTP_RETRY_INITIAL_DELAY", "1.5")
    monkeypatch.setenv("GEMINI_HTTP_RETRY_MAX_DELAY", "45.0")
    monkeypatch.setenv("GEMINI_HTTP_RETRY_EXP_BASE", "2.5")
    monkeypatch.setenv("GEMINI_HTTP_RETRY_JITTER", "0.25")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.gemini_http_retry_attempts == 8
        assert s.gemini_http_retry_initial_delay == 1.5
        assert s.gemini_http_retry_max_delay == 45.0
        assert s.gemini_http_retry_exp_base == 2.5
        assert s.gemini_http_retry_jitter == 0.25
        opts = http_retry_options_from_settings(s)
        assert opts.attempts == 8
    finally:
        for key in (
            "GEMINI_HTTP_RETRY_ATTEMPTS",
            "GEMINI_HTTP_RETRY_INITIAL_DELAY",
            "GEMINI_HTTP_RETRY_MAX_DELAY",
            "GEMINI_HTTP_RETRY_EXP_BASE",
            "GEMINI_HTTP_RETRY_JITTER",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
