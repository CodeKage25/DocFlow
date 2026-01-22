"""
Review Queue Module

Human review queue system with priority management, SLA tracking,
atomic claim operations, correction tracking, and audit trail.

"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, APIRouter
from pydantic import BaseModel, Field


from src.database import DocumentRepository, ReviewRepository
from dataclasses import asdict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)




class ReviewStatus(str, Enum):
    """Status of a review item."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    EXPIRED = "expired"


class ReviewDecision(str, Enum):
    """Decision made by reviewer."""
    APPROVE = "approve"
    CORRECT = "correct"
    REJECT = "reject"


class RejectionCategory(str, Enum):
    """Categories for rejection."""
    ILLEGIBLE = "illegible"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    INCOMPLETE = "incomplete"
    OTHER = "other"




@dataclass
class ExtractedFieldData:
    """Extracted field with confidence."""
    value: Any
    confidence: float
    is_locked: bool = False


@dataclass
class FieldCorrection:
    """A single field correction."""
    field_name: str
    original_value: Any
    corrected_value: Any
    correction_type: str = "value_change"
    reviewer_notes: Optional[str] = None


@dataclass
class ReviewItem:
    """A document pending human review."""
    item_id: str
    document_id: str
    workflow_id: str
    
    # Extraction data
    extraction_result: Dict[str, ExtractedFieldData]
    document_preview_url: str
    low_confidence_fields: List[str]
    
    # Priority & SLA
    priority: int
    priority_factors: Dict[str, float]
    created_at: datetime
    sla_deadline: datetime
    
    # Assignment
    status: ReviewStatus = ReviewStatus.PENDING
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None
    claim_expires_at: Optional[datetime] = None
    
    # Tracking
    review_attempts: int = 0
    previous_reviewers: List[str] = field(default_factory=list)
    
    # Metadata
    document_type: str = "invoice"
    source_system: str = "default"
    customer_id: Optional[str] = None
    
    @property
    def sla_remaining_seconds(self) -> int:
        """Get seconds remaining until SLA deadline."""
        remaining = (self.sla_deadline - utc_now()).total_seconds()
        return max(0, int(remaining))
    
    @property
    def is_at_risk(self) -> bool:
        """Check if item is at risk of breaching SLA."""
        return self.sla_remaining_seconds < 3600  # Within 1 hour
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "item_id": self.item_id,
            "document_id": self.document_id,
            "workflow_id": self.workflow_id,
            "extraction_result": {
                k: {"value": v.value, "confidence": v.confidence, "is_locked": v.is_locked}
                for k, v in self.extraction_result.items()
            },
            "document_preview_url": self.document_preview_url,
            "low_confidence_fields": self.low_confidence_fields,
            "priority": self.priority,
            "priority_factors": self.priority_factors,
            "created_at": self.created_at.isoformat(),
            "sla_deadline": self.sla_deadline.isoformat(),
            "sla_remaining_seconds": self.sla_remaining_seconds,
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "review_attempts": self.review_attempts,
            "document_type": self.document_type,
            "source_system": self.source_system,
            "is_at_risk": self.is_at_risk,
        }


@dataclass
class ReviewResult:
    """Outcome of a human review."""
    result_id: str
    item_id: str
    document_id: str
    reviewer_id: str
    
    decision: ReviewDecision
    corrections: List[FieldCorrection]
    rejection_reason: Optional[str] = None
    rejection_category: Optional[RejectionCategory] = None
    
    review_started_at: datetime = field(default_factory=datetime.utcnow)
    review_completed_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def review_duration_seconds(self) -> int:
        """Calculate review duration."""
        return int((self.review_completed_at - self.review_started_at).total_seconds())


@dataclass
class AuditEntry:
    """Audit trail entry."""
    entry_id: str
    timestamp: datetime
    item_id: str
    document_id: str
    actor: str
    action: str
    details: Dict[str, Any]
    previous_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None


@dataclass
class ReviewerStats:
    """Statistics for a reviewer."""
    reviewer_id: str
    is_available: bool = True
    active_items_count: int = 0
    completed_today: int = 0
    approved_today: int = 0
    corrected_today: int = 0
    rejected_today: int = 0
    total_review_time_seconds: int = 0
    expertise: Set[str] = field(default_factory=set)
    
    @property
    def avg_review_time_seconds(self) -> float:
        """Calculate average review time."""
        if self.completed_today == 0:
            return 0.0
        return self.total_review_time_seconds / self.completed_today


@dataclass
class QueueStats:
    """Queue statistics."""
    total_pending: int = 0
    total_assigned: int = 0
    total_completed_today: int = 0
    by_priority: Dict[int, int] = field(default_factory=dict)
    by_document_type: Dict[str, int] = field(default_factory=dict)
    sla_at_risk: int = 0
    sla_breached: int = 0
    avg_wait_time_minutes: float = 0.0




