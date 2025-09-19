"""Data models for the Pub/Sub system."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """WebSocket message types."""
    # Client → Server messages
    PUBLISH = "publish"
    SUBSCRIBE = "subscribe" 
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    
    # Server → Client messages
    ACK = "ack"
    EVENT = "event"
    ERROR = "error"
    PONG = "pong"
    INFO = "info"


class MessageContent(BaseModel):
    """Message content structure for publish operations."""
    id: str
    payload: Any


class WebSocketMessage(BaseModel):
    """WebSocket message format."""
    type: MessageType
    topic: Optional[str] = None
    message: Optional[MessageContent] = None
    client_id: Optional[str] = None
    last_n: Optional[int] = Field(default=0, ge=0, le=1000)  # Max 1000 historical messages
    request_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class PublishMessage(BaseModel):
    """Message to be published to a topic."""
    type: MessageType = MessageType.PUBLISH
    topic: str = Field(..., min_length=1, max_length=255)
    message: MessageContent
    request_id: Optional[str] = None


class SubscribeMessage(BaseModel):
    """Subscribe to a topic message."""
    type: MessageType = MessageType.SUBSCRIBE
    topic: str = Field(..., min_length=1, max_length=255)
    client_id: str = Field(..., min_length=1, max_length=100)
    last_n: Optional[int] = Field(default=0, ge=0, le=1000)
    request_id: Optional[str] = None


class UnsubscribeMessage(BaseModel):
    """Unsubscribe from a topic message."""
    type: MessageType = MessageType.UNSUBSCRIBE
    topic: str = Field(..., min_length=1, max_length=255)
    client_id: str = Field(..., min_length=1, max_length=100)
    request_id: Optional[str] = None


class PingMessage(BaseModel):
    """Ping message."""
    type: MessageType = MessageType.PING
    request_id: Optional[str] = None


class HistoricalMessage(BaseModel):
    """Historical message stored for replay."""
    id: str
    topic: str
    payload: Any
    timestamp: datetime
    
    
class TopicInfo(BaseModel):
    """Information about a topic."""
    name: str
    created_at: datetime
    subscriber_count: int = 0
    message_count: int = 0
    last_message_at: Optional[datetime] = None


class TopicStats(BaseModel):
    """Statistics for a topic."""
    name: str
    subscriber_count: int = 0
    total_messages: int = 0
    messages_per_second: float = 0.0
    last_activity: Optional[datetime] = None


class SystemStats(BaseModel):
    """Overall system statistics."""
    total_topics: int = 0
    total_connections: int = 0
    total_messages_sent: int = 0
    uptime_seconds: float = 0.0
    memory_usage_mb: float = 0.0


class HealthStatus(BaseModel):
    """Health check response."""
    status: str = "healthy"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0.0"
    uptime_seconds: float = 0.0


class ErrorResponse(BaseModel):
    """Error response format."""
    error: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CreateTopicRequest(BaseModel):
    """Request to create a new topic."""
    name: str = Field(..., min_length=1, max_length=255)


class TopicListResponse(BaseModel):
    """Response containing list of topics."""
    topics: List[TopicInfo]
    total_count: int
