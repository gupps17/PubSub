"""WebSocket connection manager."""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.core.pubsub import pubsub_manager
from app.config import settings

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and handles connection lifecycle."""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        
    async def connect(self, websocket: WebSocket) -> Optional[str]:
        """Accept a new WebSocket connection."""
        logger.debug(f"Attempting to establish new WebSocket connection")
        
        try:
            logger.debug(f"Accepting WebSocket connection...")
            await websocket.accept()
            logger.debug(f"WebSocket accepted, creating connection object...")
            
            connection = pubsub_manager.add_connection(websocket)
            logger.debug(f"Connection created with ID: {connection.connection_id}")
            
            self.active_connections[connection.connection_id] = websocket
            logger.info(f"✅ New WebSocket connection established: {connection.connection_id} (total active: {len(self.active_connections)})")
            return connection.connection_id
            
        except ValueError as e:
            logger.error(f"❌ Failed to establish connection (ValueError): {e}")
            logger.error(f"  This usually means max connections exceeded or server shutting down")
            try:
                await websocket.close(code=1008, reason=str(e))
            except Exception as close_error:
                logger.error(f"  Also failed to close websocket: {close_error}")
            return None
        except Exception as e:
            logger.error(f"🚨 Unexpected error during connection establishment:")
            logger.error(f"  Exception Type: {type(e).__name__}")
            logger.error(f"  Exception Message: {e}")
            
            # Log stack trace for debugging
            import traceback
            logger.error(f"  Stack Trace:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    logger.error(f"    {line}")
            
            try:
                await websocket.close(code=1011, reason="Internal server error")
            except Exception as close_error:
                logger.error(f"  Also failed to close websocket: {close_error}")
            return None
            
    async def disconnect(self, connection_id: str):
        """Handle WebSocket disconnection."""
        try:
            # Remove from pub/sub manager
            pubsub_manager.remove_connection(connection_id)
            
            # Remove from active connections
            if connection_id in self.active_connections:
                del self.active_connections[connection_id]
                
            logger.info(f"WebSocket connection disconnected: {connection_id}")
            
        except Exception as e:
            logger.error(f"Error during disconnect for {connection_id}: {e}")
            
    async def send_error(self, websocket: WebSocket, error_code: str, error_message: str, request_id: Optional[str] = None):
        """Send an error message to a WebSocket client."""
        try:
            response = {
                "type": "error",
                "error": {
                    "code": error_code,
                    "message": error_message
                },
                "ts": datetime.utcnow().isoformat()
            }
            if request_id:
                response["request_id"] = request_id
                
            await websocket.send_json(response)
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")
            
    async def send_ack(self, websocket: WebSocket, topic: str, status: str = "ok", request_id: Optional[str] = None):
        """Send an acknowledgment message to a WebSocket client."""
        try:
            response = {
                "type": "ack",
                "topic": topic,
                "status": status,
                "ts": datetime.utcnow().isoformat()
            }
            if request_id:
                response["request_id"] = request_id
                
            await websocket.send_json(response)
        except Exception as e:
            logger.error(f"Failed to send ack message: {e}")
            
    async def send_info(self, websocket: WebSocket, message: str, data: Optional[Dict] = None, request_id: Optional[str] = None):
        """Send an info message to a WebSocket client."""
        try:
            response = {
                "type": "info",
                "message": message,
                "ts": datetime.utcnow().isoformat()
            }
            if data:
                response["data"] = data
            if request_id:
                response["request_id"] = request_id
                
            await websocket.send_json(response)
        except Exception as e:
            logger.error(f"Failed to send info message: {e}")
            
    async def handle_ping(self, websocket: WebSocket, connection_id: str, request_id: Optional[str] = None):
        """Handle ping messages and respond with pong."""
        try:
            # Update last ping time
            connection = pubsub_manager.connections.get(connection_id)
            if connection:
                connection.last_ping = datetime.utcnow()
            
            response = {
                "type": "pong",
                "ts": datetime.utcnow().isoformat()
            }
            if request_id:
                response["request_id"] = request_id
                
            await websocket.send_json(response)
        except Exception as e:
            logger.error(f"Failed to handle ping for {connection_id}: {e}")
            
    async def cleanup_stale_connections(self):
        """Periodically clean up stale connections (could be called by a background task)."""
        try:
            current_time = datetime.utcnow()
            stale_connections = []
            
            for connection_id, connection in pubsub_manager.connections.items():
                time_since_ping = (current_time - connection.last_ping).total_seconds()
                if time_since_ping > settings.websocket_timeout:
                    stale_connections.append(connection_id)
                    
            for connection_id in stale_connections:
                await self.disconnect(connection_id)
                logger.info(f"Cleaned up stale connection: {connection_id}")
                
        except Exception as e:
            logger.error(f"Error during cleanup of stale connections: {e}")


# Global connection manager instance
connection_manager = ConnectionManager()
