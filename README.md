# In-Memory Pub/Sub System

A simplified in-memory Pub/Sub system built with Python and FastAPI that provides both WebSocket and REST API interfaces for real-time messaging and topic management.

## Features

- **WebSocket Interface**: Real-time publishing and subscribing via `/ws` endpoint
- **REST API**: Topic management and system observability via HTTP endpoints
- **Thread-Safe Operations**: Handles multiple publishers and subscribers safely
- **In-Memory Storage**: No external dependencies (Redis, Kafka, etc.)
- **Auto-Scaling Topics**: Topics are created automatically when first used
- **Connection Management**: Automatic cleanup of stale connections
- **Health Monitoring**: Built-in health checks and system statistics
- **Containerized**: Docker support for easy deployment

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                     │
├─────────────────────────────────────────────────────────────┤
│  WebSocket Endpoint (/ws)           REST API Endpoints      │
│  ├── publish                        ├── /api/health         │
│  ├── subscribe                      ├── /api/stats          │
│  ├── unsubscribe                    ├── /api/topics/        │
│  └── ping                           └── /api/topics/{name}  │
├─────────────────────────────────────────────────────────────┤
│                    Core Pub/Sub Manager                     │
│  ├── Thread-safe topic management                          │
│  ├── Connection lifecycle management                       │
│  ├── Message routing and delivery                          │
│  └── Statistics collection                                 │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Using Docker (Recommended)

1. Build and run the container:
```bash
docker build -t pubsub-system .
docker run -p 8000:8000 pubsub-system
```

### Local Development

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\\Scripts\\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env file as needed
```

4. Run the application:
```bash
python run.py
```

The server will start at `http://localhost:8000`

## API Documentation

### WebSocket Interface (`ws://localhost:8000/ws`)

The WebSocket endpoint supports the following message types:

#### 1. Publish Message
```json
{
    "type": "publish",
    "topic": "orders",
    "message": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "payload": {
            "order_id": "12345",
            "status": "confirmed",
            "amount": 99.99
        }
    },
    "request_id": "uuid-optional"
}
```

#### 2. Subscribe to Topic
```json
{
    "type": "subscribe",
    "topic": "orders",
    "client_id": "s1",
    "last_n": 5,
    "request_id": "uuid-optional"
}
```

#### 3. Unsubscribe from Topic
```json
{
    "type": "unsubscribe",
    "topic": "orders",
    "client_id": "s1",
    "request_id": "uuid-optional"
}
```

#### 4. Ping/Pong
```json
{
    "type": "ping",
    "request_id": "uuid-optional"
}
```

#### Server → Client Response Messages

The server responds with one of these message types: `"ack" | "event" | "error" | "pong" | "info"`

- **Acknowledgment (ack)** - Confirms successful operations:
```json
{
    "type": "ack",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "topic": "orders",
    "status": "ok",
    "ts": "2025-08-25T10:00:00Z"
}
```

- **Event** - Published message delivered to subscribers:
```json
{
    "type": "event",
    "topic": "orders",
    "message": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "payload": {
            "order_id": "ORD-123",
            "amount": 99.5,
            "currency": "USD"
        }
    },
    "ts": "2025-08-25T10:01:00Z"
}
```

- **Historical Event (Replay)** - Past messages during subscription:
```json
{
    "type": "event",
    "topic": "orders",
    "message": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "payload": {
            "order_id": "ORD-123",
            "amount": 99.5
        }
    },
    "ts": "2025-08-25T09:30:00Z",
    "replay": true
}
```

- **Pong Response**:
```json
{
    "type": "pong",
    "request_id": "ping-123",
    "ts": "2025-08-25T10:00:00Z"
}
```

- **Error Response** - Validation or flow errors:
```json
{
    "type": "error",
    "request_id": "req-67890",
    "error": {
        "code": "BAD_REQUEST",
        "message": "message.id must be a valid UUID"
    },
    "ts": "2025-08-25T10:02:00Z"
}
```

- **Info Message** - Additional information:
```json
{
    "type": "info",
    "message": "Connection established",
    "request_id": "optional-id",
    "ts": "2025-08-25T10:00:00Z"
}
```

#### Error Codes

