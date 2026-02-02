import sys
import logging
from loguru import logger
from app.config import settings

class InterceptHandler(logging.Handler):
    """
    Loguru intercept handler for standard logging.
    """
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )

def setup_logging():
    """
    Configure logging using Loguru.
    """
    # Remove default logger
    logger.remove()

    # Add sink for stdout with JSON serialization for production or structured output
    # For development, we might want a pretty format, but the requirement asks for detailed logs.
    # We will use a format that includes extra fields.

    logger.add(
        sys.stdout,
        enqueue=True,
        backtrace=True,
        diagnose=settings.DEBUG,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message} | {extra}",
        level="DEBUG" if settings.DEBUG else "INFO",
        serialize=False # Set to True if we want pure JSON lines
    )

    # Intercept standard logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Silence uvicorn access log duplicate (optional, but good practice)
    # logging.getLogger("uvicorn.access").handlers = [InterceptHandler()]

    logger.info("Logging configured.")
