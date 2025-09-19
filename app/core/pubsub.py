"""Core Pub/Sub system with thread-safe operations."""
import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from threading import RLock
from fastapi import WebSocket

from app.config import settings
from app.core.models import TopicInfo, TopicStats, SystemStats, HistoricalMessage

logger = logging.getLogger(__name__)


class Connection:
    """Represents a WebSocket connection."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.connection_id = str(uuid.uuid4())
        self.client_id: Optional[str] = None  # Set when client subscribes
        self.subscribed_topics: Set[str] = set()
        self.connected_at = datetime.utcnow()
        self.last_ping = datetime.utcnow()
        self.message_queue: deque = deque(maxlen=settings.message_queue_size)
        self.dropped_messages = 0  # Count of messages dropped due to slow consumer
        self.should_disconnect = False  # Flag for disconnect due to backpressure
        self._lock = RLock()  # Per-connection lock for thread safety
        
    async def send_message(self, message: Dict[str, Any]) -> bool:
        """Send message to this connection with backpressure handling."""
        with self._lock:
            try:
                # Check if we should disconnect this connection
                if self.should_disconnect:
                    return False
                    
                # Check queue status
                queue_full = len(self.message_queue) >= settings.message_queue_size
                
                if queue_full:
                    # Handle backpressure based on strategy
                    if settings.backpressure_strategy == "drop_new":
                        self.dropped_messages += 1
                        logger.warning(f"Slow consumer {self.connection_id}: dropping new message (total dropped: {self.dropped_messages})")
                        
                        # Check if we should disconnect due to too many drops
                        if self.dropped_messages >= settings.max_dropped_messages:
                            self.should_disconnect = True
                            logger.error(f"Disconnecting slow consumer {self.connection_id}: too many dropped messages ({self.dropped_messages})")
                        return False
                        
                    elif settings.backpressure_strategy == "drop_oldest":
                        # Remove oldest message to make room
                        if self.message_queue:
                            self.message_queue.popleft()
                        self.dropped_messages += 1
                        logger.warning(f"Slow consumer {self.connection_id}: dropping oldest message (total dropped: {self.dropped_messages})")
                        
                        # Check if we should disconnect due to too many drops
                        if self.dropped_messages >= settings.max_dropped_messages:
                            self.should_disconnect = True
                            logger.error(f"Disconnecting slow consumer {self.connection_id}: too many dropped messages ({self.dropped_messages})")
                            
                    elif settings.backpressure_strategy == "disconnect":
                        self.should_disconnect = True
                        logger.error(f"Disconnecting slow consumer {self.connection_id}: queue overflow")
                        return False
                
                # Add to queue and send
                self.message_queue.append(message)
                await self.websocket.send_json(message)
                return True
                
            except Exception as e:
                logger.error(f"Failed to send message to {self.connection_id}: {e}")
                self.should_disconnect = True
                return False
            
    def is_slow_consumer(self) -> bool:
        """Check if this connection is a slow consumer."""
        with self._lock:
            return len(self.message_queue) >= settings.message_queue_size * settings.slow_consumer_threshold


class Topic:
    """Represents a topic with its subscribers."""
    
    def __init__(self, name: str):
        self.name = name
        self.created_at = datetime.utcnow()
        self.subscribers: Set[Connection] = set()
        self.message_count = 0
        self.last_message_at: Optional[datetime] = None
        self.message_history: deque = deque(maxlen=settings.message_queue_size)  # Store historical messages
        self._lock = RLock()
        
    def add_subscriber(self, connection: Connection, client_id: str) -> bool:
        """Add a subscriber to this topic."""
        with self._lock:
            if len(self.subscribers) >= settings.max_subscribers_per_topic:
                return False
            self.subscribers.add(connection)
            connection.subscribed_topics.add(self.name)
            connection.client_id = client_id  # Set or update client ID
            return True
            
    def remove_subscriber(self, connection: Connection) -> bool:
        """Remove a subscriber from this topic."""
        with self._lock:
            if connection in self.subscribers:
                self.subscribers.discard(connection)
                connection.subscribed_topics.discard(self.name)
                return True
            return False
    
    def get_historical_messages(self, last_n: int = 0) -> List[HistoricalMessage]:
        """Get the last N historical messages."""
        with self._lock:
            if last_n <= 0 or not self.message_history:
                return []
            
            # Get last N messages (or all if less than N available)
            messages_to_return = list(self.message_history)[-last_n:]
            return messages_to_return
            
    async def publish_message(self, message_id: str, payload: Any) -> int:
        """Publish a message to all subscribers."""
        with self._lock:
            if not self.subscribers:
                # Still store the message in history even if no subscribers
                timestamp = datetime.utcnow()
                historical_msg = HistoricalMessage(
                    id=message_id,
                    topic=self.name,
                    payload=payload,
                    timestamp=timestamp
                )
                self.message_history.append(historical_msg)
                self.message_count += 1
                self.last_message_at = timestamp
                return 0
                
            self.message_count += 1
            self.last_message_at = datetime.utcnow()
            
            # Store message in history
            historical_msg = HistoricalMessage(
                id=message_id,
                topic=self.name,
                payload=payload,
                timestamp=self.last_message_at
            )
            self.message_history.append(historical_msg)
            
            message = {
                "type": "event",
                "topic": self.name,
                "message": {
                    "id": message_id,
                    "payload": payload
                },
                "ts": self.last_message_at.isoformat()
            }
            
            # Create a copy of subscribers to avoid modification during iteration
            subscribers_copy = list(self.subscribers)
            
        # Send to all subscribers (outside the lock to avoid blocking)
        successful_sends = 0
        failed_connections = []
        slow_consumer_connections = []
        
        for subscriber in subscribers_copy:
            success = await subscriber.send_message(message)
            if success:
                successful_sends += 1
            else:
                failed_connections.append(subscriber)
                # Check if this is a slow consumer that should be disconnected
                if subscriber.should_disconnect:
                    slow_consumer_connections.append(subscriber)
        
        # Clean up failed connections and notify about slow consumers
        if failed_connections:
            with self._lock:
                for connection in failed_connections:
                    self.subscribers.discard(connection)
                    connection.subscribed_topics.discard(self.name)
                    
        # Log slow consumer disconnections
        for connection in slow_consumer_connections:
            logger.info(f"Removed slow consumer {connection.connection_id} from topic '{self.name}'")
                    
        return successful_sends
        
    def get_info(self) -> TopicInfo:
        """Get topic information."""
        with self._lock:
            return TopicInfo(
                name=self.name,
                created_at=self.created_at,
                subscriber_count=len(self.subscribers),
                message_count=self.message_count,
                last_message_at=self.last_message_at
            )


class PubSubManager:
    """Thread-safe Pub/Sub manager."""
    
    def __init__(self):
        self.topics: Dict[str, Topic] = {}
        self.connections: Dict[str, Connection] = {}
        self._lock = RLock()
        self.start_time = time.time()
        self.total_messages_sent = 0
        self.shutdown_requested = False
        
    def add_connection(self, websocket: WebSocket) -> Connection:
        """Add a new WebSocket connection."""
        with self._lock:
            if self.shutdown_requested:
                raise ValueError("Server is shutting down")
            if len(self.connections) >= settings.max_connections:
                raise ValueError("Maximum connections exceeded")
                
            connection = Connection(websocket)
            self.connections[connection.connection_id] = connection
            logger.debug(f"Added connection {connection.connection_id} (total: {len(self.connections)})")
            return connection
        
    def remove_connection(self, connection_id: str) -> bool:
        """Remove a WebSocket connection and clean up subscriptions."""
        with self._lock:
            connection = self.connections.get(connection_id)
            if not connection:
                return False
                
            # Remove from all subscribed topics
            for topic_name in list(connection.subscribed_topics):
                topic = self.topics.get(topic_name)
                if topic:
                    topic.remove_subscriber(connection)
                    
            # Remove the connection
            del self.connections[connection_id]
            return True
            
    def create_topic(self, topic_name: str) -> bool:
        """Create a new topic."""
        with self._lock:
            if len(self.topics) >= settings.max_topics:
                return False
            if topic_name in self.topics:
                return False
            self.topics[topic_name] = Topic(topic_name)
            return True
            
    def delete_topic(self, topic_name: str) -> bool:
        """Delete a topic and unsubscribe all connections."""
        with self._lock:
            topic = self.topics.get(topic_name)
            if not topic:
                return False
                
            # Remove all subscribers
            for connection in list(topic.subscribers):
                topic.remove_subscriber(connection)
                
            # Delete the topic
            del self.topics[topic_name]
            return True
            
    def subscribe_to_topic(self, connection_id: str, topic_name: str, client_id: str) -> tuple[bool, str]:
        """Subscribe a connection to a topic. Returns (success, error_code)."""
        logger.debug(f"PubSubManager.subscribe_to_topic: connection_id='{connection_id}', topic='{topic_name}', client_id='{client_id}'")
        
        try:
            with self._lock:
                # Check if connection exists
                connection = self.connections.get(connection_id)
                if not connection:
                    logger.error(f"INTERNAL ERROR in subscribe_to_topic: Connection '{connection_id}' not found!")
                    logger.error(f"  Available connections: {list(self.connections.keys())}")
                    logger.error(f"  Total connections: {len(self.connections)}")
                    return False, "INTERNAL"
                
                logger.debug(f"Connection found: {connection_id} (client_id: {connection.client_id})")
                
                # Check/create topic
                topic = self.topics.get(topic_name)
                if not topic:
                    logger.debug(f"Topic '{topic_name}' not found")
                    if settings.auto_create_topics:
                        logger.debug(f"Auto-creating topic '{topic_name}'")
                        if not self.create_topic(topic_name):
                            logger.error(f"INTERNAL ERROR: Failed to auto-create topic '{topic_name}'")
                            logger.error(f"  Current topic count: {len(self.topics)}")
                            logger.error(f"  Max topics: {settings.max_topics}")
                            return False, "INTERNAL"
                        topic = self.topics[topic_name]
                        logger.info(f"Successfully auto-created topic '{topic_name}'")
                    else:
                        logger.warning(f"Topic '{topic_name}' not found and auto-create disabled")
                        return False, "TOPIC_NOT_FOUND"
                
                logger.debug(f"Topic '{topic_name}' available with {len(topic.subscribers)} subscribers")
                
                # Check if connection is slow consumer
                if connection.is_slow_consumer():
                    logger.warning(f"Connection {connection_id} rejected as slow consumer (queue: {len(connection.message_queue)}/{settings.message_queue_size})")
                    return False, "SLOW_CONSUMER"
                
                # Check topic capacity
                if len(topic.subscribers) >= settings.max_subscribers_per_topic:
                    logger.warning(f"Topic '{topic_name}' at max capacity: {len(topic.subscribers)}/{settings.max_subscribers_per_topic}")
                    return False, "SUBSCRIPTION_FAILED"
                
                # Attempt to add subscriber
                logger.debug(f"Adding subscriber to topic '{topic_name}'")
                success = topic.add_subscriber(connection, client_id)
                
                if success:
                    logger.info(f"✅ Successfully added subscriber: connection {connection_id} (client: {client_id}) → topic '{topic_name}' (total subscribers: {len(topic.subscribers)})")
                    return True, ""
                else:
                    logger.error(f"❌ Failed to add subscriber to topic '{topic_name}' for unknown reason")
                    logger.error(f"  Topic subscriber count: {len(topic.subscribers)}")
                    logger.error(f"  Max subscribers per topic: {settings.max_subscribers_per_topic}")
                    return False, "SUBSCRIPTION_FAILED"
                
        except Exception as e:
            logger.error(f"🚨 UNEXPECTED ERROR in subscribe_to_topic:")
            logger.error(f"  Connection ID: {connection_id}")
            logger.error(f"  Topic: {topic_name}")
            logger.error(f"  Client ID: {client_id}")
            logger.error(f"  Exception: {type(e).__name__}: {e}")
            
            # Log stack trace
            import traceback
            logger.error(f"  Stack Trace:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    logger.error(f"    {line}")
            
            return False, "INTERNAL"
            
    def unsubscribe_from_topic(self, connection_id: str, topic_name: str) -> bool:
        """Unsubscribe a connection from a topic."""
        with self._lock:
            connection = self.connections.get(connection_id)
            topic = self.topics.get(topic_name)
            
            if not connection or not topic:
                return False
                
            return topic.remove_subscriber(connection)
            
    async def publish_to_topic(self, topic_name: str, message_id: str, payload: Any) -> tuple[int, str]:
        """Publish a message to a topic. Returns (successful_sends, error_code)."""
        with self._lock:
            topic = self.topics.get(topic_name)
            if not topic:
                if settings.auto_create_topics:
                    # Auto-create topic if it doesn't exist
                    if not self.create_topic(topic_name):
                        return 0, "INTERNAL"
                    topic = self.topics[topic_name]
                else:
                    return 0, "TOPIC_NOT_FOUND"
                
        successful_sends = await topic.publish_message(message_id, payload)
        with self._lock:
            self.total_messages_sent += successful_sends
        return successful_sends, ""
        
    def get_historical_messages(self, topic_name: str, last_n: int = 0) -> List[HistoricalMessage]:
        """Get historical messages for a topic."""
        with self._lock:
            topic = self.topics.get(topic_name)
            if not topic:
                return []
            return topic.get_historical_messages(last_n)
        
    def get_topics(self) -> List[TopicInfo]:
        """Get list of all topics."""
        with self._lock:
            return [topic.get_info() for topic in self.topics.values()]
            
    def get_topic_stats(self, topic_name: str) -> Optional[TopicStats]:
        """Get statistics for a specific topic."""
        with self._lock:
            topic = self.topics.get(topic_name)
            if not topic:
                return None
                
            return TopicStats(
                name=topic.name,
                subscriber_count=len(topic.subscribers),
                total_messages=topic.message_count,
                messages_per_second=0.0,  # Could implement moving average
                last_activity=topic.last_message_at
            )
            
    def get_system_stats(self) -> SystemStats:
        """Get overall system statistics."""
        import psutil
        import os
        
        with self._lock:
            uptime = time.time() - self.start_time
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            return SystemStats(
                total_topics=len(self.topics),
                total_connections=len(self.connections),
                total_messages_sent=self.total_messages_sent,
                uptime_seconds=uptime,
                memory_usage_mb=memory_mb
            )
            
    async def cleanup_slow_consumers(self) -> int:
        """Clean up connections marked for disconnection due to slow consumption."""
        disconnected_count = 0
        connections_to_remove = []
        
        with self._lock:
            for connection_id, connection in self.connections.items():
                if connection.should_disconnect:
                    connections_to_remove.append(connection_id)
                    
        # Remove connections outside the main lock to avoid blocking
        for connection_id in connections_to_remove:
            if self.remove_connection(connection_id):
                disconnected_count += 1
                logger.info(f"Disconnected slow consumer: {connection_id}")
                
        return disconnected_count
        
    def get_slow_consumer_stats(self) -> Dict[str, Any]:
        """Get statistics about slow consumers."""
        with self._lock:
            slow_consumers = []
            total_dropped = 0
            
            for connection in self.connections.values():
                if connection.is_slow_consumer() or connection.dropped_messages > 0:
                    slow_consumers.append({
                        "connection_id": connection.connection_id,
                        "client_id": connection.client_id,
                        "queue_size": len(connection.message_queue),
                        "dropped_messages": connection.dropped_messages,
                        "should_disconnect": connection.should_disconnect,
                        "subscribed_topics": list(connection.subscribed_topics)
                    })
                    total_dropped += connection.dropped_messages
                    
            return {
                "slow_consumer_count": len(slow_consumers),
                "total_dropped_messages": total_dropped,
                "slow_consumers": slow_consumers
            }
            
    async def graceful_shutdown(self, timeout_seconds: int = 10):
        """Perform graceful shutdown of the pub/sub system."""
        logger.info("Starting graceful shutdown...")
        
        # Stop accepting new connections
        with self._lock:
            self.shutdown_requested = True
            connection_ids = list(self.connections.keys())
            topic_names = list(self.topics.keys())
            
        logger.info(f"Graceful shutdown: {len(connection_ids)} connections, {len(topic_names)} topics")
        
        # Give connections time to finish processing
        await asyncio.sleep(min(2.0, timeout_seconds / 2))
        
        # Close all WebSocket connections
        disconnected = 0
        for connection_id in connection_ids:
            try:
                connection = self.connections.get(connection_id)
                if connection:
                    await connection.websocket.close()
                    self.remove_connection(connection_id)
                    disconnected += 1
            except Exception as e:
                logger.error(f"Error closing connection {connection_id}: {e}")
                
        # Clear all topics
        with self._lock:
            for topic_name in topic_names:
                self.delete_topic(topic_name)
                
        logger.info(f"Graceful shutdown completed: {disconnected} connections closed, {len(topic_names)} topics cleared")
        
    def ensure_topic_isolation(self) -> Dict[str, Any]:
        """Verify topic isolation - no cross-topic leakage."""
        isolation_report = {
            "topics_checked": 0,
            "connections_checked": 0,
            "isolation_violations": [],
            "is_isolated": True
        }
        
        with self._lock:
            # Check each topic's subscribers are properly isolated
            for topic_name, topic in self.topics.items():
                isolation_report["topics_checked"] += 1
                
                for subscriber in topic.subscribers:
                    isolation_report["connections_checked"] += 1
                    
                    # Verify subscriber knows it's subscribed to this topic
                    if topic_name not in subscriber.subscribed_topics:
                        isolation_report["isolation_violations"].append({
                            "type": "missing_subscription",
                            "connection_id": subscriber.connection_id,
                            "topic": topic_name
                        })
                        isolation_report["is_isolated"] = False
                        
            # Check each connection's subscriptions are valid
            for connection_id, connection in self.connections.items():
                for subscribed_topic in connection.subscribed_topics:
                    topic = self.topics.get(subscribed_topic)
                    if not topic or connection not in topic.subscribers:
                        isolation_report["isolation_violations"].append({
                            "type": "orphaned_subscription",
                            "connection_id": connection_id,
                            "topic": subscribed_topic
                        })
                        isolation_report["is_isolated"] = False
                        
        return isolation_report


# Global pub/sub manager instance
pubsub_manager = PubSubManager()
