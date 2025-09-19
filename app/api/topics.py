"""REST API endpoints for topic management."""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.core.models import (
    TopicInfo, 
    TopicStats, 
    CreateTopicRequest, 
    TopicListResponse,
    ErrorResponse
)
from app.core.pubsub import pubsub_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/", response_model=TopicListResponse)
async def list_topics():
    """Get list of all topics with their information."""
    try:
        topics = pubsub_manager.get_topics()
        return TopicListResponse(
            topics=topics,
            total_count=len(topics)
        )
    except Exception as e:
        logger.error(f"Error listing topics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve topics"
        )


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_topic(request: CreateTopicRequest):
    """Create a new topic."""
    try:
        success = pubsub_manager.create_topic(request.name)
        
        if not success:
            # Check if topic already exists
            existing_topics = [t.name for t in pubsub_manager.get_topics()]
            if request.name in existing_topics:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Topic '{request.name}' already exists"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to create topic. Maximum number of topics may have been reached."
                )
        
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "message": f"Topic '{request.name}' created successfully",
                "topic_name": request.name
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating topic '{request.name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create topic"
        )


@router.get("/{topic_name}", response_model=TopicInfo)
async def get_topic(topic_name: str):
    """Get information about a specific topic."""
    try:
        topics = pubsub_manager.get_topics()
        topic = next((t for t in topics if t.name == topic_name), None)
        
        if not topic:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Topic '{topic_name}' not found"
            )
            
        return topic
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting topic '{topic_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve topic information"
        )


@router.delete("/{topic_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(topic_name: str):
    """Delete a topic and unsubscribe all its subscribers."""
    try:
        success = pubsub_manager.delete_topic(topic_name)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Topic '{topic_name}' not found"
            )
            
        logger.info(f"Topic '{topic_name}' deleted successfully")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting topic '{topic_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete topic"
        )


@router.get("/{topic_name}/stats", response_model=TopicStats)
async def get_topic_stats(topic_name: str):
    """Get statistics for a specific topic."""
    try:
        stats = pubsub_manager.get_topic_stats(topic_name)
        
        if not stats:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Topic '{topic_name}' not found"
            )
            
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stats for topic '{topic_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve topic statistics"
        )
