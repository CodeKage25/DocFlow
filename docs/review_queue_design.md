# Review Queue System Design

## Executive Summary

This document describes the architecture for a human review queue system that enables reviewers to validate, correct, and approve document extractions with SLA tracking and feedback loops.

---

## 1. Data Model

### 1.1 ReviewItem Structure

```python
@dataclass
class ReviewItem:
    """A document pending human review."""
    
    # Identification
    item_id: str                      # Unique review item ID
    document_id: str                  # Reference to source document
    workflow_id: str                  # Workflow that produced this item
    
    # Content
    extraction_result: Dict[str, Any] # Extracted fields with confidence
    document_preview_url: str         # URL to view original document
    low_confidence_fields: List[str]  # Fields needing attention
    
    # Priority & SLA
    priority: int                     # 1 (highest) to 5 (lowest)
    priority_factors: Dict[str, float] # Breakdown of priority calculation
    created_at: datetime              # When item entered queue
    sla_deadline: datetime            # Must be reviewed by this time
    sla_remaining_seconds: int        # Dynamic countdown
    
    # Assignment
    status: ReviewStatus              # PENDING | ASSIGNED | IN_REVIEW | COMPLETED
    assigned_to: Optional[str]        # Reviewer user ID
    assigned_at: Optional[datetime]   # When claimed
    
    # Tracking
    review_attempts: int              # Times this item was claimed
    previous_reviewers: List[str]     # Reviewers who released this item
    
    # Metadata
    document_type: str                # INVOICE | CONTRACT | RECEIPT | PO
    source_system: str                # Origin of document
    customer_id: Optional[str]        # Associated customer


class ReviewStatus(Enum):
    PENDING = "pending"               # Waiting in queue
    ASSIGNED = "assigned"             # Claimed by reviewer
    IN_REVIEW = "in_review"           # Actively being reviewed
    COMPLETED = "completed"           # Review finished
    ESCALATED = "escalated"           # Requires supervisor
    EXPIRED = "expired"               # SLA breached
```

### 1.2 ReviewResult Structure

```python
@dataclass
class ReviewResult:
    """Outcome of a human review."""
    
    item_id: str
    document_id: str
    reviewer_id: str
    
    # Decision
    decision: ReviewDecision          # APPROVE | CORRECT | REJECT
    
    # Corrections (if any)
    corrections: List[FieldCorrection]
    
    # Rejection reason (if rejected)
    rejection_reason: Optional[str]
    rejection_category: Optional[str] # ILLEGIBLE | INVALID | DUPLICATE | OTHER
    
    # Metrics
    review_started_at: datetime
    review_completed_at: datetime
    review_duration_seconds: int
    
    # Quality
    confidence_before: float
    confidence_after: float


@dataclass
class FieldCorrection:
    """A single field correction made by reviewer."""
    
    field_name: str
    original_value: Any
    corrected_value: Any
    correction_type: str              # VALUE_CHANGE | FORMAT_FIX | MISSING_VALUE
    reviewer_notes: Optional[str]
```

### 1.3 Audit Trail

```python
@dataclass
class ReviewAuditEntry:
    """Audit trail entry for review actions."""
    
    entry_id: str
    timestamp: datetime
    item_id: str
    document_id: str
    actor: str                        # User or system
    action: str                       # CREATED | CLAIMED | RELEASED | COMPLETED | ESCALATED
    details: Dict[str, Any]
    previous_state: Optional[Dict]
    new_state: Dict
```

---

## 2. Queue Operations

### 2.1 Priority Calculation

Priority determines queue order. Lower number = higher priority.

