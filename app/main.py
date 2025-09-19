"""Main FastAPI application for the Pub/Sub system."""
import asyncio
import logging
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import settings
from app.api.topics import router as topics_router
from app.api.health import router as health_router
from app.websocket.handlers import websocket_handler
from app.websocket.manager import connection_manager
from app.core.pubsub import pubsub_manager

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# For production, you might want to set specific loggers to different levels
if not settings.debug:
    # In production, reduce noise from some loggers
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
else:
    # In debug mode, show detailed logging
    logger.info("🐛 Debug mode enabled - detailed logging active")
    logger.info(f"Auto-create topics: {settings.auto_create_topics}")
    logger.info(f"Max connections: {settings.max_connections}")
    logger.info(f"Max topics: {settings.max_topics}")
    logger.info(f"Backpressure strategy: {settings.backpressure_strategy}")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Pub/Sub system...")
    
    # Start background tasks
    cleanup_task = asyncio.create_task(periodic_cleanup())
    slow_consumer_cleanup_task = asyncio.create_task(periodic_slow_consumer_cleanup())
    
    # Setup signal handlers for graceful shutdown
    def signal_handler():
        logger.info("Received shutdown signal")
        cleanup_task.cancel()
        slow_consumer_cleanup_task.cancel()
    
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
    signal.signal(signal.SIGINT, lambda s, f: signal_handler())
    
    try:
        yield
    finally:
        # Graceful shutdown
        logger.info("Shutting down Pub/Sub system...")
        
        # Cancel background tasks
        cleanup_task.cancel()
        slow_consumer_cleanup_task.cancel()
        
        # Perform graceful shutdown
        try:
            await pubsub_manager.graceful_shutdown(timeout_seconds=10)
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
        
        # Wait for tasks to complete
        for task in [cleanup_task, slow_consumer_cleanup_task]:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error waiting for task completion: {e}")


async def periodic_cleanup():
    """Periodic cleanup task for stale connections."""
    while True:
        try:
            await asyncio.sleep(settings.websocket_ping_interval)
            await connection_manager.cleanup_stale_connections()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")


async def periodic_slow_consumer_cleanup():
    """Periodic cleanup task for slow consumers."""
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            disconnected_count = await pubsub_manager.cleanup_slow_consumers()
            if disconnected_count > 0:
                logger.info(f"Cleaned up {disconnected_count} slow consumers")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in slow consumer cleanup: {e}")


# Create FastAPI application
app = FastAPI(
    title="In-Memory Pub/Sub System",
    description="A simplified in-memory Pub/Sub system with WebSocket and REST API support",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(topics_router)
app.include_router(health_router)


@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint with basic information."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Pub/Sub System</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 800px; }
            .endpoint { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
            code { background: #e0e0e0; padding: 2px 4px; border-radius: 3px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>In-Memory Pub/Sub System</h1>
            <p>A simplified pub/sub system with WebSocket and REST API support.</p>
            
            <h2>WebSocket Endpoint</h2>
            <div class="endpoint">
                <strong>WebSocket:</strong> <code>ws://localhost:8000/ws</code><br>
                <strong>Operations:</strong> publish, subscribe, unsubscribe, ping
            </div>
            
            <h2>REST API Endpoints</h2>
            <div class="endpoint">
                <strong>Health Check:</strong> <code>GET /api/health</code>
            </div>
            <div class="endpoint">
                <strong>System Stats:</strong> <code>GET /api/stats</code>
            </div>
            <div class="endpoint">
                <strong>System Metrics:</strong> <code>GET /api/metrics</code>
            </div>
            <div class="endpoint">
                <strong>Slow Consumer Stats:</strong> <code>GET /api/slow-consumers</code>
            </div>
            <div class="endpoint">
                <strong>Topic Isolation Check:</strong> <code>GET /api/isolation-check</code>
            </div>
            <div class="endpoint">
                <strong>Cleanup Slow Consumers:</strong> <code>POST /api/cleanup-slow-consumers</code>
            </div>
            <div class="endpoint">
                <strong>List Topics:</strong> <code>GET /api/topics/</code>
            </div>
            <div class="endpoint">
                <strong>Create Topic:</strong> <code>POST /api/topics/</code>
            </div>
            <div class="endpoint">
                <strong>Get Topic:</strong> <code>GET /api/topics/{topic_name}</code>
            </div>
            <div class="endpoint">
                <strong>Delete Topic:</strong> <code>DELETE /api/topics/{topic_name}</code>
            </div>
            <div class="endpoint">
                <strong>Topic Stats:</strong> <code>GET /api/topics/{topic_name}/stats</code>
            </div>
            
            <h2>Documentation</h2>
            <p>
                <a href="/docs" target="_blank">Interactive API Documentation (Swagger)</a><br>
                <a href="/redoc" target="_blank">Alternative API Documentation (ReDoc)</a>
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for pub/sub operations."""
    connection_id = None
    client_addr = getattr(websocket.client, 'host', 'unknown') if websocket.client else 'unknown'
    logger.debug(f"New WebSocket connection attempt from {client_addr}")
    
    try:
        connection_id = await connection_manager.connect(websocket)
        
        if connection_id is None:
            logger.warning(f"Failed to establish connection for client {client_addr}")
            return
        
        logger.info(f"WebSocket connection established: {connection_id} from {client_addr}")
            
        try:
            while True:
                # Receive message from client
                logger.debug(f"Waiting for message from connection {connection_id}")
                data = await websocket.receive_text()
                logger.debug(f"Received message from {connection_id}: {data[:200]}{'...' if len(data) > 200 else ''}")
                
                # Handle the message
                await websocket_handler.handle_message(websocket, connection_id, data)
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket client disconnected normally: {connection_id}")
        except Exception as e:
            logger.error(f"🚨 Error in WebSocket connection {connection_id}:")
            logger.error(f"  Client Address: {client_addr}")
            logger.error(f"  Exception Type: {type(e).__name__}")
            logger.error(f"  Exception Message: {e}")
            
            # Log stack trace for debugging
            import traceback
            logger.error(f"  Stack Trace:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    logger.error(f"    {line}")
                    
    except Exception as connection_error:
        logger.error(f"🚨 Error during WebSocket connection establishment:")
        logger.error(f"  Client Address: {client_addr}")
        logger.error(f"  Exception Type: {type(connection_error).__name__}")
        logger.error(f"  Exception Message: {connection_error}")
        
        # Log stack trace
        import traceback
        logger.error(f"  Stack Trace:")
        for line in traceback.format_exc().split('\n'):
            if line.strip():
                logger.error(f"    {line}")
    finally:
        if connection_id:
            logger.debug(f"Cleaning up connection {connection_id}")
            await connection_manager.disconnect(connection_id)
        else:
            logger.debug(f"No connection ID to clean up for {client_addr}")


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info"
    )