| Error Code | Description | When it occurs |
|------------|-------------|----------------|
| `BAD_REQUEST` | Missing required fields or malformed request | Missing `type` field in message |
| `VALIDATION_ERROR` | Pydantic validation failed | Invalid message structure, field constraints violated |
| `MESSAGE_TOO_LARGE` | Message exceeds size limit | Payload size > MAX_MESSAGE_SIZE (1MB default) |
| `TOPIC_NOT_FOUND` | Topic does not exist | Publish/subscribe to non-existent topic (when auto-create disabled) |
| `SUBSCRIPTION_FAILED` | Cannot subscribe (topic full) | Topic reached MAX_SUBSCRIBERS_PER_TOPIC limit |
| `UNSUBSCRIBE_FAILED` | Not subscribed to topic | Trying to unsubscribe from topic you're not subscribed to |
| `SLOW_CONSUMER` | Subscriber queue overflow | Connection message queue is full (80%+ capacity) |
| `UNAUTHORIZED` | Invalid/missing authentication | Invalid auth token (when auth is implemented) |
| `UNKNOWN_MESSAGE_TYPE` | Invalid message type | Message type not in: publish, subscribe, unsubscribe, ping |
| `JSON_ERROR` | Invalid JSON format | Malformed JSON in WebSocket message |
| `INTERNAL` | Unexpected server error | Server-side exceptions, database errors |

### REST API Endpoints

#### Health & Monitoring

- **GET** `/api/health` - Health check
- **GET** `/api/stats` - System statistics
- **GET** `/api/metrics` - Metrics in key-value format
- **GET** `/api/slow-consumers` - Slow consumer statistics and details
- **GET** `/api/isolation-check` - Verify topic isolation integrity
- **POST** `/api/cleanup-slow-consumers` - Manually cleanup slow consumers

#### Topic Management

- **GET** `/api/topics/` - List all topics
- **POST** `/api/topics/` - Create a new topic
- **GET** `/api/topics/{name}` - Get topic information
- **DELETE** `/api/topics/{name}` - Delete a topic
- **GET** `/api/topics/{name}/stats` - Get topic statistics

## Configuration

The system can be configured using environment variables. Copy `.env.example` to `.env` and modify as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable debug mode |
| `WEBSOCKET_TIMEOUT` | `60` | WebSocket connection timeout (seconds) |
| `WEBSOCKET_PING_INTERVAL` | `30` | Ping interval for connection health (seconds) |
| `MAX_CONNECTIONS` | `1000` | Maximum concurrent WebSocket connections |
| `MAX_TOPICS` | `1000` | Maximum number of topics |
| `MAX_SUBSCRIBERS_PER_TOPIC` | `100` | Maximum subscribers per topic |
| `MAX_MESSAGE_SIZE` | `1048576` | Maximum message size (bytes) |
| `MESSAGE_QUEUE_SIZE` | `1000` | Message queue size per connection |
| `AUTO_CREATE_TOPICS` | `true` | Automatically create topics on first use |
| `BACKPRESSURE_STRATEGY` | `drop_oldest` | Backpressure strategy: drop_oldest, drop_new, disconnect |
| `SLOW_CONSUMER_THRESHOLD` | `0.8` | Queue fill ratio to consider slow consumer |
| `MAX_DROPPED_MESSAGES` | `100` | Max dropped messages before disconnecting |
| `CORS_ORIGINS` | `["*"]` | CORS allowed origins |

## Design Decisions & Assumptions

### 1. **In-Memory Storage**
- All data is stored in Python dictionaries and sets
- No persistence across restarts (as required)
- Uses thread-safe operations with RLock for concurrent access

### 2. **Backpressure Policy**
- **Connection Limit**: Maximum 1000 concurrent WebSocket connections
- **Topic Limit**: Maximum 1000 topics system-wide
- **Subscriber Limit**: Maximum 100 subscribers per topic
- **Message Size Limit**: 1MB per message
- **Queue Size**: 1000 messages per connection (not implemented in current version)

### 3. **Auto-Topic Creation**
- Topics are created automatically when first published to or subscribed to
- Simplifies client usage - no need to pre-create topics

### 4. **Connection Management**
- Automatic cleanup of stale connections based on ping timeout
- Failed message deliveries trigger connection cleanup
- Each connection maintains its subscription list

### 5. **Thread Safety**
- Uses RLock (reentrant locks) for thread-safe operations
- Separate locks for topics and global state
- Copy-on-read pattern for iteration over collections during modifications

