# Extraction Module Architecture

## Executive Summary

This document describes the architecture for a production-grade document extraction module designed to process 5,000+ documents per hour with 99.9% uptime, sub-30-second P95 latency, and zero data loss guarantee.

---

## 1. Module Interface & Data Flow

### 1.1 Input Contract

```python
@dataclass
class DocumentInput:
    """Input contract for document processing."""
    document_id: str                    # Unique identifier (UUID)
    content: bytes                      # Raw document bytes (PDF)
    content_hash: str                   # SHA-256 hash for idempotency
    document_type: DocumentType         # INVOICE | CONTRACT | RECEIPT | PO
    metadata: Dict[str, Any]            # Source system metadata
    processing_config: ProcessingConfig # Extraction configuration
    locked_fields: Set[str]             # Fields with manual corrections
    priority: ProcessingPriority        # HIGH | NORMAL | LOW
    sla_deadline: datetime              # Processing must complete by
```

### 1.2 Output Contract

```python
@dataclass
class ExtractionResult:
    """Output contract for extraction results."""
    document_id: str
    status: ProcessingStatus            # COMPLETED | FAILED | REVIEW_PENDING
    extracted_fields: Dict[str, ExtractedField]
    confidence_score: float             # Overall extraction confidence [0-1]
    validation_results: List[ValidationResult]
    processing_metadata: ProcessingMetadata
    output_paths: OutputPaths           # Parquet and JSON file paths
    error: Optional[ErrorDetails]       # Error info if failed

@dataclass
class ExtractedField:
    """Individual field extraction result."""
    field_name: str
    value: Any                          # Typed value
    raw_value: str                      # Original extracted text
    confidence: float                   # Field-level confidence [0-1]
    source_location: SourceLocation     # Page, bounding box
    is_locked: bool                     # Was this field manually corrected?
    extraction_method: str              # LLM | OCR | RULE_BASED
```

### 1.3 Processing Pipeline Stages

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   INGEST    │───▶│   EXTRACT   │───▶│  VALIDATE   │───▶│   OUTPUT    │
│             │    │             │    │             │    │             │
│ - Hash doc  │    │ - OCR/Parse │    │ - Schema    │    │ - Parquet   │
│ - Check dup │    │ - LLM call  │    │ - Business  │    │ - JSON      │
│ - Lock check│    │ - Merge     │    │ - Threshold │    │ - Audit log │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
       │                  │                  │                  │
       ▼                  ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        STATE MANAGER                                 │
│  - Processing status tracking                                        │
│  - Idempotency cache                                                │
│  - Locked fields registry                                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Idempotency Strategy

### 2.1 Document Fingerprinting

Every document is assigned a deterministic fingerprint based on:

```python
def compute_fingerprint(document: DocumentInput) -> str:
    """Compute idempotent document fingerprint."""
    components = [
        document.content_hash,           # SHA-256 of raw bytes
        document.document_type.value,    # Document type
        json.dumps(document.processing_config.dict(), sort_keys=True),
        str(sorted(document.locked_fields)),  # Locked fields affect output
        EXTRACTION_MODEL_VERSION,        # Model version for reproducibility
    ]
    return hashlib.sha256("|".join(components).encode()).hexdigest()
```

### 2.2 Idempotency Cache

```python
class IdempotencyCache:
    """Cache for idempotent processing results."""
    
    async def get(self, fingerprint: str) -> Optional[ExtractionResult]:
        """Retrieve cached result if exists and not expired."""
        
    async def set(self, fingerprint: str, result: ExtractionResult, ttl: int):
        """Store result with TTL for eventual cleanup."""
        
    async def invalidate(self, document_id: str):
        """Invalidate cache when locked fields change."""
```

### 2.3 Re-processing Behavior

| Scenario | Behavior |
|----------|----------|
| Same content, same config | Return cached result |
| Same content, different config | Reprocess, new cache entry |
| Same content, locked fields changed | Reprocess, merge with locked values |
| Content changed | Reprocess as new document |