class PriorityCalculator:
    """Calculates review item priority."""
    
    def calculate(self, item: ReviewItem) -> Tuple[int, Dict[str, float]]:
        """
        Calculate priority based on multiple factors.
        Returns (priority, factors) where priority is 1-5.
        """
        score = 0.0
        factors = {}
        
        # Factor 1: SLA Urgency (0-40 points)
        sla_hours = item.sla_remaining_seconds / 3600
        if sla_hours <= 1:
            urgency = 40
        elif sla_hours <= 2:
            urgency = 30
        elif sla_hours <= 4:
            urgency = 20
        elif sla_hours <= 8:
            urgency = 10
        else:
            urgency = 0
        score += urgency
        factors["sla_urgency"] = urgency
        
        # Factor 2: Low Confidence Penalty (0-30 points)
        if item.low_confidence_fields:
            confidences = [
                item.extraction_result[f].confidence 
                for f in item.low_confidence_fields
                if f in item.extraction_result
            ]
            if confidences:
                avg_conf = sum(confidences) / len(confidences)
                penalty = (1 - avg_conf) * 30
                score += penalty
                factors["confidence_penalty"] = penalty
        
        # Factor 3: Document Value (0-20 points)
        total_field = item.extraction_result.get("total_amount")
        if total_field:
            total = total_field.value or 0
            if total >= 100000:
                value_score = 20
            elif total >= 10000:
                value_score = 15
            elif total >= 1000:
                value_score = 10
            else:
                value_score = 5
            score += value_score
            factors["document_value"] = value_score
        
        # Factor 4: Queue Time Boost (0-10 points)
        queue_hours = (utc_now() - item.created_at).total_seconds() / 3600
        time_boost = min(queue_hours * 2, 10)
        score += time_boost
        factors["queue_time_boost"] = time_boost
        
        # Convert to priority
        if score >= 70:
            priority = 1
        elif score >= 50:
            priority = 2
        elif score >= 30:
            priority = 3
        elif score >= 15:
            priority = 4
        else:
            priority = 5
        
        return priority, factors




class AuditLogger:
    """Logs audit events for review actions."""
    
    def __init__(self, log_dir: str = "./audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._entries: List[AuditEntry] = []
    
    async def log(
        self,
        item_id: str,
        document_id: str,
        actor: str,
        action: str,
        details: Dict[str, Any],
        previous_state: Optional[Dict] = None,
        new_state: Optional[Dict] = None
    ):
        """Log an audit event."""
        entry = AuditEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=utc_now(),
            item_id=item_id,
            document_id=document_id,
            actor=actor,
            action=action,
            details=details,
            previous_state=previous_state,
            new_state=new_state
        )
        self._entries.append(entry)
        
        # Write to file
        log_file = self.log_dir / f"{item_id}_audit.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps({
                "entry_id": entry.entry_id,
                "timestamp": entry.timestamp.isoformat(),
                "item_id": entry.item_id,
                "document_id": entry.document_id,
                "actor": entry.actor,
                "action": entry.action,
                "details": entry.details
            }) + "\n")
        
        logger.info(f"Audit: {action} on {item_id} by {actor}")
    
    async def get_entries(self, item_id: str) -> List[AuditEntry]:
        """Get audit entries for an item."""
        return [e for e in self._entries if e.item_id == item_id]




