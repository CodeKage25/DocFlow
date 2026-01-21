"""
Document Extraction Module

Production-grade extraction module for processing invoices and other documents
with LLM integration, idempotency, field preservation, and dual output formats.

Author: DocFlow Team
Version: 1.0.0
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, Field, validator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class DocumentType(str, Enum):
    """Supported document types."""
    INVOICE = "invoice"
    CONTRACT = "contract"
    RECEIPT = "receipt"
    PURCHASE_ORDER = "purchase_order"


class ProcessingStatus(str, Enum):
    """Document processing status."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEW_PENDING = "review_pending"


class ProcessingPriority(str, Enum):
    """Processing priority levels."""
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# Version for idempotency fingerprinting
EXTRACTION_MODEL_VERSION = "1.0.0"


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class SourceLocation:
    """Location of extracted data in the source document."""
    page: int
    bbox: Optional[Tuple[float, float, float, float]] = None  # x1, y1, x2, y2
    text_snippet: Optional[str] = None


@dataclass
class ExtractedField:
    """Individual field extraction result."""
    field_name: str
    value: Any
    raw_value: str
    confidence: float
    source_location: Optional[SourceLocation] = None
    is_locked: bool = False
    extraction_method: str = "llm"


@dataclass
class ValidationResult:
    """Result of a validation check."""
    rule_name: str
    passed: bool
    severity: str  # error, warning, info
    message: str
    affected_fields: List[str] = field(default_factory=list)


@dataclass
class ErrorDetails:
    """Error information for failed processing."""
    error_type: str
    message: str
    stack_trace: Optional[str] = None
    retry_count: int = 0
    recoverable: bool = True


@dataclass
class ProcessingMetadata:
    """Metadata about the processing run."""
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    model_version: str = EXTRACTION_MODEL_VERSION
    config_version: str = "1.0.0"
    source_hash: str = ""


@dataclass
class OutputPaths:
    """Paths to generated output files."""
    parquet_path: Optional[str] = None
    json_path: Optional[str] = None


@dataclass
class FieldLock:
    """Represents a locked field that must be preserved."""
    field_name: str
    locked_value: Any
    locked_by: str
    locked_at: datetime
    original_value: Any
    reason: Optional[str] = None


class ProcessingConfig(BaseModel):
    """Configuration for document processing."""
    confidence_threshold: float = Field(0.75, ge=0, le=1)
    auto_approve_threshold: float = Field(0.95, ge=0, le=1)
    enable_ocr: bool = True
    enable_llm: bool = True
    output_parquet: bool = True
    output_json: bool = True
    max_retries: int = Field(3, ge=0, le=10)


@dataclass
class DocumentInput:
    """Input contract for document processing."""
    document_id: str
    content: bytes
    content_hash: str
    document_type: DocumentType
    metadata: Dict[str, Any] = field(default_factory=dict)
    processing_config: ProcessingConfig = field(default_factory=ProcessingConfig)
    locked_fields: Set[str] = field(default_factory=set)
    priority: ProcessingPriority = ProcessingPriority.NORMAL
    sla_deadline: Optional[datetime] = None


@dataclass
class ExtractionResult:
    """Output contract for extraction results."""
    document_id: str
    status: ProcessingStatus
    extracted_fields: Dict[str, ExtractedField]
    confidence_score: float
    validation_results: List[ValidationResult]
    processing_metadata: ProcessingMetadata
    output_paths: OutputPaths
    error: Optional[ErrorDetails] = None
    lineage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEvent:
    """Immutable audit record for compliance."""
    event_id: str
    timestamp: datetime
    document_id: str
    event_type: str
    actor: str
    details: Dict[str, Any]
    previous_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None


# =============================================================================
# RETRY AND CIRCUIT BREAKER
# =============================================================================

