"""
Logging Configuration.

Rotating handlers ile log yapılandırması.
"""
import os
import logging
import logging.config
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

# Log dizini
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'logs')


def ensure_log_dir():
    """Log dizinini oluşturur."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)


# Logging konfigürasyonu
LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'detailed': {
            'format': '%(asctime)s [%(levelname)s] %(name)s.%(funcName)s:%(lineno)d - %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'json': {
            'format': '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
            'datefmt': '%Y-%m-%dT%H:%M:%S'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'stream': 'ext://sys.stdout'
        },
        'app_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'filename': os.path.join(LOG_DIR, 'app.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'encoding': 'utf-8'
        },
        'task_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',
            'formatter': 'detailed',
            'filename': os.path.join(LOG_DIR, 'tasks.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'encoding': 'utf-8'
        },
        'ai_file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'level': 'DEBUG',
            'formatter': 'json',
            'filename': os.path.join(LOG_DIR, 'ai_calls.log'),
            'when': 'midnight',
            'backupCount': 7,  # 1 hafta
            'encoding': 'utf-8'
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'ERROR',
            'formatter': 'detailed',
            'filename': os.path.join(LOG_DIR, 'errors.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 10,
            'encoding': 'utf-8'
        }
    },
    'loggers': {
        'app': {
            'handlers': ['console', 'app_file'],
            'level': 'INFO',
            'propagate': False
        },
        'app.tasks': {
            'handlers': ['console', 'task_file'],
            'level': 'INFO',
            'propagate': False
        },
        'app.ai': {
            'handlers': ['ai_file'],
            'level': 'DEBUG',
            'propagate': False
        },
        'app.generators': {
            'handlers': ['console', 'app_file'],
            'level': 'INFO',
            'propagate': False
        }
    },
    'root': {
        'handlers': ['console', 'app_file', 'error_file'],
        'level': 'INFO'
    }
}


def setup_logging():
    """
    Logging sistemini başlatır.
    Uygulama başlangıcında çağrılmalı.
    """
    ensure_log_dir()
    logging.config.dictConfig(LOGGING_CONFIG)
    
    # İlk log
    logger = logging.getLogger('app')
    logger.info("Logging system initialized")


def get_logger(name: str) -> logging.Logger:
    """
    İsimlendirilmiş logger döndürür.
    
    Usage:
        logger = get_logger('app.tasks')
        logger.info("Task started")
    """
    return logging.getLogger(name)


# Task-specific loggers
def get_task_logger() -> logging.Logger:
    """Celery task logger."""
    return logging.getLogger('app.tasks')


def get_ai_logger() -> logging.Logger:
    """AI API call logger."""
    return logging.getLogger('app.ai')


def log_ai_call(model: str, prompt: str, response: str, duration: float):
    """
    AI çağrısını loglar.
    
    Args:
        model: Kullanılan model (gemini-pro, etc.)
        prompt: Gönderilen prompt (kısaltılmış)
        response: Alınan yanıt (kısaltılmış)
        duration: Çağrı süresi (saniye)
    """
    logger = get_ai_logger()
    
    # Çok uzun prompt/response'ları kısalt
    max_len = 500
    prompt_short = prompt[:max_len] + '...' if len(prompt) > max_len else prompt
    response_short = response[:max_len] + '...' if len(response) > max_len else response
    
    logger.info(
        f"model={model} duration={duration:.2f}s "
        f"prompt_len={len(prompt)} response_len={len(response)}"
    )
    logger.debug(f"prompt: {prompt_short}")
    logger.debug(f"response: {response_short}")
