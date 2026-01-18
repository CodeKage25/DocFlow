"""
Database Configuration for DocFlow

Direct PostgreSQL connection using asyncpg for high-performance async operations.

Environment Variables:
    DATABASE_URL: PostgreSQL connection string
    
Usage:
    from src.database import get_db_pool, DocumentRepository

    # Initialize pool at startup
    pool = await get_db_pool()
    
    # Use repositories
    await DocumentRepository.create(pool, document_data)
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)

# Check if asyncpg is available
try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    logger.warning("asyncpg not installed. Run: pip install asyncpg")

# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class DatabaseConfig:
    """Database configuration."""
    database_url: str = ""
    min_connections: int = 2
    max_connections: int = 10
    
    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Load configuration from environment variables."""
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            min_connections=int(os.getenv("DB_MIN_CONNECTIONS", "2")),
            max_connections=int(os.getenv("DB_MAX_CONNECTIONS", "10")),
        )
    
    @property
    def is_configured(self) -> bool:
        """Check if database is configured."""
        return bool(self.database_url)


# Global pool
_pool: Optional["asyncpg.Pool"] = None
_config: Optional[DatabaseConfig] = None


async def get_db_pool() -> Optional["asyncpg.Pool"]:
    """Get the database connection pool."""
    global _pool, _config
    
    if not ASYNCPG_AVAILABLE:
        logger.warning("asyncpg not available, using in-memory storage")
        return None
    
    if _pool is not None:
        return _pool
    
    if _config is None:
        _config = DatabaseConfig.from_env()
    
    if not _config.is_configured:
        logger.warning("Database not configured, using in-memory storage")
        return None
    
    try:
        # For transaction pooler (port 6543), we need to disable statement cache
        # as prepared statements aren't supported in transaction mode usually
        is_pooler = ":6543" in _config.database_url
        
        _pool = await asyncpg.create_pool(
            _config.database_url,
            min_size=_config.min_connections,
            max_size=_config.max_connections,
            statement_cache_size=0 if is_pooler else 100
        )
        logger.info(f"✅ Connected to PostgreSQL database (Pooler: {is_pooler})")
        return _pool
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None