```python
def calculate_priority(item: ReviewItem) -> int:
    """
    Calculate review priority based on multiple factors.
    Returns priority 1-5 (1 = highest).
    """
    score = 0.0
    factors = {}
    
    # Factor 1: SLA Urgency (0-40 points)
    sla_hours_remaining = (item.sla_deadline - datetime.utcnow()).total_seconds() / 3600
    if sla_hours_remaining <= 1:
        urgency_score = 40
    elif sla_hours_remaining <= 2:
        urgency_score = 30
    elif sla_hours_remaining <= 4:
        urgency_score = 20
    elif sla_hours_remaining <= 8:
        urgency_score = 10
    else:
        urgency_score = 0
    score += urgency_score
    factors["sla_urgency"] = urgency_score
    
    # Factor 2: Low Confidence Penalty (0-30 points)
    avg_confidence = sum(
        item.extraction_result[f]["confidence"] 
        for f in item.low_confidence_fields
    ) / max(len(item.low_confidence_fields), 1)
    confidence_penalty = (1 - avg_confidence) * 30
    score += confidence_penalty
    factors["confidence_penalty"] = confidence_penalty
    
    # Factor 3: Document Value (0-20 points)
    total_amount = item.extraction_result.get("total_amount", {}).get("value", 0)
    if total_amount >= 100000:
        value_score = 20
    elif total_amount >= 10000:
        value_score = 15
    elif total_amount >= 1000:
        value_score = 10
    else:
        value_score = 5
    score += value_score
    factors["document_value"] = value_score
    
    # Factor 4: Queue Time Boost (0-10 points, increases over time)
    queue_hours = (datetime.utcnow() - item.created_at).total_seconds() / 3600
    time_boost = min(queue_hours * 2, 10)
    score += time_boost
    factors["queue_time_boost"] = time_boost
    
    # Convert score to priority (higher score = higher priority = lower number)
    if score >= 70:
        priority = 1  # Critical
    elif score >= 50:
        priority = 2  # High
    elif score >= 30:
        priority = 3  # Medium
    elif score >= 15:
        priority = 4  # Low
    else:
        priority = 5  # Very Low
    
    return priority, factors
```

### 2.2 SLA-Aware Ordering

```python
class QueueOrdering:
    """Ordering strategy for the review queue."""
    
    @staticmethod
    def sla_first(items: List[ReviewItem]) -> List[ReviewItem]:
        """Order by SLA deadline (closest first)."""
        return sorted(items, key=lambda x: x.sla_deadline)
    
    @staticmethod
    def priority_first(items: List[ReviewItem]) -> List[ReviewItem]:
        """Order by priority (1 first), then by SLA."""
        return sorted(items, key=lambda x: (x.priority, x.sla_deadline))
    
    @staticmethod
    def balanced(items: List[ReviewItem]) -> List[ReviewItem]:
        """
        Balanced ordering considering both priority and SLA.
        Items close to SLA breach get boosted to top regardless of priority.
        """
        now = datetime.utcnow()
        
        def sort_key(item: ReviewItem):
            hours_to_sla = (item.sla_deadline - now).total_seconds() / 3600
            
            # Boost items within 1 hour of SLA to top
            if hours_to_sla <= 1:
                return (0, hours_to_sla)
            else:
                return (item.priority, hours_to_sla)
        
        return sorted(items, key=sort_key)
```

### 2.3 Load-Balanced Assignment

```python
class ReviewerLoadBalancer:
    """Balances review assignment across reviewers."""
    
    def __init__(self):
        self.reviewer_stats: Dict[str, ReviewerStats] = {}
    
    def get_available_reviewer(
        self, 
        reviewers: List[str],
        item: ReviewItem
    ) -> Optional[str]:
        """
        Select best available reviewer for an item.
        Considers workload, expertise, and availability.
        """
        candidates = []
        
        for reviewer_id in reviewers:
            stats = self.reviewer_stats.get(reviewer_id)
            if not stats or not stats.is_available:
                continue
            
            # Calculate assignment score
            score = 0
            
            # Favor reviewers with fewer active items
            score -= stats.active_items_count * 10
            
            # Favor reviewers with expertise in this document type
            if item.document_type in stats.expertise:
                score += 20
            
            # Favor faster reviewers (lower avg time = higher score)
            if stats.avg_review_time_seconds:
                speed_bonus = max(0, 120 - stats.avg_review_time_seconds) / 10
                score += speed_bonus
            
            candidates.append((reviewer_id, score))
        
        if not candidates:
            return None
        
        # Return reviewer with highest score
        return max(candidates, key=lambda x: x[1])[0]


@dataclass
class ReviewerStats:
    """Statistics for a reviewer."""
    reviewer_id: str
    is_available: bool
    active_items_count: int
    completed_today: int
    avg_review_time_seconds: float
    expertise: Set[str]  # Document types
```

