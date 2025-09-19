"""WebSocket message handlers for pub/sub operations."""
import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core.models import (
    MessageType, 
    WebSocketMessage, 
    PublishMessage, 
    SubscribeMessage, 
    UnsubscribeMessage,
    PingMessage
)
from app.core.pubsub import pubsub_manager
from app.websocket.manager import connection_manager
from app.config import settings

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Handles WebSocket messages for pub/sub operations."""
    
    @staticmethod
    async def handle_publish(websocket: WebSocket, connection_id: str, message_data: Dict[str, Any], request_id: Optional[str] = None):
        """Handle publish message."""
        try:
            # Validate publish message
            publish_msg = PublishMessage(**message_data)
            
            # Check message size
            message_size = len(json.dumps(publish_msg.message.payload))
            if message_size > settings.max_message_size:
                await connection_manager.send_error(
                    websocket, 
                    "MESSAGE_TOO_LARGE",
                    f"Message size ({message_size}) exceeds maximum allowed ({settings.max_message_size})",
                    request_id
                )
                return
                
            # Publish the message
            successful_sends, error_code = await pubsub_manager.publish_to_topic(
                publish_msg.topic, 
                publish_msg.message.id,
                publish_msg.message.payload
            )
            
            if error_code:
                await connection_manager.send_error(
                    websocket,
                    error_code,
                    f"Failed to publish to topic '{publish_msg.topic}': {error_code}",
                    request_id
                )
                return
            
            # Send acknowledgment response
            await connection_manager.send_ack(
                websocket,
                publish_msg.topic,
                "ok",
                request_id
            )
            
            logger.debug(f"Published message to topic '{publish_msg.topic}' "
                        f"reached {successful_sends} subscribers")
                        
        except ValidationError as e:
            await connection_manager.send_error(
                websocket, 
                "VALIDATION_ERROR",
                f"Invalid publish message format: {e}",
                request_id
            )
        except Exception as e:
            logger.error(f"Error handling publish message: {e}")
            await connection_manager.send_error(websocket, "INTERNAL", "Failed to publish message", request_id)
            
    @staticmethod
    async def handle_subscribe(websocket: WebSocket, connection_id: str, message_data: Dict[str, Any], request_id: Optional[str] = None):
        """Handle subscribe message."""
        try:
            logger.info(f"Starting subscription for connection {connection_id}, request_id: {request_id}")
            logger.debug(f"Subscribe message data: {message_data}")
            
            # Validate subscribe message
            try:
                subscribe_msg = SubscribeMessage(**message_data)
                logger.debug(f"Subscribe message validated: topic='{subscribe_msg.topic}', client_id='{subscribe_msg.client_id}', last_n={subscribe_msg.last_n}")
            except Exception as validation_error:
                logger.error(f"Subscribe message validation failed for connection {connection_id}: {validation_error}")
                logger.error(f"Invalid message data: {message_data}")
                raise
            
            # Subscribe to topic
            logger.debug(f"Attempting to subscribe connection {connection_id} to topic '{subscribe_msg.topic}' as client '{subscribe_msg.client_id}'")
            success, error_code = pubsub_manager.subscribe_to_topic(connection_id, subscribe_msg.topic, subscribe_msg.client_id)
            logger.debug(f"Subscription result: success={success}, error_code='{error_code}'")
            
            if success:
                logger.info(f"Successfully subscribed connection {connection_id} (client: {subscribe_msg.client_id}) to topic '{subscribe_msg.topic}'")
                
                # Send historical messages if requested
                if subscribe_msg.last_n and subscribe_msg.last_n > 0:
                    try:
                        logger.debug(f"Fetching {subscribe_msg.last_n} historical messages for topic '{subscribe_msg.topic}'")
                        historical_messages = pubsub_manager.get_historical_messages(
                            subscribe_msg.topic, 
                            subscribe_msg.last_n
                        )
                        logger.debug(f"Found {len(historical_messages)} historical messages")
                        
                        for i, historical_msg in enumerate(historical_messages):
                            try:
                                replay_message = {
                                    "type": "event",
                                    "topic": subscribe_msg.topic,
                                    "message": {
                                        "id": historical_msg.id,
                                        "payload": historical_msg.payload
                                    },
                                    "ts": historical_msg.timestamp.isoformat(),
                                    "replay": True  # Indicate this is a historical message
                                }
                                await websocket.send_json(replay_message)
                                logger.debug(f"Sent historical message {i+1}/{len(historical_messages)} to connection {connection_id}")
                            except Exception as replay_error:
                                logger.error(f"Failed to send historical message {i+1} to connection {connection_id}: {replay_error}")
                                # Continue with other messages instead of failing completely
                    except Exception as history_error:
                        logger.error(f"Failed to fetch/send historical messages for connection {connection_id}: {history_error}")
                        # Don't fail the subscription due to history replay issues
                
                try:
                    await connection_manager.send_ack(
                        websocket,
                        subscribe_msg.topic,
                        "ok",
                        request_id
                    )
                    logger.info(f"✅ Subscription successful: connection {connection_id} (client: {subscribe_msg.client_id}) → topic '{subscribe_msg.topic}'")
                except Exception as ack_error:
                    logger.error(f"Failed to send subscription ack to connection {connection_id}: {ack_error}")
                    # The subscription was successful in the backend, just the ack failed
                    raise ack_error
            else:
                logger.warning(f"❌ Subscription failed: connection {connection_id} (client: {subscribe_msg.client_id}) → topic '{subscribe_msg.topic}' | Error: {error_code}")
                
                error_message = {
                    "TOPIC_NOT_FOUND": f"Topic '{subscribe_msg.topic}' does not exist",
                    "SLOW_CONSUMER": f"Cannot subscribe: connection queue is full (slow consumer)",
                    "SUBSCRIPTION_FAILED": f"Failed to subscribe to topic '{subscribe_msg.topic}'. Topic may have reached maximum subscribers.",
                    "INTERNAL": "Internal server error during subscription"
                }.get(error_code, f"Failed to subscribe to topic '{subscribe_msg.topic}'")
                
                # Log detailed error information
                if error_code == "INTERNAL":
                    logger.error(f"INTERNAL ERROR during subscription:")
                    logger.error(f"  Connection ID: {connection_id}")
                    logger.error(f"  Client ID: {subscribe_msg.client_id}")
                    logger.error(f"  Topic: {subscribe_msg.topic}")
                    logger.error(f"  Request ID: {request_id}")
                    # Get more details from pubsub manager
                    try:
                        connection = pubsub_manager.connections.get(connection_id)
                        if connection:
                            logger.error(f"  Connection exists: YES")
                            logger.error(f"  Connection queue size: {len(connection.message_queue)}")
                            logger.error(f"  Connection dropped messages: {connection.dropped_messages}")
                            logger.error(f"  Connection should disconnect: {connection.should_disconnect}")
                        else:
                            logger.error(f"  Connection exists: NO - Connection not found in manager!")
                        
                        # Check topic existence
                        topic = pubsub_manager.topics.get(subscribe_msg.topic)
                        if topic:
                            logger.error(f"  Topic exists: YES")
                            logger.error(f"  Topic subscriber count: {len(topic.subscribers)}")
                        else:
                            logger.error(f"  Topic exists: NO")
                            
                    except Exception as debug_error:
                        logger.error(f"  Failed to get debug info: {debug_error}")
                
                try:
                    await connection_manager.send_error(
                        websocket, 
                        error_code,
                        error_message,
                        request_id
                    )
                except Exception as error_send_failure:
                    logger.error(f"Failed to send error response to connection {connection_id}: {error_send_failure}")
                    # This is a critical issue - we can't even send error responses
                
        except ValidationError as e:
            logger.warning(f"Validation error in subscription for connection {connection_id}: {e}")
            logger.debug(f"Invalid message data that caused validation error: {message_data}")
            await connection_manager.send_error(
                websocket, 
                "VALIDATION_ERROR",
                f"Invalid subscribe message format: {e}",
                request_id
            )
        except Exception as e:
            logger.error(f"🚨 UNEXPECTED ERROR handling subscribe message:")
            logger.error(f"  Connection ID: {connection_id}")
            logger.error(f"  Request ID: {request_id}")
            logger.error(f"  Exception Type: {type(e).__name__}")
            logger.error(f"  Exception Message: {e}")
            logger.error(f"  Message Data: {message_data}")
            
            # Log stack trace for debugging
            import traceback
            logger.error(f"  Stack Trace:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    logger.error(f"    {line}")
            
            # Try to get connection info for debugging
            try:
                connection = pubsub_manager.connections.get(connection_id)
                if connection:
                    logger.error(f"  Connection Status: Active")
                    logger.error(f"  Client ID: {connection.client_id}")
                    logger.error(f"  Subscribed Topics: {list(connection.subscribed_topics)}")
                else:
                    logger.error(f"  Connection Status: NOT FOUND")
            except Exception as debug_error:
                logger.error(f"  Failed to get connection debug info: {debug_error}")
            
            await connection_manager.send_error(websocket, "INTERNAL", "Failed to subscribe to topic", request_id)
            
    @staticmethod
    async def handle_unsubscribe(websocket: WebSocket, connection_id: str, message_data: Dict[str, Any], request_id: Optional[str] = None):
        """Handle unsubscribe message."""
        try:
            # Validate unsubscribe message
            unsubscribe_msg = UnsubscribeMessage(**message_data)
            
            # Unsubscribe from topic
            success = pubsub_manager.unsubscribe_from_topic(connection_id, unsubscribe_msg.topic)
            
            if success:
                await connection_manager.send_ack(
                    websocket,
                    unsubscribe_msg.topic,
                    "ok",
                    request_id
                )
                logger.debug(f"Connection {connection_id} (client: {unsubscribe_msg.client_id}) unsubscribed from topic '{unsubscribe_msg.topic}'")
            else:
                await connection_manager.send_error(
                    websocket, 
                    "UNSUBSCRIBE_FAILED",
                    f"Failed to unsubscribe from topic '{unsubscribe_msg.topic}'. You may not be subscribed to this topic.",
                    request_id
                )
                
        except ValidationError as e:
            await connection_manager.send_error(
                websocket, 
                "VALIDATION_ERROR",
                f"Invalid unsubscribe message format: {e}",
                request_id
            )
        except Exception as e:
            logger.error(f"Error handling unsubscribe message: {e}")
            await connection_manager.send_error(websocket, "INTERNAL", "Failed to unsubscribe from topic", request_id)
            
    @staticmethod
    async def handle_ping(websocket: WebSocket, connection_id: str, request_id: Optional[str] = None):
        """Handle ping message."""
        await connection_manager.handle_ping(websocket, connection_id, request_id)
        
    @staticmethod
    async def handle_message(websocket: WebSocket, connection_id: str, raw_message: str):
        """Route incoming WebSocket messages to appropriate handlers."""
        try:
            # Parse JSON message
            message_dict = json.loads(raw_message)
            
            # Validate basic message structure
            if "type" not in message_dict:
                await connection_manager.send_error(websocket, "BAD_REQUEST", "Message type is required")
                return
                
            message_type = message_dict["type"]
            request_id = message_dict.get("request_id")
            
            # Check for authentication if needed (placeholder for future auth implementation)
            # TODO: Implement authentication logic here
            # auth_token = message_dict.get("auth_token")
            # if not WebSocketHandler.is_authorized(auth_token):
            #     await connection_manager.send_error(
            #         websocket, "UNAUTHORIZED", "Invalid or missing authentication", request_id
            #     )
            #     return
            
            # Check if connection is slow consumer before processing heavy operations
            connection = pubsub_manager.connections.get(connection_id)
            if connection and connection.is_slow_consumer() and message_type in [MessageType.SUBSCRIBE]:
                await connection_manager.send_error(
                    websocket, 
                    "SLOW_CONSUMER",
                    "Connection queue is full. Please consume messages faster.",
                    request_id
                )
                return
            
            # Route message based on type
            if message_type == MessageType.PUBLISH:
                await WebSocketHandler.handle_publish(websocket, connection_id, message_dict, request_id)
            elif message_type == MessageType.SUBSCRIBE:
                await WebSocketHandler.handle_subscribe(websocket, connection_id, message_dict, request_id)
            elif message_type == MessageType.UNSUBSCRIBE:
                await WebSocketHandler.handle_unsubscribe(websocket, connection_id, message_dict, request_id)
            elif message_type == MessageType.PING:
                await WebSocketHandler.handle_ping(websocket, connection_id, request_id)
            else:
                await connection_manager.send_error(
                    websocket, 
                    "UNKNOWN_MESSAGE_TYPE",
                    f"Unknown message type: {message_type}",
                    request_id
                )
                
        except json.JSONDecodeError:
            await connection_manager.send_error(
                websocket, 
                "JSON_ERROR",
                "Invalid JSON format"
            )
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
            await connection_manager.send_error(websocket, "INTERNAL", "Internal server error")


# Global handler instance
websocket_handler = WebSocketHandler()