async def close_db_pool():
    """Close the database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


# =============================================================================
# DATABASE SCHEMA
# =============================================================================

SCHEMA_SQL = """
-- DocFlow Database Schema

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id TEXT UNIQUE NOT NULL,
    content_hash TEXT NOT NULL,
    document_type TEXT NOT NULL DEFAULT 'invoice',
    source_system TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_documents_document_id ON documents(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- Extraction results table
CREATE TABLE IF NOT EXISTS extraction_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id TEXT NOT NULL,
    extracted_fields JSONB NOT NULL DEFAULT '{}',
    confidence_score FLOAT,
    status TEXT NOT NULL DEFAULT 'pending',
    processing_time_ms INTEGER,
    model_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extraction_results_document_id ON extraction_results(document_id);

-- Review items table
CREATE TABLE IF NOT EXISTS review_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id TEXT UNIQUE NOT NULL,
    document_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    extraction_result JSONB NOT NULL DEFAULT '{}',
    document_preview_url TEXT,
    document_type TEXT NOT NULL DEFAULT 'invoice',
    source_system TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_to TEXT,
    assigned_at TIMESTAMPTZ,
    claim_expires_at TIMESTAMPTZ,
    sla_deadline TIMESTAMPTZ NOT NULL,
    low_confidence_fields TEXT[] DEFAULT '{}',
    review_attempts INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_review_items_priority ON review_items(priority);
CREATE INDEX IF NOT EXISTS idx_review_items_assigned_to ON review_items(assigned_to);

-- Review results table
CREATE TABLE IF NOT EXISTS review_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    result_id TEXT UNIQUE NOT NULL,
    item_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    corrections JSONB DEFAULT '[]',
    rejection_reason TEXT,
    review_duration_seconds INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_results_item_id ON review_results(item_id);
CREATE INDEX IF NOT EXISTS idx_review_results_reviewer_id ON review_results(reviewer_id);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_documents_updated_at') THEN
        CREATE TRIGGER update_documents_updated_at
            BEFORE UPDATE ON documents
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_review_items_updated_at') THEN
        CREATE TRIGGER update_review_items_updated_at
            BEFORE UPDATE ON review_items
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END
$$;
"""


async def init_database():
    """Initialize the database schema."""
    pool = await get_db_pool()
    if pool is None:
        logger.info("No database configured, skipping initialization")
        return False
    
    try:
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        logger.info("✅ Database schema initialized")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False


# =============================================================================
# REPOSITORIES
# =============================================================================

class DocumentRepository:
    """Repository for document operations."""
    
    @staticmethod
    async def create(document_id: str, content_hash: str, document_type: str, metadata: Dict = None) -> Optional[str]:
        """Create a new document record."""
        pool = await get_db_pool()
        if pool is None:
            return document_id  # Return ID even without DB
        
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO documents (document_id, content_hash, document_type, metadata)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (document_id) DO UPDATE SET
                    content_hash = $2,
                    updated_at = NOW()
            """, document_id, content_hash, document_type, json.dumps(metadata or {}))
        return document_id
    
    @staticmethod
    async def get_by_id(document_id: str) -> Optional[Dict]:
        """Get document by ID."""
        pool = await get_db_pool()
        if pool is None:
            return None
        
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM documents WHERE document_id = $1", 
                document_id
            )
            return dict(row) if row else None
    
    @staticmethod
    async def update_status(document_id: str, status: str) -> bool:
        """Update document status."""
        pool = await get_db_pool()
        if pool is None:
            return True
        
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE documents SET status = $1 WHERE document_id = $2",
                status, document_id
            )
            return "UPDATE" in result

    @staticmethod
    async def save_extraction(document_id: str, extracted_fields: Dict, confidence: float) -> bool:
        """Save extraction results."""
        pool = await get_db_pool()
        if pool is None:
            return True
        
        import json
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO extraction_results 
                (document_id, extracted_fields, confidence_score, status)
                VALUES ($1, $2, $3, 'completed')
            """, document_id, json.dumps(extracted_fields), confidence)
        return True
    
    @staticmethod
    async def list_all(limit: int = 100) -> List[Dict]:
        """List all documents with their extraction results."""
        pool = await get_db_pool()
        if pool is None:
            return []
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    d.document_id,
                    d.document_type,
                    d.status,
                    d.metadata,
                    d.created_at,
                    d.updated_at,
                    e.extracted_fields,
                    e.confidence_score,
                    CASE WHEN r.item_id IS NOT NULL THEN true ELSE false END as needs_review,
                    r.item_id as review_item_id,
                    r.status as review_status
                FROM documents d
                LEFT JOIN extraction_results e ON d.document_id = e.document_id
                LEFT JOIN review_items r ON d.document_id = r.document_id
                ORDER BY d.created_at DESC
                LIMIT $1
            """, limit)
            
            return [dict(row) for row in rows]