@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.1


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate delay with exponential backoff and jitter."""
    delay = min(
        config.base_delay_seconds * (config.exponential_base ** attempt),
        config.max_delay_seconds
    )
    jitter = delay * config.jitter_factor * (2 * random.random() - 1)
    return max(0, delay + jitter)


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """Protects external services from cascading failures."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try recovery."""
        if self.last_failure_time is None:
            return True
        return (time.time() - self.last_failure_time) >= self.recovery_timeout
    
    async def _on_success(self):
        """Handle successful call."""
        async with self._lock:
            self.failure_count = 0
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self.state = CircuitState.CLOSED
                    self.success_count = 0
                    logger.info("Circuit breaker closed after successful recovery")
    
    async def _on_failure(self):
        """Handle failed call."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            self.success_count = 0
            
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                logger.warning("Circuit breaker opened (half-open failure)")
            elif self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(f"Circuit breaker opened after {self.failure_count} failures")
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker entering half-open state")
                else:
                    raise CircuitBreakerError("Circuit breaker is open")
        
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            raise


# =============================================================================
# CACHING
# =============================================================================

class IdempotencyCache:
    """Cache for idempotent processing results."""
    
    def __init__(self, max_entries: int = 100000, default_ttl: int = 86400):
        self._cache: Dict[str, Tuple[ExtractionResult, float]] = {}
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
    
    async def get(self, fingerprint: str) -> Optional[ExtractionResult]:
        """Retrieve cached result if exists and not expired."""
        async with self._lock:
            if fingerprint in self._cache:
                result, expires_at = self._cache[fingerprint]
                if time.time() < expires_at:
                    logger.info(f"Cache hit for fingerprint: {fingerprint[:16]}...")
                    return result
                else:
                    del self._cache[fingerprint]
            return None
    
    async def set(self, fingerprint: str, result: ExtractionResult, ttl: Optional[int] = None):
        """Store result with TTL for eventual cleanup."""
        async with self._lock:
            # LRU eviction if at capacity
            if len(self._cache) >= self._max_entries:
                oldest = min(self._cache.items(), key=lambda x: x[1][1])
                del self._cache[oldest[0]]
            
            expires_at = time.time() + (ttl or self._default_ttl)
            self._cache[fingerprint] = (result, expires_at)
            logger.info(f"Cached result for fingerprint: {fingerprint[:16]}...")
    
    async def invalidate(self, fingerprint: str):
        """Invalidate a specific cache entry."""
        async with self._lock:
            if fingerprint in self._cache:
                del self._cache[fingerprint]
                logger.info(f"Invalidated cache for fingerprint: {fingerprint[:16]}...")


# =============================================================================
# FIELD LOCK MANAGER
# =============================================================================

class FieldLockManager:
    """Manages field-level locks to prevent overwriting corrections."""
    
    def __init__(self):
        self._locks: Dict[str, Dict[str, FieldLock]] = {}  # doc_id -> field_name -> lock
        self._lock = asyncio.Lock()
    
    async def is_locked(self, document_id: str, field_name: str) -> bool:
        """Check if field is locked."""
        async with self._lock:
            return (
                document_id in self._locks and 
                field_name in self._locks[document_id]
            )
    
    async def get_locked_value(self, document_id: str, field_name: str) -> Optional[Any]:
        """Get the locked value to use instead of extraction."""
        async with self._lock:
            if document_id in self._locks and field_name in self._locks[document_id]:
                return self._locks[document_id][field_name].locked_value
            return None
    
    async def get_all_locks(self, document_id: str) -> Dict[str, FieldLock]:
        """Get all locked fields for a document."""
        async with self._lock:
            return dict(self._locks.get(document_id, {}))
    
    async def lock_field(
        self, 
        document_id: str, 
        field_name: str, 
        value: Any, 
        user_id: str,
        original_value: Any = None,
        reason: str = None
    ):
        """Lock a field after manual correction."""
        async with self._lock:
            if document_id not in self._locks:
                self._locks[document_id] = {}
            
            self._locks[document_id][field_name] = FieldLock(
                field_name=field_name,
                locked_value=value,
                locked_by=user_id,
                locked_at=datetime.utcnow(),
                original_value=original_value,
                reason=reason
            )
            logger.info(f"Locked field {field_name} for document {document_id}")
    
    async def unlock_field(self, document_id: str, field_name: str):
        """Unlock a field."""
        async with self._lock:
            if document_id in self._locks and field_name in self._locks[document_id]:
                del self._locks[document_id][field_name]
                logger.info(f"Unlocked field {field_name} for document {document_id}")
    
    async def merge_with_locks(
        self, 
        document_id: str, 
        extracted: Dict[str, ExtractedField]
    ) -> Dict[str, ExtractedField]:
        """Merge extracted values with locked fields (locked wins)."""
        locks = await self.get_all_locks(document_id)
        
        for field_name, lock in locks.items():
            if field_name in extracted:
                # Preserve locked value, update the field
                extracted[field_name] = ExtractedField(
                    field_name=field_name,
                    value=lock.locked_value,
                    raw_value=str(lock.locked_value),
                    confidence=1.0,  # Locked fields have full confidence
                    is_locked=True,
                    extraction_method="manual"
                )
                logger.info(f"Preserved locked value for {field_name}")
        
        return extracted


# =============================================================================
# LLM CLIENT
# =============================================================================

class LLMClient(ABC):
    """Abstract base class for LLM clients."""
    
    @abstractmethod
    async def extract(self, text: str, document_type: DocumentType) -> Dict[str, Any]:
        """Extract structured data from text."""
        pass


class MistralClient(LLMClient):
    """Mistral AI client for document extraction."""
    
    EXTRACTION_PROMPT = """You are a document extraction specialist. Extract structured data from the following {document_type} document.