---

## 3. State Management

### 3.1 Processing Status State Machine

```
                    ┌──────────────────┐
                    │                  │
                    ▼                  │
┌─────────┐    ┌─────────────┐    ┌────┴────┐
│ QUEUED  │───▶│ PROCESSING  │───▶│ FAILED  │
└─────────┘    └─────────────┘    └─────────┘
                    │                  │
                    │    retry < max   │
                    │◀─────────────────┘
                    │
                    ▼
            ┌───────────────┐
            │               │
     ┌──────┴─────┐   ┌─────┴──────┐
     ▼            ▼   ▼            ▼
┌─────────┐  ┌────────────────┐
│COMPLETED│  │ REVIEW_PENDING │
└─────────┘  └────────────────┘
                    │
                    ▼ (after human review)
              ┌─────────┐
              │COMPLETED│
              └─────────┘
```

### 3.2 Status Transitions

```python
class ProcessingStatus(Enum):
    QUEUED = "queued"              # Waiting in processing queue
    PROCESSING = "processing"      # Active extraction
    COMPLETED = "completed"        # Successfully processed
    FAILED = "failed"              # Failed after all retries
    REVIEW_PENDING = "review_pending"  # Needs human review

VALID_TRANSITIONS = {
    ProcessingStatus.QUEUED: {ProcessingStatus.PROCESSING},
    ProcessingStatus.PROCESSING: {
        ProcessingStatus.COMPLETED,
        ProcessingStatus.FAILED,
        ProcessingStatus.REVIEW_PENDING,
    },
    ProcessingStatus.FAILED: {ProcessingStatus.QUEUED},  # Retry
    ProcessingStatus.REVIEW_PENDING: {ProcessingStatus.COMPLETED},
}
```

### 3.3 Field-Level Locking

```python
@dataclass
class FieldLock:
    """Represents a locked field that must be preserved."""
    field_name: str
    locked_value: Any
    locked_by: str           # User ID who made correction
    locked_at: datetime
    original_value: Any      # What the system extracted
    reason: Optional[str]    # Why was it corrected?

class FieldLockManager:
    """Manages field-level locks to prevent overwriting corrections."""
    
    async def is_locked(self, document_id: str, field_name: str) -> bool:
        """Check if field is locked."""
    
    async def get_locked_value(self, document_id: str, field_name: str) -> Any:
        """Get the locked value to use instead of extraction."""
    
    async def lock_field(self, document_id: str, field_name: str, 
                        value: Any, user_id: str, reason: str = None):
        """Lock a field after manual correction."""
    
    async def merge_with_locks(self, document_id: str, 
                               extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Merge extracted values with locked fields (locked wins)."""
```

### 3.4 Field Preservation Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     EXTRACTION COMPLETE                          │
│                                                                  │
│  Extracted: {invoice_number: "INV-001", total: 1234.56}         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     CHECK LOCKED FIELDS                          │
│                                                                  │
│  Locked: {total: 1234.00}  (manually corrected)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MERGE (LOCKED WINS)                          │
│                                                                  │
│  Final: {invoice_number: "INV-001", total: 1234.00}             │
│         └── from extraction ──┘  └── preserved ──┘              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Error Handling & Scalability

### 4.1 Retry Strategy

```python
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.1  # ±10% randomization

def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate delay with exponential backoff and jitter."""
    delay = min(
        config.base_delay_seconds * (config.exponential_base ** attempt),
        config.max_delay_seconds
    )
    jitter = delay * config.jitter_factor * (2 * random.random() - 1)
    return delay + jitter
```

### 4.2 Circuit Breaker Pattern

```python
class CircuitBreaker:
    """Protects external services from cascading failures."""
    
    def __init__(
        self,
        failure_threshold: int = 5,      # Failures before opening
        recovery_timeout: float = 30.0,  # Seconds before half-open
        success_threshold: int = 3,      # Successes to close
    ):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
    
    async def call(self, func: Callable, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
            else:
                raise CircuitOpenError("Circuit breaker is open")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
```

