"""Repository for DCR registered clients with PostgreSQL persistence."""

import logging
from typing import Any

from sqlalchemy import select

from lightspeed_agent.db import DCRClientModel, get_session
from lightspeed_agent.dcr.models import RegisteredClient

logger = logging.getLogger(__name__)


class DCRClientRepository:
    """Repository for storing and retrieving DCR registered clients.

    Uses PostgreSQL via SQLAlchemy for persistence.
    """

    async def get_by_client_id(self, client_id: str) -> RegisteredClient | None:
        """Get a registered client by client_id.

        The same client_id may be associated with multiple orders.
        Returns the most recently created entry.

        Args:
            client_id: The OAuth client ID.

        Returns:
            RegisteredClient if found, None otherwise.
        """
        async with get_session() as session:
            result = await session.execute(
                select(DCRClientModel)
                .where(DCRClientModel.client_id == client_id)
                .order_by(DCRClientModel.created_at.desc())
            )
            model = result.scalars().first()
            if model:
                return self._model_to_entity(model)
            return None

    async def get_by_order_id(self, order_id: str) -> RegisteredClient | None:
        """Get a registered client by order_id.

        Args:
            order_id: The marketplace order ID.

        Returns:
            RegisteredClient if found, None otherwise.
        """
        async with get_session() as session:
            result = await session.execute(
                select(DCRClientModel).where(DCRClientModel.order_id == order_id)
            )
            model = result.scalar_one_or_none()
            if model:
                return self._model_to_entity(model)
            return None

    async def create(
        self,
        client_id: str,
        client_secret_encrypted: str,
        order_id: str,
        account_id: str,
        redirect_uris: list[str] | None = None,
        grant_types: list[str] | None = None,
        registration_access_token_encrypted: str | None = None,
        keycloak_client_uuid: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RegisteredClient:
        """Create a new registered client.

        Args:
            client_id: The OAuth client ID.
            client_secret_encrypted: Encrypted client secret.
            order_id: The marketplace order ID.
            account_id: The marketplace account ID.
            redirect_uris: OAuth redirect URIs.
            grant_types: OAuth grant types.
            registration_access_token_encrypted: Encrypted registration access token.
            keycloak_client_uuid: Keycloak's internal client UUID.
            metadata: Additional metadata.

        Returns:
            The created RegisteredClient.
        """
        async with get_session() as session:
            model = DCRClientModel(
                client_id=client_id,
                client_secret_encrypted=client_secret_encrypted,
                order_id=order_id,
                account_id=account_id,
                redirect_uris=redirect_uris or [],
                grant_types=grant_types or ["authorization_code", "refresh_token"],
                registration_access_token_encrypted=registration_access_token_encrypted,
                keycloak_client_uuid=keycloak_client_uuid,
                metadata_=metadata or {},
            )
            session.add(model)
            await session.flush()
            await session.refresh(model)

            logger.info(
                "Created DCR client: client_id=%s, order_id=%s",
                client_id,
                order_id,
            )

            return self._model_to_entity(model)

    def _model_to_entity(self, model: DCRClientModel) -> RegisteredClient:
        """Convert ORM model to Pydantic entity.

        Args:
            model: The ORM model.

        Returns:
            RegisteredClient entity.
        """
        return RegisteredClient(
            client_id=model.client_id,
            client_secret_encrypted=model.client_secret_encrypted,
            order_id=model.order_id,
            account_id=model.account_id,
            redirect_uris=model.redirect_uris or [],
            grant_types=model.grant_types or ["authorization_code", "refresh_token"],
            created_at=model.created_at,
            metadata={
                **(model.metadata_ or {}),
                "keycloak_client_uuid": model.keycloak_client_uuid,
                "has_registration_token": model.registration_access_token_encrypted is not None,
            },
        )


# Global repository instance
_dcr_client_repo: DCRClientRepository | None = None


def get_dcr_client_repository() -> DCRClientRepository:
    """Get the global DCR client repository instance.

    Returns:
        DCRClientRepository instance.
    """
    global _dcr_client_repo
    if _dcr_client_repo is None:
        _dcr_client_repo = DCRClientRepository()
    return _dcr_client_repo
