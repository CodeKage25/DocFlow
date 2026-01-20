"""
Supabase Storage Module for DocFlow

Handles file uploads to Supabase Storage for persistent document storage.

Environment Variables:
    SUPABASE_URL: Supabase project URL (e.g., https://xxx.supabase.co)
    SUPABASE_KEY: Supabase anon/service key
"""

import os
import logging
from typing import Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Supabase client (lazy initialization)
_supabase_client = None
BUCKET_NAME = "documents"


def get_supabase_client():
    """Get or create Supabase client."""
    global _supabase_client
    
    if _supabase_client is not None:
        return _supabase_client
    
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        logger.warning("Supabase credentials not configured. File storage will use local fallback.")
        return None
    
    try:
        from supabase import create_client
        _supabase_client = create_client(supabase_url, supabase_key)
        logger.info("Supabase Storage client initialized")
        return _supabase_client
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        return None


async def upload_file(file_content: bytes, file_name: str, content_type: str = "application/pdf") -> Tuple[bool, Optional[str]]:
    """
    Upload a file to Supabase Storage.
    
    Args:
        file_content: File bytes
        file_name: Name to save the file as
        content_type: MIME type of the file
    
    Returns:
        Tuple of (success, public_url or error_message)
    """
    client = get_supabase_client()
    
    if client is None:
        return False, "Supabase not configured"
    
    try:
        # Upload to storage bucket
        result = client.storage.from_(BUCKET_NAME).upload(
            path=file_name,
            file=file_content,
            file_options={"content-type": content_type, "upsert": "true"}
        )
        
        # Get public URL
        public_url = client.storage.from_(BUCKET_NAME).get_public_url(file_name)
        
        logger.info(f"Uploaded {file_name} to Supabase Storage")
        return True, public_url
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to upload {file_name} to Supabase: {error_msg}")
        return False, error_msg


async def get_file_url(file_name: str) -> Optional[str]:
    """
    Get the public URL for a file in Supabase Storage.
    
    Args:
        file_name: Name of the file
    
    Returns:
        Public URL or None if not found
    """
    client = get_supabase_client()
    
    if client is None:
        return None
    
    try:
        return client.storage.from_(BUCKET_NAME).get_public_url(file_name)
    except Exception as e:
        logger.error(f"Failed to get URL for {file_name}: {e}")
        return None


async def delete_file(file_name: str) -> bool:
    """
    Delete a file from Supabase Storage.
    
    Args:
        file_name: Name of the file to delete
    
    Returns:
        True if successful, False otherwise
    """
    client = get_supabase_client()
    
    if client is None:
        return False
    
    try:
        client.storage.from_(BUCKET_NAME).remove([file_name])
        logger.info(f"Deleted {file_name} from Supabase Storage")
        return True
    except Exception as e:
        logger.error(f"Failed to delete {file_name}: {e}")
        return False


def is_storage_configured() -> bool:
    """Check if Supabase Storage is properly configured."""
    return get_supabase_client() is not None