DOCUMENT TEXT:
{document_text}

Extract the following fields and return them as a JSON object. For each field, provide the value and your confidence level (0.0 to 1.0).

Required fields for invoices:
- invoice_number: The unique invoice identifier
- invoice_date: Date the invoice was issued (YYYY-MM-DD format)
- due_date: Payment due date (YYYY-MM-DD format, null if not found)
- vendor_name: Name of the vendor/supplier
- vendor_address: Full vendor address (null if not found)
- vendor_tax_id: Tax ID/VAT number (null if not found)
- customer_name: Name of the customer (null if not found)
- subtotal: Amount before tax
- tax_rate: Tax rate as a percentage (null if not applicable)
- tax_amount: Total tax amount (null if not applicable)
- total_amount: Total invoice amount including tax
- currency: Currency code (USD, EUR, CHF, etc.)
- line_items: Array of items with description, quantity, unit_price, and amount

Respond ONLY with a valid JSON object in this exact format:
{{
  "fields": {{
    "invoice_number": {{"value": "...", "confidence": 0.95}},
    "invoice_date": {{"value": "YYYY-MM-DD", "confidence": 0.95}},
    "due_date": {{"value": "YYYY-MM-DD or null", "confidence": 0.85}},
    "vendor_name": {{"value": "...", "confidence": 0.90}},
    "vendor_address": {{"value": "...", "confidence": 0.80}},
    "vendor_tax_id": {{"value": "...", "confidence": 0.85}},
    "customer_name": {{"value": "...", "confidence": 0.80}},
    "subtotal": {{"value": 0.00, "confidence": 0.90}},
    "tax_rate": {{"value": 0.00, "confidence": 0.85}},
    "tax_amount": {{"value": 0.00, "confidence": 0.90}},
    "total_amount": {{"value": 0.00, "confidence": 0.95}},
    "currency": {{"value": "USD", "confidence": 0.95}},
    "line_items": {{"value": [...], "confidence": 0.85}}
  }},
  "overall_confidence": 0.90
}}