### 4.3 Circuit States

```
        failures >= threshold
    ┌───────────────────────────┐
    │                           ▼
┌───────┐                 ┌──────────┐
│ CLOSED│                 │   OPEN   │
└───────┘                 └──────────┘
    ▲                           │
    │ successes >= threshold    │ timeout elapsed
    │                           ▼
    │                    ┌────────────┐
    └────────────────────│ HALF-OPEN  │
              success    └────────────┘
                               │
                               │ failure
                               ▼
                         ┌──────────┐
                         │   OPEN   │
                         └──────────┘
```

### 4.4 Scalability Design (5,000 docs/hour)

#### Concurrency Model

```python
class ExtractionPool:
    """Manages concurrent document processing."""
    
    def __init__(
        self,
        max_concurrent: int = 100,        # Max parallel extractions
        llm_rate_limit: int = 50,         # LLM calls per second
        queue_size: int = 10000,          # Max queued documents
    ):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = TokenBucketRateLimiter(llm_rate_limit)
        self.queue = asyncio.Queue(maxsize=queue_size)
```

#### Throughput Calculation

| Metric | Value | Notes |
|--------|-------|-------|
| Target throughput | 5,000 docs/hour | ~1.4 docs/second |
| P95 latency target | 30 seconds | Single document |
| Concurrent workers | 100 | Saturates at peak |
| Avg processing time | ~5 seconds | LLM + validation |
| Theoretical max | 72,000 docs/hour | With 100 workers |
| Safety margin | 14x | Handles burst loads |

#### Resource Pooling

```python
class ResourcePool:
    """Manages shared resources for efficient processing."""
    
    http_client: aiohttp.ClientSession    # Connection pooling
    llm_clients: List[MistralClient]      # Multiple API clients
    pdf_processor_pool: ProcessPoolExecutor  # CPU-bound PDF parsing
```

---

## 5. LLM Integration Architecture

### 5.1 Extraction Prompt Strategy

```python
EXTRACTION_PROMPT = """
You are a document extraction specialist. Extract structured data from the 
following invoice document.

DOCUMENT TYPE: {document_type}
DOCUMENT TEXT:
{document_text}

Extract the following fields with their values. For each field, provide:
- The extracted value (or null if not found)
- Your confidence level (0.0 to 1.0)
- The source text that led to this extraction

Required fields:
{field_definitions}

Respond in the following JSON format:
{output_schema}

Be precise and only extract information explicitly present in the document.
If a field is ambiguous, set confidence below 0.7.
"""
```

### 5.2 Confidence Calibration

| Confidence Range | Interpretation | Action |
|-----------------|----------------|--------|
| 0.95 - 1.00 | Very high confidence | Auto-approve |
| 0.80 - 0.94 | High confidence | Auto-approve with audit |
| 0.60 - 0.79 | Medium confidence | Route to review queue |
| 0.00 - 0.59 | Low confidence | Require manual review |

---

## 6. Data Flow Diagram

