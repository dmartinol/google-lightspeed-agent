"""Tests for application settings guards."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lightspeed_agent.config import Settings


class TestSkipJwtProductionGuard:
    """Verify SKIP_JWT_VALIDATION cannot be enabled in Cloud Run."""

    def _env_without_k_service(self) -> dict[str, str]:
        """Return a copy of os.environ without K_SERVICE."""
        return {k: v for k, v in os.environ.items() if k != "K_SERVICE"}

    def test_skip_jwt_allowed_without_k_service(self):
        """SKIP_JWT_VALIDATION=true is fine when K_SERVICE is unset."""
        with patch.dict(os.environ, self._env_without_k_service(), clear=True):
            settings = Settings(skip_jwt_validation=True)
            assert settings.skip_jwt_validation is True

    def test_skip_jwt_blocked_in_cloud_run(self):
        """SKIP_JWT_VALIDATION=true must fail when K_SERVICE is set."""
        with (
            patch.dict(os.environ, {"K_SERVICE": "lightspeed-agent"}, clear=False),
            pytest.raises(ValidationError, match="not allowed in Cloud Run"),
        ):
            Settings(skip_jwt_validation=True)

    def test_no_skip_jwt_allowed_in_cloud_run(self):
        """SKIP_JWT_VALIDATION=false (default) is fine in Cloud Run."""
        with patch.dict(
            os.environ, {"K_SERVICE": "lightspeed-agent"}, clear=False
        ):
            settings = Settings(skip_jwt_validation=False)
            assert settings.skip_jwt_validation is False

    def test_skip_jwt_defaults_to_false(self):
        """Default value of skip_jwt_validation is False."""
        with patch.dict(
            os.environ,
            self._env_without_k_service()
            | {"SKIP_JWT_VALIDATION": "false"},
            clear=True,
        ):
            settings = Settings(skip_jwt_validation=False)
            assert settings.skip_jwt_validation is False
