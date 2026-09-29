import os
import sys
import logging
from loguru import logger
from app.config import settings


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging():
    """Single source-of-truth for application logging (stdout + optional rotating file)."""
    logger.remove()

    fmt = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message} | {extra}"
    level = "DEBUG" if settings.DEBUG else "INFO"

    logger.add(
        sys.stdout,
        enqueue=True,
        backtrace=True,
        diagnose=settings.DEBUG,
        format=fmt,
        level=level,
    )

    log_dir = os.environ.get("LOG_DIR", "")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        logger.add(
            os.path.join(log_dir, "app.log"),
            rotation="10 MB",
            retention=5,
            encoding="utf-8",
            enqueue=True,
            level=level,
            format=fmt,
        )
        logger.add(
            os.path.join(log_dir, "errors.log"),
            rotation="10 MB",
            retention=10,
            encoding="utf-8",
            enqueue=True,
            level="ERROR",
            format=fmt,
        )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logger.info("Logging configured.")


def get_logger(name: str = "app"):
    """Return a loguru logger bound with the given name."""
    return logger.bind(name=name)


def get_task_logger():
    return logger.bind(name="app.tasks")


def get_ai_logger():
    return logger.bind(name="app.ai")