---

## 3. API Design

### 3.1 REST Endpoints

```yaml
# Review Queue API
/api/v1/review:

  # Queue Operations
  GET /queue:
    description: Get pending review items
    parameters:
      - status: filter by status (pending, assigned, etc.)
      - priority: filter by priority (1-5)
      - document_type: filter by type
      - sort: ordering (sla, priority, created)
      - page: pagination offset
      - limit: items per page (default 20, max 100)
    response:
      items: List[ReviewItem]
      total: int
      has_more: bool

  GET /queue/stats:
    description: Queue statistics
    response:
      total_pending: int
      by_priority: Dict[int, int]
      by_document_type: Dict[str, int]
      sla_at_risk: int (within 1 hour)
      avg_wait_time_minutes: float

  # Item Operations
  GET /items/{item_id}:
    description: Get single review item with full details
    response: ReviewItem with document content

  POST /items/{item_id}/claim:
    description: Claim item for review (atomic operation)
    response:
      success: bool
      item: ReviewItem
      expires_at: datetime (claim timeout)

  POST /items/{item_id}/release:
    description: Release claimed item back to queue
    body:
      reason: string (optional)
    response:
      success: bool

  POST /items/{item_id}/submit:
    description: Submit review decision
    body:
      decision: APPROVE | CORRECT | REJECT
      corrections: List[FieldCorrection] (if CORRECT)
      rejection_reason: string (if REJECT)
      rejection_category: string (if REJECT)
    response:
      success: bool
      result: ReviewResult

  POST /items/{item_id}/escalate:
    description: Escalate to supervisor
    body:
      reason: string
    response:
      success: bool

  # Reviewer Operations
  GET /my-items:
    description: Get items assigned to current user
    response:
      active: List[ReviewItem]
      completed_today: List[ReviewItem]

  GET /my-stats:
    description: Get current user's review statistics
    response:
      today:
        completed: int
        approved: int
        corrected: int
        rejected: int
        avg_time_seconds: float
      this_week: {...}
      this_month: {...}

  # Admin Operations (requires admin role)
  POST /items/{item_id}/reassign:
    description: Reassign item to different reviewer
    body:
      reviewer_id: string
    response:
      success: bool

  GET /reviewers:
    description: List all reviewers with stats
    response:
      reviewers: List[ReviewerStats]

  GET /audit/{item_id}:
    description: Get audit trail for item
    response:
      entries: List[ReviewAuditEntry]
```

### 3.2 WebSocket Events

```yaml
# Real-time updates via WebSocket
ws://api/v1/review/stream:

  # Server -> Client Events
  queue.updated:
    description: Queue state changed
    data:
      total_pending: int
      sla_at_risk: int

  item.claimed:
    description: Item was claimed (removed from available queue)
    data:
      item_id: string
      claimed_by: string

  item.released:
    description: Item released back to queue
    data:
      item_id: string
      priority: int

  item.completed:
    description: Review completed
    data:
      item_id: string
      decision: string

  sla.warning:
    description: Item approaching SLA deadline
    data:
      item_id: string
      minutes_remaining: int

  sla.breach:
    description: Item breached SLA
    data:
      item_id: string

  # Client -> Server Events
  subscribe:
    description: Subscribe to specific events
    data:
      events: List[string]

  heartbeat:
    description: Keep connection alive
```

---

## 4. Feedback Loop

### 4.1 Correction Aggregation

Corrections are aggregated to identify patterns and improve extraction:

```python
class CorrectionAggregator:
    """Aggregates corrections for model improvement."""
    
    def aggregate_corrections(
        self, 
        corrections: List[FieldCorrection],
        time_window_days: int = 30
    ) -> CorrectionReport:
        """
        Analyze correction patterns over time.
        """
        report = CorrectionReport()
        
        # Group by field
        by_field = defaultdict(list)
        for c in corrections:
            by_field[c.field_name].append(c)
        
        for field_name, field_corrections in by_field.items():
            stats = FieldCorrectionStats(
                field_name=field_name,
                total_corrections=len(field_corrections),
                correction_rate=len(field_corrections) / total_extractions,
                common_patterns=self._find_patterns(field_corrections),
                avg_confidence_at_error=self._avg_confidence(field_corrections)
            )
            report.field_stats[field_name] = stats
        
        return report
    
    def _find_patterns(
        self, 
        corrections: List[FieldCorrection]
    ) -> List[CorrectionPattern]:
        """Identify common correction patterns."""
        patterns = []
        
        # Pattern: Consistent format fixes
        format_corrections = [
            c for c in corrections 
            if c.correction_type == "FORMAT_FIX"
        ]
        if len(format_corrections) > 10:
            patterns.append(CorrectionPattern(
                pattern_type="format_issue",
                frequency=len(format_corrections),
                examples=format_corrections[:5]
            ))
        
        # Pattern: Similar value corrections
        # (e.g., model consistently misreads "0" as "O")
        value_changes = defaultdict(int)
        for c in corrections:
            if c.correction_type == "VALUE_CHANGE":
                key = (str(c.original_value), str(c.corrected_value))
                value_changes[key] += 1
        
        for (orig, new), count in value_changes.items():
            if count >= 5:
                patterns.append(CorrectionPattern(
                    pattern_type="systematic_error",
                    frequency=count,
                    description=f"'{orig}' frequently corrected to '{new}'"
                ))
        
        return patterns
```

### 4.2 Model Improvement Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FEEDBACK LOOP PIPELINE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   1. COLLECT CORRECTIONS                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ Review System ──▶ Correction Store ──▶ Aggregation Job (daily)      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
│   2. ANALYZE PATTERNS                                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ Pattern Detection ──▶ Threshold Check ──▶ Alert if high error rate  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
│   3. UPDATE PROMPTS (automated if pattern is clear)                         │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ Generate Prompt Adjustments ──▶ A/B Test ──▶ Deploy if improved     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
│   4. FINE-TUNING (periodic, if volume warrants)                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ Build Training Dataset ──▶ Fine-tune Model ──▶ Evaluate ──▶ Deploy  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Confidence Calibration

Use corrections to calibrate confidence scores:

```python
class ConfidenceCalibrator:
    """Calibrates LLM confidence scores based on actual accuracy."""
    
    def calibrate(
        self, 
        extractions: List[ExtractionWithOutcome]
    ) -> CalibrationModel:
        """
        Build a calibration model from historical data.
        Maps raw confidence to calibrated probability of correctness.
        """
        # Bucket extractions by confidence
        buckets = defaultdict(lambda: {"total": 0, "correct": 0})
        
        for e in extractions:
            bucket = round(e.confidence, 1)  # 0.0, 0.1, ..., 1.0
            buckets[bucket]["total"] += 1
            if e.was_correct:
                buckets[bucket]["correct"] += 1
        
        # Calculate actual accuracy per bucket
        calibration = {}
        for bucket, stats in buckets.items():
            if stats["total"] >= 100:  # Minimum sample size
                calibration[bucket] = stats["correct"] / stats["total"]
        
        return CalibrationModel(calibration)
    
    def apply(self, raw_confidence: float, model: CalibrationModel) -> float:
        """Apply calibration to a raw confidence score."""
        bucket = round(raw_confidence, 1)
        if bucket in model.calibration:
            return model.calibration[bucket]
        return raw_confidence  # Fallback to raw if not enough data
```

---

## 5. SLA Monitoring

### 5.1 SLA Definitions

