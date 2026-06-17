import os
from typing import List
from pydantic_settings import BaseSettings

class BaseWorkerSettings(BaseSettings):
    """
    Global Base Configuration for all GPU Workers.
    """
    WORKER_ID: str = "worker-1"
    SUPPORTED_TASKS: List[str] = []
    
    # Agent Core Go API Integration
    Agent Core_API_BASE: str = "http://localhost:8011/internal"
    WORKER_SECRET: str = "your_internal_secret_here"
    
    # Storage / Cloudflare R2
    R2_ENDPOINT: str = ""
    R2_ACCESS_KEY: str = ""
    R2_SECRET_KEY: str = ""
    R2_BUCKET: str = ""
    R2_CUSTOM_DOMAIN: str = ""
    
    class Config:
        case_sensitive = True
        env_file = os.getenv("ENV_FILE", ".env")
        extra = "ignore"
