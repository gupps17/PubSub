"""REST API endpoints for health checks and system statistics."""
import logging
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from app.core.models import HealthStatus, SystemStats
from app.core.pubsub import pubsub_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/", tags=["health"])


@router.get("/health", response_model=HealthStatus)
async def health_check():
    """Health check endpoint."""
    try:
        uptime = time.time() - pubsub_manager.start_time
        
        return HealthStatus(
            status="healthy",
            timestamp=datetime.utcnow(),
            version="1.0.0",
            uptime_seconds=uptime
        )
        
    except Exception as e:
        logger.error(f"Error during health check: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Health check failed"
        )


@router.get("/stats", response_model=SystemStats)
async def get_system_stats():
    """Get overall system statistics."""
    try:
        return pubsub_manager.get_system_stats()
        
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve system statistics"
        )


@router.get("/metrics")
async def get_metrics():
    """Get system metrics in a simple format for monitoring."""
    try:
        stats = pubsub_manager.get_system_stats()
        
        # Return metrics in a simple key-value format
        metrics = {
            "pubsub_total_topics": stats.total_topics,
            "pubsub_total_connections": stats.total_connections,
            "pubsub_total_messages_sent": stats.total_messages_sent,
            "pubsub_uptime_seconds": stats.uptime_seconds,
            "pubsub_memory_usage_mb": stats.memory_usage_mb,
        }
        
        # Add per-topic metrics
        topics = pubsub_manager.get_topics()
        for topic in topics:
            safe_topic_name = topic.name.replace("-", "_").replace(".", "_")
            metrics[f"pubsub_topic_{safe_topic_name}_subscribers"] = topic.subscriber_count
            metrics[f"pubsub_topic_{safe_topic_name}_messages"] = topic.message_count
            
        return metrics
        
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve metrics"
        )


@router.get("/slow-consumers")
async def get_slow_consumer_stats():
    """Get statistics about slow consumers."""
    try:
        return pubsub_manager.get_slow_consumer_stats()
        
    except Exception as e:
        logger.error(f"Error getting slow consumer stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve slow consumer statistics"
        )


@router.get("/isolation-check")
async def check_topic_isolation():
    """Check topic isolation - ensure no cross-topic leakage."""
    try:
        return pubsub_manager.ensure_topic_isolation()
        
    except Exception as e:
        logger.error(f"Error checking topic isolation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check topic isolation"
        )


@router.post("/cleanup-slow-consumers")
async def manual_slow_consumer_cleanup():
    """Manually trigger cleanup of slow consumers."""
    try:
        disconnected_count = await pubsub_manager.cleanup_slow_consumers()
        return {
            "message": f"Cleaned up {disconnected_count} slow consumers",
            "disconnected_count": disconnected_count
        }
        
    except Exception as e:
        logger.error(f"Error during manual cleanup: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cleanup slow consumers"
        )
