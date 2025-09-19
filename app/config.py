"""Configuration management for the Pub/Sub system."""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Server configuration
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    # WebSocket configuration
    websocket_timeout: int = 60
    websocket_ping_interval: int = 30
    max_connections: int = 1000
    
    # Pub/Sub configuration
    max_topics: int = 1000
    max_subscribers_per_topic: int = 100
    max_message_size: int = 1024 * 1024  # 1MB
    message_queue_size: int = 1000
    auto_create_topics: bool = True  # Auto-create topics on first use
    
    # Backpressure configuration
    backpressure_strategy: str = "drop_oldest"  # "drop_oldest", "drop_new", "disconnect"
    slow_consumer_threshold: float = 0.8  # Queue % full to consider slow consumer
    max_dropped_messages: int = 100  # Max dropped messages before disconnecting
    
    # Security
    cors_origins: list[str] = ["*"]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()