Be precise and only extract information explicitly present in the document."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not self.api_key:
            logger.warning("No Mistral API key provided. Using mock extraction.")
        self._client = None
    
    async def _get_client(self):
        """Lazily initialize the Mistral client."""
        if self._client is None and self.api_key:
            try:
                from mistralai import Mistral
                self._client = Mistral(api_key=self.api_key)
            except ImportError:
                logger.warning("mistralai package not installed. Using mock extraction.")
        return self._client
    
    async def extract(self, text: str, document_type: DocumentType) -> Dict[str, Any]:
        """Extract structured data from text using Mistral AI."""
        client = await self._get_client()
        
        if client is None:
            # Return mock data for testing
            return self._mock_extraction(document_type)
        
        prompt = self.EXTRACTION_PROMPT.format(
            document_type=document_type.value,
            document_text=text[:8000]  # Limit text length
        )
        
        try:
            response = await asyncio.to_thread(
                client.chat.complete,
                model="mistral-large-latest",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4096,
            )
            
            content = response.choices[0].message.content
            
            # Parse JSON from response
            # Find JSON object in response
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                return json.loads(json_str)
            else:
                raise ValueError("No JSON object found in response")
                
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            raise
    
    async def extract_from_image(self, image_bytes: bytes, content_type: str, document_type: DocumentType) -> Dict[str, Any]:
        """Extract structured data from image using Mistral Pixtral (vision) model.
        
        Args:
            image_bytes: Raw image bytes
            content_type: MIME type of the image (e.g., 'image/jpeg', 'image/png')
            document_type: Type of document for extraction prompts
            
        Returns:
            Extracted fields dictionary
        """
        import base64
        
        client = await self._get_client()
        
        if client is None:
            # Return mock data for testing
            return self._mock_extraction(document_type)
        
        # Encode image as base64 data URL
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        image_url = f"data:{content_type};base64,{base64_image}"
        
        prompt = self.EXTRACTION_PROMPT.format(
            document_type=document_type.value,
            document_text="[Document is an image - extract fields from the image]"
        )
        
        try:
            logger.info(f"Extracting from image using pixtral-large-latest, size={len(image_bytes)} bytes")
            
            response = await asyncio.to_thread(
                client.chat.complete,
                model="pixtral-large-latest",  # Vision-capable model
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }],
                temperature=0.1,
                max_tokens=4096,
            )
            
            content = response.choices[0].message.content
            logger.info(f"Vision extraction response received, length={len(content)}")
            
            # Parse JSON from response
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                return json.loads(json_str)
            else:
                raise ValueError("No JSON object found in vision model response")
                
        except Exception as e:
            logger.error(f"Vision extraction failed: {e}")
            raise
    
    def _mock_extraction(self, document_type: DocumentType) -> Dict[str, Any]:
        """Return mock extraction for testing without API key."""
        return {
            "fields": {
                "invoice_number": {"value": "INV-001", "confidence": 0.95},
                "invoice_date": {"value": "2024-01-15", "confidence": 0.92},
                "due_date": {"value": "2024-02-15", "confidence": 0.88},
                "vendor_name": {"value": "Acme Corp", "confidence": 0.90},
                "vendor_address": {"value": "123 Main St, New York, NY 10001", "confidence": 0.75},
                "vendor_tax_id": {"value": "12-3456789", "confidence": 0.85},
                "customer_name": {"value": "Test Customer", "confidence": 0.80},
                "subtotal": {"value": 1000.00, "confidence": 0.92},
                "tax_rate": {"value": 20.0, "confidence": 0.88},
                "tax_amount": {"value": 200.00, "confidence": 0.90},
                "total_amount": {"value": 1200.00, "confidence": 0.95},
                "currency": {"value": "USD", "confidence": 0.98},
                "line_items": {
                    "value": [
                        {"description": "Service A", "quantity": 1, "unit_price": 500.00, "amount": 500.00},
                        {"description": "Service B", "quantity": 2, "unit_price": 250.00, "amount": 500.00}
                    ],
                    "confidence": 0.85
                }
            },
            "overall_confidence": 0.89
        }


# =============================================================================
# PDF PROCESSOR
# =============================================================================

class PDFProcessor:
    """Extracts text content from PDF documents."""
    
    async def extract_text(self, content: bytes) -> str:
        """Extract text from PDF content."""
        try:
            # Try pdfplumber first
            import pdfplumber
            import io
            
            text_parts = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            
            return "\n\n".join(text_parts)
            
        except ImportError:
            logger.warning("pdfplumber not available, trying PyMuPDF")
            
        try:
            # Fallback to PyMuPDF
            import fitz
            import io
            
            text_parts = []
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            
            return "\n\n".join(text_parts)
            
        except ImportError:
            logger.error("No PDF library available. Install pdfplumber or PyMuPDF.")
            # Return empty string for testing
            return "[PDF content - install pdfplumber or PyMuPDF for extraction]"


# =============================================================================
# VALIDATORS
# =============================================================================

class FieldValidator:
    """Validates extracted fields against rules."""
    
    def validate(self, fields: Dict[str, ExtractedField]) -> List[ValidationResult]:
        """Run all validation rules on extracted fields."""
        results = []
        
        # Required fields check
        required_fields = ["invoice_number", "total_amount", "currency"]
        for field_name in required_fields:
            if field_name not in fields or fields[field_name].value is None:
                results.append(ValidationResult(
                    rule_name=f"required_{field_name}",
                    passed=False,
                    severity="error",
                    message=f"Required field '{field_name}' is missing",
                    affected_fields=[field_name]
                ))
        
        # Tax calculation check
        if all(f in fields for f in ["subtotal", "tax_rate", "tax_amount"]):
            subtotal = fields["subtotal"].value
            tax_rate = fields["tax_rate"].value
            tax_amount = fields["tax_amount"].value
            
            if subtotal and tax_rate and tax_amount:
                expected_tax = subtotal * (tax_rate / 100)
                if abs(expected_tax - tax_amount) > 0.01:
                    results.append(ValidationResult(
                        rule_name="tax_calculation_check",
                        passed=False,
                        severity="warning",
                        message=f"Tax amount ({tax_amount}) doesn't match calculated ({expected_tax:.2f})",
                        affected_fields=["tax_amount", "subtotal", "tax_rate"]
                    ))
        
        # Total calculation check
        if all(f in fields for f in ["subtotal", "total_amount"]):
            subtotal = fields["subtotal"].value or 0
            tax_amount = fields.get("tax_amount", ExtractedField("tax_amount", 0, "0", 1.0)).value or 0
            total = fields["total_amount"].value
            
            if total:
                expected_total = subtotal + tax_amount
                if abs(expected_total - total) > 0.01:
                    results.append(ValidationResult(
                        rule_name="total_calculation_check",
                        passed=False,
                        severity="warning",
                        message=f"Total ({total}) doesn't match subtotal + tax ({expected_total:.2f})",
                        affected_fields=["total_amount", "subtotal", "tax_amount"]
                    ))
        
        # Confidence threshold check
        low_confidence_fields = [
            f.field_name for f in fields.values() 
            if f.confidence < 0.75 and not f.is_locked
        ]
        if low_confidence_fields:
            results.append(ValidationResult(
                rule_name="low_confidence_check",
                passed=False,
                severity="warning",
                message=f"Fields with low confidence: {', '.join(low_confidence_fields)}",
                affected_fields=low_confidence_fields
            ))
        
        return results