### 6. **Error Handling**
- Graceful error handling with proper HTTP status codes
- WebSocket errors are sent as JSON messages
- Failed connections are automatically cleaned up

### 7. **Slow Consumer Detection**
- Each connection has a message queue (MESSAGE_QUEUE_SIZE limit)
- Connections at 80%+ queue capacity are marked as slow consumers  
- Slow consumers are rejected for new subscriptions with SLOW_CONSUMER error
- Messages to full queues are dropped and logged

### 8. **Topic Management**
- Topics can be auto-created on first use (AUTO_CREATE_TOPICS=true)
- When auto-create is disabled, TOPIC_NOT_FOUND errors are returned
- Topics are deleted when all subscribers disconnect

### 9. **Concurrency Safety**
- Per-connection locks prevent race conditions
- Thread-safe topic subscriber management
- Lock-free message broadcasting to avoid blocking
- Graceful cleanup of failed/slow connections

### 10. **Fan-out & Isolation**
- Each message delivered exactly once to each subscriber
- Topics are completely isolated - no cross-topic message leakage
- Automatic isolation integrity checks available via REST API

### 11. **Graceful Shutdown**
- Stop accepting new connections during shutdown
- Allow existing operations to complete (configurable timeout)
- Close all WebSocket connections cleanly
- Clear all topic subscriptions

### 12. **Scalability Considerations**
- Message broadcasting is async but sequential per topic
- Failed deliveries don't block other subscribers
- Statistics collection has minimal performance impact

## Testing the System

### Using curl for REST API:

```bash
# Check health
curl http://localhost:8000/api/health

# Get system stats
curl http://localhost:8000/api/stats

# Check slow consumers
curl http://localhost:8000/api/slow-consumers

# Check topic isolation
curl http://localhost:8000/api/isolation-check

# Manually cleanup slow consumers
curl -X POST http://localhost:8000/api/cleanup-slow-consumers

# Create a topic
curl -X POST http://localhost:8000/api/topics/ \\
  -H "Content-Type: application/json" \\
  -d '{"name": "test-topic"}'

# List topics
curl http://localhost:8000/api/topics/
```

### Using WebSocket client:

```python
import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://localhost:8000/ws"
    
    async with websockets.connect(uri) as websocket:
        # Subscribe to topic
        await websocket.send(json.dumps({
            "type": "subscribe",
            "topic": "test-topic"
        }))
        
        # Publish message
        await websocket.send(json.dumps({
            "type": "publish",
            "topic": "test-topic",
            "data": {"message": "Hello, WebSocket!"}
        }))
        
        # Receive messages
        async for message in websocket:
            print(json.loads(message))

asyncio.run(test_websocket())
```

## Production Considerations

### Security
- Enable authentication/authorization for production use
- Configure CORS properly for your domain
- Use HTTPS/WSS in production
- Consider rate limiting

### Monitoring
- The `/api/metrics` endpoint provides Prometheus-compatible metrics
- Health check endpoint for load balancer configuration
- Structured logging for observability

### Performance
- Current implementation is single-threaded (suitable for moderate loads)
- For high throughput, consider async message queuing
- Monitor memory usage as all data is in-memory

### High Availability
- No persistence means data is lost on restart
- Consider implementing connection state recovery
- Use multiple instances behind a load balancer for redundancy

## Limitations

1. **No Persistence**: All data is lost on application restart
2. **Single Node**: No built-in clustering or horizontal scaling
3. **Memory Bound**: Limited by available system memory
4. **No Message History**: Messages are delivered in real-time only
5. **No Authentication**: No built-in user authentication or authorization

## Development

### Project Structure
```
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Configuration management
│   ├── api/                    # REST API endpoints
│   │   ├── topics.py
│   │   └── health.py
│   ├── core/                   # Core business logic
│   │   ├── models.py           # Data models
│   │   └── pubsub.py           # Pub/Sub manager
│   └── websocket/              # WebSocket handling
│       ├── handlers.py         # Message handlers
│       └── manager.py          # Connection management
├── requirements.txt
├── .env.example
├── Dockerfile
├── run.py                      # Application entry point
└── README.md
```

### Code Quality
- Type hints throughout the codebase
- Pydantic models for data validation
- Comprehensive error handling
- Structured logging
- Clean separation of concerns

## License

This project is provided as-is for educational and demonstration purposes.