```yaml
sla_definitions:
  review_items:
    # By priority
    priority_1:
      target_time_hours: 1
      warning_threshold: 0.5  # 30 minutes
      breach_severity: critical
    
    priority_2:
      target_time_hours: 2
      warning_threshold: 1.0
      breach_severity: high
    
    priority_3:
      target_time_hours: 4
      warning_threshold: 2.0
      breach_severity: medium
    
    priority_4:
      target_time_hours: 8
      warning_threshold: 4.0
      breach_severity: low
    
    priority_5:
      target_time_hours: 24
      warning_threshold: 12.0
      breach_severity: low
  
  # Global SLA metrics
  global:
    review_completion_rate: 0.999  # 99.9% within SLA
    avg_review_time_seconds: 120   # Target 2 minutes per item
    max_queue_depth: 500           # Alert if exceeded
```

### 5.2 SLA Monitoring Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SLA MONITORING DASHBOARD                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   CURRENT STATUS                                                             │
│   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌─────────────┐ │
│   │  Queue Depth  │  │  At Risk      │  │  Breached     │  │ Compliance  │ │
│   │     247       │  │     12        │  │      2        │  │   99.2%     │ │
│   │   ▼ 15%       │  │   ▲ 3         │  │   ▲ 1         │  │   ▼ 0.1%    │ │
│   └───────────────┘  └───────────────┘  └───────────────┘  └─────────────┘ │
│                                                                              │
│   BY PRIORITY                                                                │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ P1 ████████░░ 12 items (2 at risk)                                  │   │
│   │ P2 ████████████████░░░░ 45 items (5 at risk)                       │   │
│   │ P3 ████████████████████████████░░░░░░ 89 items (4 at risk)         │   │
│   │ P4 ████████████████████████████████████░░░░░░░░ 72 items (1 at risk)│   │
│   │ P5 ██████████████░░░░ 29 items (0 at risk)                         │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   REVIEWER PERFORMANCE                                                       │
│   ┌──────────────┬──────────┬──────────┬──────────┬──────────┐             │
│   │ Reviewer     │ Active   │ Today    │ Avg Time │ SLA %    │             │
│   ├──────────────┼──────────┼──────────┼──────────┼──────────┤             │
│   │ @alice       │ 3        │ 45       │ 1m 32s   │ 100%     │             │
│   │ @bob         │ 2        │ 38       │ 2m 15s   │ 97.4%    │             │
│   │ @charlie     │ 4        │ 52       │ 1m 48s   │ 100%     │             │
│   └──────────────┴──────────┴──────────┴──────────┴──────────┘             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REVIEW QUEUE ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         API GATEWAY                                  │   │
│   │   REST: /api/v1/review/*    WebSocket: ws://api/v1/review/stream   │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│              ┌─────────────────────┼─────────────────────┐                  │
│              ▼                     ▼                     ▼                  │
│   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐          │
│   │  Queue Manager  │   │  Review Handler │   │  Stats Service  │          │
│   │                 │   │                 │   │                 │          │
│   │ - Priority calc │   │ - Claim/Release │   │ - Aggregation   │          │
│   │ - Ordering      │   │ - Submit review │   │ - Reporting     │          │
│   │ - Load balance  │   │ - Corrections   │   │ - Alerts        │          │
│   └────────┬────────┘   └────────┬────────┘   └────────┬────────┘          │
│            │                     │                     │                    │
│            └─────────────────────┼─────────────────────┘                    │
│                                  ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         DATA LAYER                                   │   │
│   │                                                                      │   │
│   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │   │
│   │  │Review Queue │  │  Results    │  │   Audit     │  │ Corrections│ │   │
│   │  │  (Redis)    │  │  (Postgres) │  │  (Postgres) │  │ (Postgres) │ │   │
│   │  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                  │                                          │
│                                  ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                      FEEDBACK PIPELINE                               │   │
│   │                                                                      │   │
│   │  Corrections ──▶ Aggregation ──▶ Analysis ──▶ Model Updates         │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```