# =============================================================================
# OUTPUT GENERATORS
# =============================================================================

class OutputGenerator:
    """Generates Parquet and JSON output files."""
    
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def generate(
        self, 
        document_id: str, 
        fields: Dict[str, ExtractedField],
        metadata: ProcessingMetadata,
        lineage: Dict[str, Any]
    ) -> OutputPaths:
        """Generate both Parquet and JSON outputs."""
        paths = OutputPaths()
        
        # Generate JSON
        json_path = await self._generate_json(document_id, fields, metadata, lineage)
        paths.json_path = str(json_path)
        
        # Generate Parquet
        parquet_path = await self._generate_parquet(document_id, fields, metadata)
        paths.parquet_path = str(parquet_path)
        
        return paths
    
    async def _generate_json(
        self, 
        document_id: str, 
        fields: Dict[str, ExtractedField],
        metadata: ProcessingMetadata,
        lineage: Dict[str, Any]
    ) -> Path:
        """Generate JSON output."""
        output = {
            "schema_version": "1.0.0",
            "metadata": {
                "document_id": document_id,
                "processed_at": metadata.completed_at.isoformat() if metadata.completed_at else None,
                "processing_time_ms": metadata.duration_ms,
                "model_version": metadata.model_version,
            },
            "extraction": {
                field.field_name: {
                    "value": field.value,
                    "confidence": field.confidence,
                    "is_locked": field.is_locked,
                    "extraction_method": field.extraction_method
                }
                for field in fields.values()
            },
            "lineage": lineage
        }
        
        json_path = self.output_dir / f"{document_id}.json"
        with open(json_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        
        logger.info(f"Generated JSON output: {json_path}")
        return json_path
    
    async def _generate_parquet(
        self, 
        document_id: str, 
        fields: Dict[str, ExtractedField],
        metadata: ProcessingMetadata
    ) -> Path:
        """Generate Parquet output."""
        # Flatten fields for Parquet
        data = {
            "document_id": [document_id],
            "processed_at": [metadata.completed_at],
            "processing_time_ms": [metadata.duration_ms],
            "model_version": [metadata.model_version],
        }
        
        # Add field values
        for field_name in ["invoice_number", "invoice_date", "due_date", "vendor_name",
                          "vendor_tax_id", "customer_name", "currency"]:
            if field_name in fields:
                data[field_name] = [fields[field_name].value]
            else:
                data[field_name] = [None]
        
        # Add numeric fields
        for field_name in ["subtotal", "tax_rate", "tax_amount", "total_amount"]:
            if field_name in fields:
                data[field_name] = [float(fields[field_name].value) if fields[field_name].value else None]
            else:
                data[field_name] = [None]
        
        # Add metadata fields
        data["overall_confidence"] = [
            sum(f.confidence for f in fields.values()) / len(fields) if fields else 0
        ]
        data["has_corrections"] = [any(f.is_locked for f in fields.values())]
        data["line_item_count"] = [
            len(fields.get("line_items", ExtractedField("line_items", [], "", 0)).value or [])
        ]
        
        table = pa.table(data)
        parquet_path = self.output_dir / f"{document_id}.parquet"
        pq.write_table(table, parquet_path, compression="snappy")
        
        logger.info(f"Generated Parquet output: {parquet_path}")
        return parquet_path


# =============================================================================
# AUDIT LOGGER
# =============================================================================

class AuditLogger:
    """Logs audit events for compliance."""
    
    def __init__(self, log_dir: str = "./audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._events: List[AuditEvent] = []
    
    async def log(self, event: AuditEvent):
        """Log an audit event."""
        self._events.append(event)
        
        # Also write to file
        log_file = self.log_dir / f"{event.document_id}_audit.jsonl"
        with open(log_file, "a") as f:
            event_dict = {
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat(),
                "document_id": event.document_id,
                "event_type": event.event_type,
                "actor": event.actor,
                "details": event.details,
            }
            f.write(json.dumps(event_dict) + "\n")
    
    async def get_events(self, document_id: str) -> List[AuditEvent]:
        """Get all audit events for a document."""
        return [e for e in self._events if e.document_id == document_id]


# =============================================================================
# EXTRACTION MODULE
# =============================================================================

class ExtractionModule:
    """
    Main extraction module for processing documents.
    
    Features:
    - LLM-powered extraction with Mistral AI
    - Idempotent processing with caching
    - Field preservation for manual corrections
    - Dual output format (Parquet + JSON)
    - Retry with exponential backoff
    - Circuit breaker for external services
    """
    
    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        output_dir: str = "./output",
        cache_ttl: int = 86400,
        max_concurrent: int = 100,
    ):
        self.llm_client = llm_client or MistralClient()
        self.pdf_processor = PDFProcessor()
        self.validator = FieldValidator()
        self.output_generator = OutputGenerator(output_dir)
        self.audit_logger = AuditLogger()
        
        self.cache = IdempotencyCache(default_ttl=cache_ttl)
        self.field_lock_manager = FieldLockManager()
        self.circuit_breaker = CircuitBreaker()
        self.retry_config = RetryConfig()
        
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._status_store: Dict[str, ProcessingStatus] = {}
    
    def compute_fingerprint(self, document: DocumentInput) -> str:
        """Compute idempotent document fingerprint."""
        components = [
            document.content_hash,
            document.document_type.value,
            json.dumps(document.processing_config.dict(), sort_keys=True),
            str(sorted(document.locked_fields)),
            EXTRACTION_MODEL_VERSION,
        ]
        return hashlib.sha256("|".join(components).encode()).hexdigest()
    
    async def get_status(self, document_id: str) -> ProcessingStatus:
        """Get current processing status for a document."""
        return self._status_store.get(document_id, ProcessingStatus.QUEUED)
    
    async def _set_status(self, document_id: str, status: ProcessingStatus):
        """Update processing status."""
        self._status_store[document_id] = status
        logger.info(f"Document {document_id} status: {status.value}")
    
    async def process(self, document: DocumentInput) -> ExtractionResult:
        """
        Process a document and extract structured data.
        
        This is the main entry point for document processing.
        """
        async with self._semaphore:
            return await self._process_with_retry(document)
    
    async def _process_with_retry(self, document: DocumentInput) -> ExtractionResult:
        """Process with retry logic."""
        fingerprint = self.compute_fingerprint(document)
        
        # Check cache first
        cached_result = await self.cache.get(fingerprint)
        if cached_result:
            logger.info(f"Returning cached result for {document.document_id}")
            return cached_result
        
        # Set initial status
        await self._set_status(document.document_id, ProcessingStatus.PROCESSING)
        
        # Log audit event
        await self.audit_logger.log(AuditEvent(
            event_id=f"evt_{document.document_id}_{int(time.time()*1000)}",
            timestamp=datetime.utcnow(),
            document_id=document.document_id,
            event_type="extraction_started",
            actor="system",
            details={"fingerprint": fingerprint}
        ))
        
        last_error = None
        for attempt in range(self.retry_config.max_retries + 1):
            try:
                result = await self._process_document(document, fingerprint)
                
                # Cache successful result
                await self.cache.set(fingerprint, result)
                
                return result
                
            except CircuitBreakerError as e:
                logger.error(f"Circuit breaker open for {document.document_id}")
                last_error = e
                break
                
            except Exception as e:
                last_error = e
                if attempt < self.retry_config.max_retries:
                    delay = calculate_delay(attempt, self.retry_config)
                    logger.warning(
                        f"Attempt {attempt + 1} failed for {document.document_id}, "
                        f"retrying in {delay:.2f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"All retries exhausted for {document.document_id}: {e}")
        
        # All retries failed
        await self._set_status(document.document_id, ProcessingStatus.FAILED)
        
        return ExtractionResult(
            document_id=document.document_id,
            status=ProcessingStatus.FAILED,
            extracted_fields={},
            confidence_score=0.0,
            validation_results=[],
            processing_metadata=ProcessingMetadata(
                started_at=datetime.utcnow(),
                source_hash=document.content_hash
            ),
            output_paths=OutputPaths(),
            error=ErrorDetails(
                error_type=type(last_error).__name__,
                message=str(last_error),
                retry_count=self.retry_config.max_retries,
                recoverable=True
            )
        )
    
    async def _process_document(
        self, 
        document: DocumentInput, 
        fingerprint: str
    ) -> ExtractionResult:
        """Core document processing logic."""
        start_time = datetime.utcnow()
        
        # Check if document is an image based on metadata or content type
        IMAGE_TYPES = {"image/jpeg", "image/png", "image/tiff", "image/jpg", "image/webp"}
        content_type = document.metadata.get("content_type", "")
        filename = document.metadata.get("filename", "").lower()
        
        is_image = (
            content_type in IMAGE_TYPES or
            any(filename.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".tiff", ".webp"])
        )
        
        if is_image:
            # Image path: use vision model directly on the raw bytes
            logger.info(f"Processing image document: {document.document_id}")
            extraction_result = await self.circuit_breaker.call(
                self.llm_client.extract_from_image,
                document.content,
                content_type or "image/jpeg",
                document.document_type
            )
        else:
            # PDF/text path: extract text first, then use text model
            if document.processing_config.enable_ocr:
                text = await self.pdf_processor.extract_text(document.content)
            else:
                try:
                    text = document.content.decode('utf-8')
                except UnicodeDecodeError:
                    # If cannot decode, might be binary but OCR disabled?
                    # Raise helpful error
                    raise ValueError("OCR disabled but content is not valid UTF-8 text.")
            
            if not text.strip():
                raise ValueError("No text could be extracted from document")
            
            # LLM extraction with circuit breaker
            extraction_result = await self.circuit_breaker.call(
                self.llm_client.extract,
                text,
                document.document_type
            )
        
        # Step 3: Parse extraction result into ExtractedField objects
        fields: Dict[str, ExtractedField] = {}
        for field_name, field_data in extraction_result.get("fields", {}).items():
            fields[field_name] = ExtractedField(
                field_name=field_name,
                value=field_data.get("value"),
                raw_value=str(field_data.get("value", "")),
                confidence=field_data.get("confidence", 0.0),
            )
        
        # Step 4: Merge with locked fields (locked values always win)
        fields = await self.field_lock_manager.merge_with_locks(
            document.document_id, 
            fields
        )
        
        # Step 5: Validate extracted fields
        validation_results = self.validator.validate(fields)
        
        # Step 6: Determine final status
        overall_confidence = extraction_result.get("overall_confidence", 0.0)
        has_errors = any(v.severity == "error" and not v.passed for v in validation_results)
        needs_review = (
            overall_confidence < document.processing_config.confidence_threshold or
            has_errors
        )
        
        if needs_review:
            status = ProcessingStatus.REVIEW_PENDING
        else:
            status = ProcessingStatus.COMPLETED
        
        await self._set_status(document.document_id, status)
        
        # Step 7: Generate outputs
        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        
        processing_metadata = ProcessingMetadata(
            started_at=start_time,
            completed_at=end_time,
            duration_ms=duration_ms,
            source_hash=document.content_hash
        )
        
        # Build lineage information
        lineage = {
            "source_hash": document.content_hash,
            "extraction_model": "mistral-large-latest",
            "extraction_version": EXTRACTION_MODEL_VERSION,
            "processed_at": end_time.isoformat(),
            "processing_duration_ms": duration_ms,
            "corrections": [
                {
                    "field": f.field_name,
                    "corrected_value": f.value,
                }
                for f in fields.values() if f.is_locked
            ]
        }
        
        output_paths = await self.output_generator.generate(
            document.document_id,
            fields,
            processing_metadata,
            lineage
        )
        
        # Log completion
        await self.audit_logger.log(AuditEvent(
            event_id=f"evt_{document.document_id}_{int(time.time()*1000)}",
            timestamp=datetime.utcnow(),
            document_id=document.document_id,
            event_type="extraction_completed",
            actor="system",
            details={
                "status": status.value,
                "confidence": overall_confidence,
                "duration_ms": duration_ms
            }
        ))
        
        return ExtractionResult(
            document_id=document.document_id,
            status=status,
            extracted_fields=fields,
            confidence_score=overall_confidence,
            validation_results=validation_results,
            processing_metadata=processing_metadata,
            output_paths=output_paths,
            lineage=lineage
        )
    
    async def lock_field(
        self, 
        document_id: str, 
        field_name: str, 
        value: Any,
        user_id: str,
        reason: str = None
    ):
        """Lock a field with a manual correction."""
        # Get original value if we have it
        original_value = None
        cached_result = None
        
        for _, (result, _) in self.cache._cache.items():
            if result.document_id == document_id:
                cached_result = result
                if field_name in result.extracted_fields:
                    original_value = result.extracted_fields[field_name].value
                break
        
        await self.field_lock_manager.lock_field(
            document_id=document_id,
            field_name=field_name,
            value=value,
            user_id=user_id,
            original_value=original_value,
            reason=reason
        )
        
        # Log audit event
        await self.audit_logger.log(AuditEvent(
            event_id=f"evt_{document_id}_{int(time.time()*1000)}",
            timestamp=datetime.utcnow(),
            document_id=document_id,
            event_type="field_locked",
            actor=user_id,
            details={
                "field_name": field_name,
                "new_value": value,
                "original_value": original_value,
                "reason": reason
            }
        ))
    
    async def process_batch(
        self, 
        documents: List[DocumentInput]
    ) -> List[ExtractionResult]:
        """Process multiple documents in parallel."""
        tasks = [self.process(doc) for doc in documents]
        return await asyncio.gather(*tasks, return_exceptions=False)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def compute_content_hash(content: bytes) -> str:
    """Compute SHA-256 hash of document content."""
    return hashlib.sha256(content).hexdigest()


async def create_document_input(
    document_id: str,
    file_path: str,
    document_type: DocumentType = DocumentType.INVOICE,
    **kwargs
) -> DocumentInput:
    """Helper to create DocumentInput from a file."""
    with open(file_path, "rb") as f:
        content = f.read()
    
    return DocumentInput(
        document_id=document_id,
        content=content,
        content_hash=compute_content_hash(content),
        document_type=document_type,
        **kwargs
    )


# =============================================================================
# MAIN (for testing)
# =============================================================================

async def main():
    """Test the extraction module."""
    import sys
    
    # Initialize module
    module = ExtractionModule(output_dir="./output")
    
    # Check for test file
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return
        
        # Create input document
        doc = await create_document_input(
            document_id=f"doc_{int(time.time())}",
            file_path=file_path
        )
        
        print(f"Processing: {file_path}")
        print(f"Document ID: {doc.document_id}")
        print(f"Content hash: {doc.content_hash[:16]}...")
        print()
        
        # Process document
        result = await module.process(doc)
        
        print(f"Status: {result.status.value}")
        print(f"Confidence: {result.confidence_score:.2%}")
        print()
        print("Extracted Fields:")
        for field in result.extracted_fields.values():
            status = "🔒" if field.is_locked else "✓" if field.confidence >= 0.8 else "⚠"
            print(f"  {status} {field.field_name}: {field.value} ({field.confidence:.0%})")
        
        if result.validation_results:
            print()
            print("Validation Results:")
            for v in result.validation_results:
                icon = "✓" if v.passed else "✗" if v.severity == "error" else "⚠"
                print(f"  {icon} {v.rule_name}: {v.message}")
        
        print()
        print(f"JSON output: {result.output_paths.json_path}")
        print(f"Parquet output: {result.output_paths.parquet_path}")
    else:
        print("Usage: python extraction_module.py <pdf_file>")
        print()
        print("Running mock extraction test...")
        
        # Create mock document
        doc = DocumentInput(
            document_id="test_doc_001",
            content=b"mock pdf content",
            content_hash=compute_content_hash(b"mock pdf content"),
            document_type=DocumentType.INVOICE
        )
        
        result = await module.process(doc)
        
        print(f"Status: {result.status.value}")
        print(f"Confidence: {result.confidence_score:.2%}")
        print(f"Fields extracted: {len(result.extracted_fields)}")
        
        # Test field locking
        print()
        print("Testing field locking...")
        await module.lock_field(
            document_id="test_doc_001",
            field_name="total_amount",
            value=1500.00,
            user_id="reviewer_001",
            reason="Corrected calculation error"
        )
        
        # Reprocess (should use cached result but with locked field)
        result2 = await module.process(doc)
        total_field = result2.extracted_fields.get("total_amount")
        if total_field:
            print(f"Total after lock: {total_field.value} (locked: {total_field.is_locked})")


if __name__ == "__main__":
    asyncio.run(main())