class ReviewQueueManager:
    """
    Manages the human review queue.
    
    Features:
    - Atomic claim operations with optimistic locking
    - Priority-based ordering with SLA awareness
    - Correction tracking with audit trail
    - SLA monitoring and alerting
    - Real-time updates via WebSocket
    """
    
    def __init__(
        self,
        claim_timeout_minutes: int = 30,
        sla_warning_threshold_minutes: int = 60,
    ):
        self._items: Dict[str, ReviewItem] = {}
        self._results: Dict[str, ReviewResult] = {}
        self._reviewer_stats: Dict[str, ReviewerStats] = {}
        self._corrections: List[FieldCorrection] = []
        
        self._claim_timeout = timedelta(minutes=claim_timeout_minutes)
        self._sla_warning_threshold = timedelta(minutes=sla_warning_threshold_minutes)
        
        self._lock = asyncio.Lock()
        self._priority_calculator = PriorityCalculator()
        self._audit_logger = AuditLogger()
        
        # WebSocket connections for real-time updates
        self._websocket_clients: Set[WebSocket] = set()
    
    # -------------------------------------------------------------------------
    # Queue Management
    # -------------------------------------------------------------------------

    async def load_from_db(self):
        """Load active items from database."""
        logger.info("Loading review queue from database...")
        items = await ReviewRepository.get_active_items()
        
        count = 0
        async with self._lock:
            for data in items:
                try:
                    # Convert extraction result dicts to objects
                    extraction_result = {}
                    if data.get('extraction_result'):
                        for k, v in data['extraction_result'].items():
                            if isinstance(v, dict):
                                extraction_result[k] = ExtractedFieldData(**v)
                            
                    # Remove DB columns not in dataclass or rename if necessary
                    # filtering keys...
                    valid_keys = ReviewItem.__annotations__.keys()
                    item_data = {k: v for k, v in data.items() if k in valid_keys}
                    
                    item_data['extraction_result'] = extraction_result
                    
                    # Provide defaults for fields not in DB table
                    if 'priority_factors' not in item_data or not item_data['priority_factors']:
                         item_data['priority_factors'] = {}
                    if 'previous_reviewers' not in item_data or not item_data['previous_reviewers']:
                         item_data['previous_reviewers'] = []
                    if 'customer_id' not in item_data:
                         item_data['customer_id'] = None
                    
                    # Convert status string to enum
                    if 'status' in item_data and isinstance(item_data['status'], str):
                        item_data['status'] = ReviewStatus(item_data['status'])
                         
                    item = ReviewItem(**item_data)
                    self._items[item.item_id] = item
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load item {data.get('item_id')}: {e}")
                    
        logger.info(f"Loaded {count} items from database")
    
    async def add_item(
        self,
        document_id: str,
        workflow_id: str,
        extraction_result: Dict[str, ExtractedFieldData],
        document_preview_url: str,
        document_type: str = "invoice",
        sla_hours: float = 4.0,
        source_system: str = "default",
        customer_id: Optional[str] = None
    ) -> ReviewItem:
        """Add a new item to the review queue."""
        item_id = str(uuid.uuid4())
        
        # Find low confidence fields
        low_confidence_fields = [
            name for name, field in extraction_result.items()
            if field.confidence < 0.75 and not field.is_locked
        ]
        
        # Create item
        item = ReviewItem(
            item_id=item_id,
            document_id=document_id,
            workflow_id=workflow_id,
            extraction_result=extraction_result,
            document_preview_url=document_preview_url,
            low_confidence_fields=low_confidence_fields,
            priority=5,  # Will be recalculated
            priority_factors={},
            created_at=utc_now(),
            sla_deadline=utc_now() + timedelta(hours=sla_hours),
            document_type=document_type,
            source_system=source_system,
            customer_id=customer_id
        )
        
        # Calculate priority
        item.priority, item.priority_factors = self._priority_calculator.calculate(item)
        
        async with self._lock:
            self._items[item_id] = item
            
        # Persist to database
        await ReviewRepository.upsert(asdict(item))
        
        # Audit log
        await self._audit_logger.log(
            item_id=item_id,
            document_id=document_id,
            actor="system",
            action="created",
            details={"priority": item.priority, "sla_hours": sla_hours}
        )
        
        # Update document status in database
        await DocumentRepository.update_status(document_id, "review_pending")
        
        # Notify WebSocket clients
        await self._broadcast({
            "event": "item.created",
            "data": {"item_id": item_id, "priority": item.priority}
        })
        
        logger.info(f"Added review item {item_id} for document {document_id}")
        return item
    
    async def get_queue(
        self,
        status: Optional[ReviewStatus] = None,
        priority: Optional[int] = None,
        document_type: Optional[str] = None,
        sort_by: str = "priority",
        page: int = 1,
        limit: int = 20
    ) -> Tuple[List[ReviewItem], int]:
        """Get items from the queue with filtering and pagination."""
        async with self._lock:
            items = list(self._items.values())
        
        # Filter by status
        if status:
            items = [i for i in items if i.status == status]
        else:
            # Default to pending and expired items (SLA-breached items should still be reviewable)
            items = [i for i in items if i.status in (ReviewStatus.PENDING, ReviewStatus.EXPIRED)]
        
        # Filter by priority
        if priority:
            items = [i for i in items if i.priority == priority]
        
        # Filter by document type
        if document_type:
            items = [i for i in items if i.document_type == document_type]
        
        # Sort
        if sort_by == "sla":
            items.sort(key=lambda x: x.sla_deadline)
        elif sort_by == "priority":
            items.sort(key=lambda x: (x.priority, x.sla_deadline))
        elif sort_by == "created":
            items.sort(key=lambda x: x.created_at)
        
        # Paginate
        total = len(items)
        start = (page - 1) * limit
        end = start + limit
        items = items[start:end]
        
        return items, total
    
    async def get_item(self, item_id: str) -> Optional[ReviewItem]:
        """Get a specific review item."""
        return self._items.get(item_id)
    
    async def get_stats(self) -> QueueStats:
        """Get queue statistics."""
        async with self._lock:
            items = list(self._items.values())
        
        stats = QueueStats()
        
        # Include EXPIRED items as pending (SLA-breached items should still show as needing review)
        pending = [i for i in items if i.status in (ReviewStatus.PENDING, ReviewStatus.EXPIRED)]
        assigned = [i for i in items if i.status == ReviewStatus.ASSIGNED]
        
        stats.total_pending = len(pending)
        stats.total_assigned = len(assigned)
        
        # By priority
        for item in pending:
            stats.by_priority[item.priority] = stats.by_priority.get(item.priority, 0) + 1
        
        # By document type
        for item in pending:
            stats.by_document_type[item.document_type] = \
                stats.by_document_type.get(item.document_type, 0) + 1
        
        # SLA at risk
        stats.sla_at_risk = sum(1 for i in pending if i.is_at_risk)
        
        # SLA breached
        stats.sla_breached = sum(
            1 for i in items 
            if i.status != ReviewStatus.COMPLETED and i.sla_remaining_seconds == 0
        )
        
        # Average wait time
        if pending:
            wait_times = [(utc_now() - i.created_at).total_seconds() / 60 for i in pending]
            stats.avg_wait_time_minutes = sum(wait_times) / len(wait_times)
        
        return stats

    async def get_history(self, limit: int = 50) -> List[ReviewItem]:
        """Get completed items history."""
        items = await ReviewRepository.get_completed_items(limit)
        result = []
        for data in items:
            try:
                # Reconstruct ExtractedFieldData objects
                extraction_result = {}
                if data.get('extraction_result'):
                    for k, v in data['extraction_result'].items():
                        if isinstance(v, dict):
                            extraction_result[k] = ExtractedFieldData(**v)
                
                # Filter valid keys
                valid_keys = ReviewItem.__annotations__.keys()
                item_data = {k: v for k, v in data.items() if k in valid_keys}
                item_data['extraction_result'] = extraction_result
                
                # Provide defaults for fields not in DB table
                if 'priority_factors' not in item_data or not item_data['priority_factors']:
                     item_data['priority_factors'] = {}
                if 'previous_reviewers' not in item_data or not item_data['previous_reviewers']:
                     item_data['previous_reviewers'] = []
                if 'customer_id' not in item_data:
                     item_data['customer_id'] = None
                
                # Convert status string to enum
                if 'status' in item_data and isinstance(item_data['status'], str):
                    item_data['status'] = ReviewStatus(item_data['status'])
                     
                result.append(ReviewItem(**item_data))
            except Exception as e:
                logger.error(f"Error hydrating history item {data.get('item_id')}: {e}")
        return result
    
    # -------------------------------------------------------------------------
    # Claim Operations
    # -------------------------------------------------------------------------
    
    async def claim_item(
        self, 
        item_id: str, 
        reviewer_id: str
    ) -> Tuple[bool, Optional[ReviewItem], Optional[str]]:
        """
        Atomically claim an item for review.
        
        Returns:
            (success, item, error_message)
        """
        async with self._lock:
            item = self._items.get(item_id)
            
            if not item:
                return False, None, "Item not found"
            
            if item.status not in (ReviewStatus.PENDING, ReviewStatus.EXPIRED):
                if item.assigned_to == reviewer_id:
                    return True, item, None  # Already claimed by this reviewer
                return False, None, f"Item already {item.status.value}"
            
            # Check if claim expired on another reviewer
            if item.claim_expires_at and utc_now() > item.claim_expires_at:
                # Release expired claim
                item.status = ReviewStatus.PENDING
                item.assigned_to = None
                item.assigned_at = None
                item.claim_expires_at = None
            
            # Claim the item
            previous_state = {"status": item.status.value, "assigned_to": item.assigned_to}
            
            item.status = ReviewStatus.ASSIGNED
            item.assigned_to = reviewer_id
            item.assigned_at = utc_now()
            item.claim_expires_at = utc_now() + self._claim_timeout
            item.review_attempts += 1
            
            # Update reviewer stats
            if reviewer_id not in self._reviewer_stats:
                self._reviewer_stats[reviewer_id] = ReviewerStats(reviewer_id=reviewer_id)
            self._reviewer_stats[reviewer_id].active_items_count += 1
            
            # Persist item state
            await ReviewRepository.upsert(asdict(item))
        
        # Audit log
        await self._audit_logger.log(
            item_id=item_id,
            document_id=item.document_id,
            actor=reviewer_id,
            action="claimed",
            details={"expires_at": item.claim_expires_at.isoformat()},
            previous_state=previous_state,
            new_state={"status": item.status.value, "assigned_to": reviewer_id}
        )
        
        # Notify WebSocket clients
        await self._broadcast({
            "event": "item.claimed",
            "data": {"item_id": item_id, "claimed_by": reviewer_id}
        })
        
        logger.info(f"Item {item_id} claimed by {reviewer_id}")
        return True, item, None
    
    async def release_all_claims(self) -> int:
        """Release ALL claimed items (Admin/Debug tool)."""
        async with self._lock:
            count = 0
            for item in self._items.values():
                if item.status == ReviewStatus.ASSIGNED:
                    item.status = ReviewStatus.PENDING
                    item.assigned_to = None
                    item.assigned_at = None
                    item.claim_expires_at = None
                    # Update DB
                    await ReviewRepository.upsert(asdict(item))
                    count += 1
            
            # Reset all reviewer active counts
            for stats in self._reviewer_stats.values():
                stats.active_items_count = 0
                
            return count
    
    async def release_item(
        self, 
        item_id: str, 
        reviewer_id: str,
        reason: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """Release an item back to the queue.
        
        Args:
            force: If True, skip the assignment check (admin operation).
        """
        async with self._lock:
            item = self._items.get(item_id)
            
            if not item:
                return False, "Item not found"
            
            if not force and item.assigned_to != reviewer_id:
                return False, "Item not assigned to this reviewer"
            
            previous_state = {"status": item.status.value, "assigned_to": item.assigned_to}
            
            # Release the item
            if item.assigned_to:
                item.previous_reviewers.append(item.assigned_to)
            
            item.status = ReviewStatus.PENDING
            item.assigned_to = None
            item.assigned_at = None
            item.claim_expires_at = None
            
            # Recalculate priority (might have changed)
            item.priority, item.priority_factors = self._priority_calculator.calculate(item)
            
            # Update reviewer stats
            if reviewer_id in self._reviewer_stats:
                self._reviewer_stats[reviewer_id].active_items_count = max(
                    0, self._reviewer_stats[reviewer_id].active_items_count - 1
                )
                
        # Persist to database
        await ReviewRepository.upsert(asdict(item))
        
        # Audit log
        await self._audit_logger.log(
            item_id=item_id,
            document_id=item.document_id,
            actor=reviewer_id,
            action="released",
            details={"reason": reason},
            previous_state=previous_state,
            new_state={"status": item.status.value, "assigned_to": None}
        )
        
        # Notify WebSocket clients
        await self._broadcast({
            "event": "item.released",
            "data": {"item_id": item_id, "priority": item.priority}
        })
        
        logger.info(f"Item {item_id} released by {reviewer_id}")
        return True, None
    
    # -------------------------------------------------------------------------
    # Review Submission
    # -------------------------------------------------------------------------
    
    async def submit_review(
        self,
        item_id: str,
        reviewer_id: str,
        decision: ReviewDecision,
        corrections: Optional[List[FieldCorrection]] = None,
        rejection_reason: Optional[str] = None,
        rejection_category: Optional[RejectionCategory] = None,
        started_at: Optional[datetime] = None
    ) -> Tuple[bool, Optional[ReviewResult], Optional[str]]:
        """Submit a review decision."""
        async with self._lock:
            item = self._items.get(item_id)
            
            if not item:
                return False, None, "Item not found"
            
            if item.assigned_to != reviewer_id:
                return False, None, "Item not assigned to this reviewer"
            
            # Create result
            result = ReviewResult(
                result_id=str(uuid.uuid4()),
                item_id=item_id,
                document_id=item.document_id,
                reviewer_id=reviewer_id,
                decision=decision,
                corrections=corrections or [],
                rejection_reason=rejection_reason,
                rejection_category=rejection_category,
                review_started_at=started_at or item.assigned_at or utc_now(),
                review_completed_at=utc_now()
            )
            
            # Store result
            self._results[result.result_id] = result
            
            # Update item status
            previous_state = {"status": item.status.value}
            item.status = ReviewStatus.COMPLETED
            
            # Track corrections
            if corrections:
                for correction in corrections:
                    self._corrections.append(correction)
                    
                    # Update extraction result with correction
                    if correction.field_name in item.extraction_result:
                        item.extraction_result[correction.field_name] = ExtractedFieldData(
                            value=correction.corrected_value,
                            confidence=1.0,
                            is_locked=True
                        )
            
            # Update reviewer stats
            if reviewer_id in self._reviewer_stats:
                stats = self._reviewer_stats[reviewer_id]
                stats.active_items_count = max(0, stats.active_items_count - 1)
                stats.completed_today += 1
                stats.total_review_time_seconds += result.review_duration_seconds
                
                if decision == ReviewDecision.APPROVE:
                    stats.approved_today += 1
                elif decision == ReviewDecision.CORRECT:
                    stats.corrected_today += 1
                elif decision == ReviewDecision.REJECT:
                    stats.rejected_today += 1
            
            # Persist to database
            await ReviewRepository.upsert(asdict(item))
        
        # Audit log
        await self._audit_logger.log(
            item_id=item_id,
            document_id=item.document_id,
            actor=reviewer_id,
            action="completed",
            details={
                "decision": decision.value,
                "corrections_count": len(corrections or []),
                "duration_seconds": result.review_duration_seconds
            },
            previous_state=previous_state,
            new_state={"status": item.status.value, "decision": decision.value}
        )
        
        # Notify WebSocket clients
        await self._broadcast({
            "event": "item.completed",
            "data": {
                "item_id": item_id,
                "decision": decision.value,
                "reviewer_id": reviewer_id
            }
        })
        
        logger.info(
            f"Review completed for {item_id} by {reviewer_id}: {decision.value} "
            f"({len(corrections or [])} corrections)"
        )
        return True, result, None
    
    async def escalate_item(
        self,
        item_id: str,
        reviewer_id: str,
        reason: str
    ) -> Tuple[bool, Optional[str]]:
        """Escalate an item to a supervisor."""
        async with self._lock:
            item = self._items.get(item_id)
            
            if not item:
                return False, "Item not found"
            
            if item.assigned_to != reviewer_id:
                return False, "Item not assigned to this reviewer"
            
            previous_state = {"status": item.status.value}
            previous_state = {"status": item.status.value}
            item.status = ReviewStatus.ESCALATED
            
            # Persist to database
            await ReviewRepository.upsert(asdict(item))
        
        # Audit log
        await self._audit_logger.log(
            item_id=item_id,
            document_id=item.document_id,
            actor=reviewer_id,
            action="escalated",
            details={"reason": reason},
            previous_state=previous_state,
            new_state={"status": item.status.value}
        )
        
        logger.info(f"Item {item_id} escalated by {reviewer_id}: {reason}")
        return True, None
    
    # -------------------------------------------------------------------------
    # Reviewer Operations
    # -------------------------------------------------------------------------
    
    async def get_my_items(self, reviewer_id: str) -> Dict[str, List[ReviewItem]]:
        """Get items assigned to a reviewer."""
        async with self._lock:
            items = list(self._items.values())
        
        active = [i for i in items if i.assigned_to == reviewer_id and i.status != ReviewStatus.COMPLETED]
        
        # Get completed today
        today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today = [
            r for r in self._results.values()
            if r.reviewer_id == reviewer_id and r.review_completed_at >= today_start
        ]
        completed_item_ids = {r.item_id for r in completed_today}
        completed_items = [self._items[iid] for iid in completed_item_ids if iid in self._items]
        
        return {
            "active": active,
            "completed_today": completed_items
        }
    
    async def get_my_stats(self, reviewer_id: str) -> ReviewerStats:
        """Get statistics for a reviewer."""
        if reviewer_id not in self._reviewer_stats:
            self._reviewer_stats[reviewer_id] = ReviewerStats(reviewer_id=reviewer_id)
        return self._reviewer_stats[reviewer_id]
    
    async def get_all_reviewers(self) -> List[ReviewerStats]:
        """Get statistics for all reviewers."""
        return list(self._reviewer_stats.values())
    
    # -------------------------------------------------------------------------
    # SLA Monitoring
    # -------------------------------------------------------------------------
    
    async def check_sla_breaches(self) -> List[ReviewItem]:
        """Check for items that have breached or are about to breach SLA."""
        async with self._lock:
            items = list(self._items.values())
        
        breached = []
        at_risk = []
        
        now = utc_now()
        
        for item in items:
            if item.status == ReviewStatus.COMPLETED:
                continue
            
            if item.sla_deadline <= now:
                if item.status != ReviewStatus.EXPIRED:
                    item.status = ReviewStatus.EXPIRED
                    await self._audit_logger.log(
                        item_id=item.item_id,
                        document_id=item.document_id,
                        actor="system",
                        action="sla_breached",
                        details={"sla_deadline": item.sla_deadline.isoformat()}
                    )
                    await self._broadcast({
                        "event": "sla.breach",
                        "data": {"item_id": item.item_id}
                    })
                breached.append(item)
            
            elif item.sla_deadline <= now + self._sla_warning_threshold:
                at_risk.append(item)
                await self._broadcast({
                    "event": "sla.warning",
                    "data": {
                        "item_id": item.item_id,
                        "minutes_remaining": item.sla_remaining_seconds // 60
                    }
                })
        
        return breached
    
    async def release_expired_claims(self):
        """Release items where the claim has expired."""
        now = utc_now()
        
        async with self._lock:
            for item in self._items.values():
                if item.status == ReviewStatus.ASSIGNED and item.claim_expires_at:
                    if now > item.claim_expires_at:
                        logger.info(f"Releasing expired claim on {item.item_id}")
                        if item.assigned_to:
                            item.previous_reviewers.append(item.assigned_to)
                            
                            # Update reviewer stats
                            if item.assigned_to in self._reviewer_stats:
                                self._reviewer_stats[item.assigned_to].active_items_count = max(
                                    0, self._reviewer_stats[item.assigned_to].active_items_count - 1
                                )
                        
                        item.status = ReviewStatus.PENDING
                        item.assigned_to = None
                        item.assigned_at = None
                        item.claim_expires_at = None
    
    # -------------------------------------------------------------------------
    # Audit Trail
    # -------------------------------------------------------------------------
    
    async def get_audit_trail(self, item_id: str) -> List[AuditEntry]:
        """Get audit trail for an item."""
        return await self._audit_logger.get_entries(item_id)
    
    # -------------------------------------------------------------------------
    # Corrections Analysis
    # -------------------------------------------------------------------------
    
    async def get_correction_stats(self) -> Dict[str, Any]:
        """Get statistics about corrections."""
        if not self._corrections:
            return {"total": 0, "by_field": {}, "by_type": {}}
        
        by_field = defaultdict(int)
        by_type = defaultdict(int)
        
        for c in self._corrections:
            by_field[c.field_name] += 1
            by_type[c.correction_type] += 1
        
        return {
            "total": len(self._corrections),
            "by_field": dict(by_field),
            "by_type": dict(by_type)
        }
    
    # -------------------------------------------------------------------------
    # WebSocket Support
    # -------------------------------------------------------------------------
    
    async def register_websocket(self, websocket: WebSocket):
        """Register a WebSocket client for real-time updates."""
        self._websocket_clients.add(websocket)
    
    async def unregister_websocket(self, websocket: WebSocket):
        """Unregister a WebSocket client."""
        self._websocket_clients.discard(websocket)
    
    async def _broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected WebSocket clients."""
        if not self._websocket_clients:
            return
        
        disconnected = set()
        
        for ws in self._websocket_clients:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)
        
        # Clean up disconnected clients
        for ws in disconnected:
            self._websocket_clients.discard(ws)


# API MODELS

class ClaimRequest(BaseModel):
    """Request to claim an item."""
    pass


class ReleaseRequest(BaseModel):
    """Request to release an item."""
    reason: Optional[str] = None


class CorrectionModel(BaseModel):
    """Field correction model."""
    field_name: str
    original_value: Any
    corrected_value: Any
    correction_type: str = "value_change"
    reviewer_notes: Optional[str] = None


class SubmitReviewRequest(BaseModel):
    """Request to submit a review."""
    decision: ReviewDecision
    corrections: List[CorrectionModel] = Field(default_factory=list)
    rejection_reason: Optional[str] = None
    rejection_category: Optional[RejectionCategory] = None


class EscalateRequest(BaseModel):
    """Request to escalate an item."""
    reason: str


# FASTAPI APPLICATION

def get_review_router(queue_manager: ReviewQueueManager) -> APIRouter:
    """Create APIRouter for review queue endpoints."""
    router = APIRouter()
    
    @router.get("/api/v1/review/queue")
    async def get_queue(
        status: Optional[str] = None,
        priority: Optional[int] = None,
        document_type: Optional[str] = None,
        sort: str = "priority",
        page: int = 1,
        limit: int = 20
    ):
        """Get pending review items."""
        status_enum = ReviewStatus(status) if status else None
        items, total = await queue_manager.get_queue(
            status=status_enum,
            priority=priority,
            document_type=document_type,
            sort_by=sort,
            page=page,
            limit=limit
        )
        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "page": page,
            "limit": limit,
            "has_more": page * limit < total
        }
    
    @router.get("/api/v1/review/queue/stats")
    async def get_stats():
        """Get queue statistics."""
        stats = await queue_manager.get_stats()
        return {
            "total_pending": stats.total_pending,
            "total_assigned": stats.total_assigned,
            "by_priority": stats.by_priority,
            "by_document_type": stats.by_document_type,
            "sla_at_risk": stats.sla_at_risk,
            "sla_breached": stats.sla_breached,
            "avg_wait_time_minutes": stats.avg_wait_time_minutes
        }
    
    @router.get("/api/v1/review/items/{item_id}")
    async def get_item(item_id: str):
        """Get a specific review item."""
        item = await queue_manager.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        return item.to_dict()
    
    @router.post("/api/v1/review/items/{item_id}/claim")
    async def claim_item(item_id: str, reviewer_id: str = "default_reviewer"):
        """Claim an item for review."""
        success, item, error = await queue_manager.claim_item(item_id, reviewer_id)
        if not success:
            raise HTTPException(status_code=400, detail=error)
        return {
            "success": True,
            "item": item.to_dict(),
            "expires_at": item.claim_expires_at.isoformat()
        }
    
    @router.post("/api/v1/review/items/{item_id}/release")
    async def release_item(item_id: str, request: ReleaseRequest, reviewer_id: str = "default_reviewer"):
        """Release an item back to the queue."""
        success, error = await queue_manager.release_item(item_id, reviewer_id, request.reason)
        if not success:
            raise HTTPException(status_code=400, detail=error)
        return {"success": True}
    
    @router.get("/api/v1/review/history")
    async def get_review_history(limit: int = 50):
        """Get processed document history."""
        items = await queue_manager.get_history(limit)
        return {"items": [asdict(item) for item in items]}

    @router.post("/api/v1/review/items/{item_id}/submit")
    async def submit_review(
        item_id: str, 
        request: SubmitReviewRequest,
        reviewer_id: str = "default_reviewer"
    ):
        """Submit a review decision."""
        corrections = [
            FieldCorrection(
                field_name=c.field_name,
                original_value=c.original_value,
                corrected_value=c.corrected_value,
                correction_type=c.correction_type,
                reviewer_notes=c.reviewer_notes
            )
            for c in request.corrections
        ]
        
        success, result, error = await queue_manager.submit_review(
            item_id=item_id,
            reviewer_id=reviewer_id,
            decision=request.decision,
            corrections=corrections,
            rejection_reason=request.rejection_reason,
            rejection_category=request.rejection_category
        )
        
        if not success:
            raise HTTPException(status_code=400, detail=error)
        
        return {
            "success": True,
            "result_id": result.result_id,
            "decision": result.decision.value,
            "corrections_count": len(result.corrections),
            "duration_seconds": result.review_duration_seconds
        }
    
    @router.post("/api/v1/review/items/{item_id}/escalate")
    async def escalate_item(
        item_id: str,
        request: EscalateRequest,
        reviewer_id: str = "default_reviewer"
    ):
        """Escalate an item to a supervisor."""
        success, error = await queue_manager.escalate_item(item_id, reviewer_id, request.reason)
        if not success:
            raise HTTPException(status_code=400, detail=error)
        return {"success": True}
    
    @router.get("/api/v1/review/my-items")
    async def get_my_items(reviewer_id: str = "default_reviewer"):
        """Get items assigned to the current reviewer."""
        items = await queue_manager.get_my_items(reviewer_id)
        return {
            "active": [i.to_dict() for i in items["active"]],
            "completed_today": [i.to_dict() for i in items["completed_today"]]
        }
    
    @router.get("/api/v1/review/my-stats")
    async def get_my_stats(reviewer_id: str = "default_reviewer"):
        """Get statistics for the current reviewer."""
        stats = await queue_manager.get_my_stats(reviewer_id)
        return {
            "reviewer_id": stats.reviewer_id,
            "active_items_count": stats.active_items_count,
            "completed_today": stats.completed_today,
            "approved_today": stats.approved_today,
            "corrected_today": stats.corrected_today,
            "rejected_today": stats.rejected_today,
            "avg_review_time_seconds": stats.avg_review_time_seconds
        }
    
    @router.get("/api/v1/review/audit/{item_id}")
    async def get_audit_trail(item_id: str):
        """Get audit trail for an item."""
        entries = await queue_manager.get_audit_trail(item_id)
        return {
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "timestamp": e.timestamp.isoformat(),
                    "actor": e.actor,
                    "action": e.action,
                    "details": e.details
                }
                for e in entries
            ]
        }
    
    @router.websocket("/api/v1/review/stream")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time updates."""
        await websocket.accept()
        await queue_manager.register_websocket(websocket)
        
        try:
            while True:
                # Keep connection alive
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            await queue_manager.unregister_websocket(websocket)
            
    @router.post("/api/v1/review/debug/release-all")
    async def debug_release_all():
        """DEBUG: Release all claimed items."""
        count = await queue_manager.release_all_claims()
        return {"released_count": count}

    return router


def create_review_api(queue_manager: ReviewQueueManager = None) -> FastAPI:
    """Create FastAPI application for review queue."""
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    import os
    
    if queue_manager is None:
        queue_manager = ReviewQueueManager()
    
    app = FastAPI(title="DocFlow Review API", version="1.0.0")
    
    # Include the router
    app.include_router(get_review_router(queue_manager))
    
    # Health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint for container orchestration."""
        return {
            "status": "healthy",
            "timestamp": utc_now().isoformat(),
            "version": "1.0.0"
        }
    
    # Serve static files if they exist (for production deployment)
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    if os.path.exists(static_dir):
        app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")
        
        @app.get("/")
        async def serve_spa():
            return FileResponse(os.path.join(static_dir, "index.html"))
    
    return app


# MAIN

async def main():
    """Test the review queue."""
    queue = ReviewQueueManager()
    
    # Add some test items
    print("Adding test items...")
    
    for i in range(5):
        extraction = {
            "invoice_number": ExtractedFieldData(f"INV-{i+1:03d}", 0.95),
            "vendor_name": ExtractedFieldData(f"Vendor {i+1}", 0.85 - i*0.1),
            "total_amount": ExtractedFieldData(1000 * (i+1), 0.90),
            "currency": ExtractedFieldData("USD", 0.98)
        }
        
        item = await queue.add_item(
            document_id=f"doc_{i+1:03d}",
            workflow_id="wf_001",
            extraction_result=extraction,
            document_preview_url=f"/preview/doc_{i+1:03d}.pdf",
            document_type="invoice",
            sla_hours=4 - i*0.5  # Varying SLA
        )
        print(f"  Added {item.item_id} (priority {item.priority})")
    
    print()
    
    # Get queue stats
    stats = await queue.get_stats()
    print(f"Queue Stats:")
    print(f"  Pending: {stats.total_pending}")
    print(f"  By Priority: {stats.by_priority}")
    print(f"  At Risk: {stats.sla_at_risk}")
    print()
    
    # Get queue
    items, total = await queue.get_queue(sort_by="priority", limit=10)
    print(f"Queue ({total} items):")
    for item in items:
        print(f"  {item.item_id}: P{item.priority} | SLA: {item.sla_remaining_seconds//60}min | {item.low_confidence_fields}")
    print()
    
    # Claim an item
    print("Claiming first item...")
    success, item, error = await queue.claim_item(items[0].item_id, "reviewer_001")
    if success:
        print(f"  Claimed {item.item_id}")
    print()
    
    # Submit review with corrections
    print("Submitting review with correction...")
    corrections = [
        FieldCorrection(
            field_name="vendor_name",
            original_value="Vendor 1",
            corrected_value="Acme Corporation",
            correction_type="value_change",
            reviewer_notes="Fixed vendor name"
        )
    ]
    success, result, error = await queue.submit_review(
        item_id=items[0].item_id,
        reviewer_id="reviewer_001",
        decision=ReviewDecision.CORRECT,
        corrections=corrections
    )
    if success:
        print(f"  Review submitted: {result.decision.value}")
        print(f"  Duration: {result.review_duration_seconds}s")
    print()
    
    # Get reviewer stats
    stats = await queue.get_my_stats("reviewer_001")
    print(f"Reviewer Stats:")
    print(f"  Completed Today: {stats.completed_today}")
    print(f"  Corrected: {stats.corrected_today}")
    print()
    
    # Get correction stats
    correction_stats = await queue.get_correction_stats()
    print(f"Correction Stats:")
    print(f"  Total: {correction_stats['total']}")
    print(f"  By Field: {correction_stats['by_field']}")


if __name__ == "__main__":
    asyncio.run(main())
