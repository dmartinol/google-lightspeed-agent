"""Entry point for running the Marketplace Handler service."""

import logging
import os

import uvicorn


def main() -> None:
    """Run the Marketplace Handler service."""
    # Configure logging
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "text")

    if log_format == "json":
        logging.basicConfig(
            level=log_level,
            format=(
                '{"time": "%(asctime)s", "level": "%(levelname)s",'
                ' "logger": "%(name)s", "message": "%(message)s"}'
            ),
        )
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    # Get host and port from environment
    host = os.getenv("HANDLER_HOST", "0.0.0.0")
    port = int(os.getenv("HANDLER_PORT", "8001"))

    logging.info(f"Starting Marketplace Handler on {host}:{port}")

    uvicorn.run(
        "lightspeed_agent.marketplace.app:create_app",
        host=host,
        port=port,
        factory=True,
        log_level=log_level.lower(),
    )


if __name__ == "__main__":
    main()