```
                                    DOCUMENT PROCESSING PIPELINE
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│  ┌─────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────────┐   │
│  │  API    │────▶│  INGESTION  │────▶│  EXTRACTION │────▶│   VALIDATION    │   │
│  │ Gateway │     │   Service   │     │   Service   │     │    Service      │   │
│  └─────────┘     └─────────────┘     └─────────────┘     └─────────────────┘   │
│       │                │                   │                     │              │
│       │                ▼                   ▼                     ▼              │
│       │         ┌───────────┐       ┌───────────┐         ┌───────────┐        │
│       │         │ Document  │       │   LLM     │         │ Business  │        │
│       │         │  Storage  │       │ Provider  │         │  Rules    │        │
│       │         └───────────┘       │ (Mistral) │         │  Engine   │        │
│       │                             └───────────┘         └───────────┘        │
│       │                                                          │              │
│       │                                                          ▼              │
│       │     ┌──────────────────────────────────────────────────────────────┐   │
│       │     │                      OUTPUT GENERATOR                         │   │
│       │     │  ┌─────────────┐                      ┌─────────────┐         │   │
│       │     │  │   Parquet   │                      │    JSON     │         │   │
│       │     │  │   Writer    │                      │   Writer    │         │   │
│       │     │  └─────────────┘                      └─────────────┘         │   │
│       │     └──────────────────────────────────────────────────────────────┘   │
│       │                                     │                                   │
│       │                                     ▼                                   │
│       │     ┌──────────────────────────────────────────────────────────────┐   │
│       └────▶│                     STATE MANAGER                             │   │
│             │  - Idempotency Cache    - Processing Status                   │   │
│             │  - Locked Fields        - Audit Trail                         │   │
│             └──────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Audit Trail

### 7.1 Audit Event Schema

```python
@dataclass
class AuditEvent:
    """Immutable audit record for compliance."""
    event_id: str
    timestamp: datetime
    document_id: str
    event_type: AuditEventType
    actor: str                    # System or user ID
    details: Dict[str, Any]
    previous_state: Optional[Dict[str, Any]]
    new_state: Dict[str, Any]

class AuditEventType(Enum):
    DOCUMENT_RECEIVED = "document_received"
    EXTRACTION_STARTED = "extraction_started"
    EXTRACTION_COMPLETED = "extraction_completed"
    EXTRACTION_FAILED = "extraction_failed"
    FIELD_LOCKED = "field_locked"
    FIELD_UNLOCKED = "field_unlocked"
    REVIEW_ASSIGNED = "review_assigned"
    REVIEW_COMPLETED = "review_completed"
    OUTPUT_GENERATED = "output_generated"
```

### 7.2 Lineage Tracking

Every output record includes complete lineage:

```json
{
  "lineage": {
    "document_id": "doc-123",
    "source_hash": "sha256:abc...",
    "extraction_model": "mistral-large-2",
    "extraction_version": "1.2.0",
    "processed_at": "2024-01-15T10:30:00Z",
    "processing_duration_ms": 4523,
    "corrections": [
      {
        "field": "total",
        "original_value": 1234.56,
        "corrected_value": 1234.00,
        "corrected_by": "user-456",
        "corrected_at": "2024-01-15T11:00:00Z"
      }
    ]
  }
}
```

---

## 8. Error Categories & Handling

| Error Type | Retry? | Circuit Breaker? | Action |
|------------|--------|------------------|--------|
| Network timeout | Yes | Yes | Exponential backoff |
| LLM rate limit (429) | Yes | No | Wait per Retry-After |
| LLM server error (5xx) | Yes | Yes | Backoff + circuit |
| Invalid document | No | No | Fail with error detail |
| Validation failure | No | No | Route to review queue |
| Schema mismatch | No | No | Fail, alert ops team |

---

## 9. Performance Optimization

### 9.1 Caching Layers

1. **Idempotency Cache**: Full extraction results (TTL: 24h)
2. **PDF Parse Cache**: Parsed text content (TTL: 1h)
3. **LLM Response Cache**: Identical prompts (TTL: 1h)

### 9.2 Batching Strategy

```python
class BatchProcessor:
    """Optimizes throughput via intelligent batching."""
    
    batch_size: int = 10           # Documents per batch
    batch_timeout_ms: int = 100    # Max wait for batch fill
    
    async def process_batch(self, documents: List[DocumentInput]):
        """Process documents in parallel within batch."""
        async with self.semaphore:
            tasks = [self.process_single(doc) for doc in documents]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results
```

---

## 10. Deployment Considerations

### 10.1 Horizontal Scaling

- Stateless extraction workers behind load balancer
- Shared state in Redis cluster
- Document storage in S3-compatible object store

### 10.2 Resource Requirements (per worker)

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 4 cores |
| Memory | 4 GB | 8 GB |
| Network | 100 Mbps | 1 Gbps |

### 10.3 High Availability

- Multiple worker replicas (min 3)
- Health checks with automatic restart
- Graceful shutdown with work drain