class ReviewItemRepository:
    """Repository for review item operations."""
    
    @staticmethod
    async def create(
        item_id: str,
        document_id: str,
        workflow_id: str,
        extraction_result: Dict,
        document_type: str,
        sla_deadline: datetime,
        priority: int = 3,
        low_confidence_fields: List[str] = None
    ) -> Optional[str]:
        """Create a new review item."""
        pool = await get_db_pool()
        if pool is None:
            return item_id
        
        import json
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO review_items 
                (item_id, document_id, workflow_id, extraction_result, document_type, 
                 sla_deadline, priority, low_confidence_fields)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, item_id, document_id, workflow_id, json.dumps(extraction_result),
                document_type, sla_deadline, priority, low_confidence_fields or [])
        return item_id
    
    @staticmethod
    async def get_pending(limit: int = 50) -> List[Dict]:
        """Get pending review items."""
        pool = await get_db_pool()
        if pool is None:
            return []
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM review_items 
                WHERE status = 'pending'
                ORDER BY priority, sla_deadline
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]
    
    @staticmethod
    async def claim(item_id: str, reviewer_id: str, expires_at: datetime) -> bool:
        """Claim an item for review."""
        pool = await get_db_pool()
        if pool is None:
            return True
        
        async with pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE review_items 
                SET status = 'assigned', assigned_to = $2, 
                    assigned_at = NOW(), claim_expires_at = $3
                WHERE item_id = $1 AND status = 'pending'
            """, item_id, reviewer_id, expires_at)
            return "UPDATE 1" in result
    
    @staticmethod
    async def complete(item_id: str) -> bool:
        """Mark item as completed."""
        pool = await get_db_pool()
        if pool is None:
            return True
        
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE review_items SET status = 'completed' WHERE item_id = $1",
                item_id
            )
            return "UPDATE" in result


class ReviewResultRepository:
    """Repository for review results."""
    
    @staticmethod
    async def create(
        result_id: str,
        item_id: str,
        document_id: str,
        reviewer_id: str,
        decision: str,
        corrections: List[Dict] = None,
        duration_seconds: int = None
    ) -> Optional[str]:
        """Create a review result."""
        pool = await get_db_pool()
        if pool is None:
            return result_id
        
        import json
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO review_results 
                (result_id, item_id, document_id, reviewer_id, decision, corrections, review_duration_seconds)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, result_id, item_id, document_id, reviewer_id, decision,
                json.dumps(corrections or []), duration_seconds)
        return result_id

class ReviewRepository:
    """Repository for review queue operations."""
    
    @staticmethod
    async def upsert(item_data: Dict[str, Any]) -> bool:
        """Insert or update a review item."""
        pool = await get_db_pool()
        if pool is None:
            return False
            
        query = """
            INSERT INTO review_items (
                item_id, document_id, workflow_id, extraction_result, 
                document_preview_url, document_type, source_system,
                priority, status, assigned_to, assigned_at, 
                claim_expires_at, sla_deadline, low_confidence_fields, 
                created_at
            ) VALUES (
                $1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
            )
            ON CONFLICT (item_id) DO UPDATE SET
                status = EXCLUDED.status,
                assigned_to = EXCLUDED.assigned_to,
                assigned_at = EXCLUDED.assigned_at,
                claim_expires_at = EXCLUDED.claim_expires_at,
                priority = EXCLUDED.priority,
                updated_at = NOW()
        """
        
        try:
            # Ensure status is a string, not an enum
            status_val = item_data['status']
            if hasattr(status_val, 'value'):
                status_val = status_val.value
            
            async with pool.acquire() as conn:
                await conn.execute(
                    query,
                    item_data['item_id'],
                    item_data['document_id'],
                    item_data['workflow_id'],
                    json.dumps(item_data['extraction_result']),
                    item_data['document_preview_url'],
                    item_data['document_type'],
                    item_data['source_system'],
                    item_data['priority'],
                    status_val,
                    item_data.get('assigned_to'),
                    item_data.get('assigned_at'),
                    item_data.get('claim_expires_at'),
                    item_data['sla_deadline'],
                    item_data['low_confidence_fields'],
                    item_data['created_at']
                )
            return True
        except Exception as e:
            logger.error(f"Failed to upsert review item: {e}")
            return False

    @staticmethod
    async def get_active_items() -> List[Dict[str, Any]]:
        """Get all active review items (pending, assigned, in_review)."""
        pool = await get_db_pool()
        if pool is None:
            return []
            
        query = """
            SELECT * FROM review_items 
            WHERE status IN ('pending', 'assigned', 'in_review', 'escalated')
        """
        
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get active items: {e}")
            return []

    @staticmethod
    async def get_completed_items(limit: int = 50) -> List[Dict[str, Any]]:
        """Get completed review items."""
        pool = await get_db_pool()
        if pool is None:
            return []
            
        query = """
            SELECT * FROM review_items 
            WHERE status = 'completed'
            ORDER BY updated_at DESC
            LIMIT $1
        """
        
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, limit)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get completed items: {e}")
            return []            
        query = """
            SELECT * FROM review_items 
            WHERE status IN ('pending', 'assigned', 'in_review', 'escalated')
        """
        
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get active items: {e}")
            return []

    @staticmethod
    async def update_status(item_id: str, status: str, reviewer_id: Optional[str] = None) -> bool:
        """Update item status."""
        pool = await get_db_pool()
        if pool is None:
            return False
            
        query = """
            UPDATE review_items 
            SET status = $2, 
                assigned_to = COALESCE($3, assigned_to),
                updated_at = NOW()
            WHERE item_id = $1
        """
        
        try:
            async with pool.acquire() as conn:
                await conn.execute(query, item_id, status, reviewer_id)
            return True
        except Exception as e:
            logger.error(f"Failed to update review item status: {e}")
            return False
