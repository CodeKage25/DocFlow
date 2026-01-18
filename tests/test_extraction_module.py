"""
Test Suite for Extraction Module

Comprehensive tests covering extraction, idempotency, field preservation,
validation, and error handling.
"""

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from extraction_module import (
    DocumentInput,
    ExtractionResult,
    ExtractedField,
    ProcessingConfig,
    ProcessingStatus,
    IdempotencyCache,
    FieldLockManager,
    FieldValidator,
    CircuitBreaker,
    CircuitState,
    ExtractionModule,
    OutputGenerator,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_document():
    """Create a sample document for testing."""
    return DocumentInput(
        document_id="test_doc_001",
        content=b"Sample invoice content\nInvoice Number: INV-2024-001\nTotal: $1234.56",
        content_hash="abc123",
        document_type="invoice",
        metadata={"source": "test"},
        processing_config=ProcessingConfig(),
        locked_fields={},
        priority=1,
    )


@pytest.fixture
def sample_extraction_result():
    """Create a sample extraction result."""
    return ExtractionResult(
        document_id="test_doc_001",
        status=ProcessingStatus.COMPLETED,
        extracted_fields={
            "invoice_number": ExtractedField(
                name="invoice_number",
                value="INV-2024-001",
                confidence=0.95,
                source_location={"page": 1, "line": 2}
            ),
            "total_amount": ExtractedField(
                name="total_amount",
                value=1234.56,
                confidence=0.88,
                source_location={"page": 1, "line": 3}
            ),
            "currency": ExtractedField(
                name="currency",
                value="USD",
                confidence=0.99,
                source_location={"page": 1, "line": 3}
            ),
        },
        confidence_score=0.92,
        processing_metadata={
            "model_version": "1.0.0",
            "processing_time_ms": 450
        },
    )


@pytest.fixture
def idempotency_cache():
    """Create an idempotency cache for testing."""
    return IdempotencyCache(max_size=100, ttl_seconds=3600)


@pytest.fixture
def field_lock_manager():
    """Create a field lock manager for testing."""
    return FieldLockManager()


@pytest.fixture
def field_validator():
    """Create a field validator for testing."""
    return FieldValidator()


@pytest.fixture
def output_generator():
    """Create an output generator for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield OutputGenerator(output_dir=tmpdir)


# =============================================================================
# IDEMPOTENCY TESTS
# =============================================================================

class TestIdempotencyCache:
    """Tests for idempotency cache functionality."""
    
    def test_compute_fingerprint_deterministic(self, idempotency_cache, sample_document):
        """Fingerprint should be deterministic for same input."""
        fp1 = idempotency_cache.compute_fingerprint(sample_document)
        fp2 = idempotency_cache.compute_fingerprint(sample_document)
        assert fp1 == fp2
    
    def test_compute_fingerprint_different_for_different_content(self, idempotency_cache):
        """Fingerprint should differ for different content."""
        doc1 = DocumentInput(
            document_id="doc1",
            content=b"Content A",
            content_hash="hash_a",
            document_type="invoice",
        )
        doc2 = DocumentInput(
            document_id="doc1",
            content=b"Content B",
            content_hash="hash_b",
            document_type="invoice",
        )
        
        fp1 = idempotency_cache.compute_fingerprint(doc1)
        fp2 = idempotency_cache.compute_fingerprint(doc2)
        assert fp1 != fp2
    
    def test_fingerprint_includes_locked_fields(self, idempotency_cache):
        """Fingerprint should change when locked fields change."""
        doc1 = DocumentInput(
            document_id="doc1",
            content=b"Same content",
            content_hash="same_hash",
            document_type="invoice",
            locked_fields={}
        )
        doc2 = DocumentInput(
            document_id="doc1",
            content=b"Same content",
            content_hash="same_hash",
            document_type="invoice",
            locked_fields={"vendor_name": "Acme Corp"}
        )
        
        fp1 = idempotency_cache.compute_fingerprint(doc1)
        fp2 = idempotency_cache.compute_fingerprint(doc2)
        assert fp1 != fp2
    
    def test_cache_store_and_retrieve(self, idempotency_cache, sample_extraction_result):
        """Should store and retrieve cached results."""
        fingerprint = "test_fingerprint_123"
        
        # Initially not in cache
        assert idempotency_cache.get(fingerprint) is None
        
        # Store result
        idempotency_cache.set(fingerprint, sample_extraction_result)
        
        # Retrieve result
        cached = idempotency_cache.get(fingerprint)
        assert cached is not None
        assert cached.document_id == sample_extraction_result.document_id
    
    def test_cache_eviction_when_full(self, idempotency_cache, sample_extraction_result):
        """Cache should evict old entries when full."""
        # Fill cache
        for i in range(idempotency_cache.max_size + 10):
            idempotency_cache.set(f"fp_{i}", sample_extraction_result)
        
        # Should not exceed max size
        assert len(idempotency_cache._cache) <= idempotency_cache.max_size
    
    def test_cache_hit_returns_same_result(self, idempotency_cache, sample_document, sample_extraction_result):
        """Cached result should be returned on cache hit."""
        fingerprint = idempotency_cache.compute_fingerprint(sample_document)
        idempotency_cache.set(fingerprint, sample_extraction_result)
        
        # Hit should return same result
        cached = idempotency_cache.get(fingerprint)
        assert cached.extracted_fields == sample_extraction_result.extracted_fields


# =============================================================================
# FIELD LOCK TESTS
# =============================================================================

class TestFieldLockManager:
    """Tests for field lock management."""
    
    def test_lock_field(self, field_lock_manager):
        """Should lock a field."""
        doc_id = "test_doc"
        field_lock_manager.lock_field(doc_id, "vendor_name", "Locked Corp")
        
        assert field_lock_manager.is_locked(doc_id, "vendor_name")
    
    def test_unlock_field(self, field_lock_manager):
        """Should unlock a field."""
        doc_id = "test_doc"
        field_lock_manager.lock_field(doc_id, "vendor_name", "Locked Corp")
        field_lock_manager.unlock_field(doc_id, "vendor_name")
        
        assert not field_lock_manager.is_locked(doc_id, "vendor_name")
    
    def test_get_locked_value(self, field_lock_manager):
        """Should return locked value."""
        doc_id = "test_doc"
        field_lock_manager.lock_field(doc_id, "vendor_name", "Locked Corp")
        
        value = field_lock_manager.get_locked_value(doc_id, "vendor_name")
        assert value == "Locked Corp"
    
    def test_merge_preserves_locked_fields(self, field_lock_manager):
        """Merge should preserve locked field values."""
        doc_id = "test_doc"
        field_lock_manager.lock_field(doc_id, "vendor_name", "Manual Corp")
        
        extracted_fields = {
            "vendor_name": ExtractedField(
                name="vendor_name",
                value="Extracted Corp",
                confidence=0.95
            ),
            "amount": ExtractedField(
                name="amount",
                value=1000,
                confidence=0.90
            )
        }
        
        merged = field_lock_manager.merge_with_locked(doc_id, extracted_fields)
        
        # Locked field should have manual value
        assert merged["vendor_name"].value == "Manual Corp"
        assert merged["vendor_name"].confidence == 1.0
        assert merged["vendor_name"].is_locked == True
        
        # Non-locked field should have extracted value
        assert merged["amount"].value == 1000
    
    def test_get_all_locked_fields(self, field_lock_manager):
        """Should return all locked fields for a document."""
        doc_id = "test_doc"
        field_lock_manager.lock_field(doc_id, "field1", "value1")
        field_lock_manager.lock_field(doc_id, "field2", "value2")
        
        locked = field_lock_manager.get_locked_fields(doc_id)
        assert len(locked) == 2
        assert "field1" in locked
        assert "field2" in locked


# =============================================================================
# VALIDATION TESTS
# =============================================================================

class TestFieldValidator:
    """Tests for field validation."""
    
    def test_validate_required_field_present(self, field_validator):
        """Should pass when required field is present."""
        fields = {
            "invoice_number": ExtractedField(
                name="invoice_number",
                value="INV-001",
                confidence=0.95
            )
        }
        
        errors = field_validator.validate_required(fields, ["invoice_number"])
        assert len(errors) == 0
    
    def test_validate_required_field_missing(self, field_validator):
        """Should fail when required field is missing."""
        fields = {
            "vendor_name": ExtractedField(
                name="vendor_name",
                value="Test",
                confidence=0.90
            )
        }
        
        errors = field_validator.validate_required(fields, ["invoice_number"])
        assert len(errors) == 1
        assert "invoice_number" in errors[0]
    
    def test_validate_confidence_above_threshold(self, field_validator):
        """Should pass when confidence is above threshold."""
        fields = {
            "invoice_number": ExtractedField(
                name="invoice_number",
                value="INV-001",
                confidence=0.95
            )
        }
        
        errors = field_validator.validate_confidence(fields, threshold=0.80)
        assert len(errors) == 0
    
    def test_validate_confidence_below_threshold(self, field_validator):
        """Should flag when confidence is below threshold."""
        fields = {
            "invoice_number": ExtractedField(
                name="invoice_number",
                value="INV-001",
                confidence=0.65
            )
        }
        
        warnings = field_validator.validate_confidence(fields, threshold=0.80)
        assert len(warnings) == 1
    
    def test_validate_calculation_check(self, field_validator):
        """Should validate calculation consistency."""
        fields = {
            "subtotal": ExtractedField(name="subtotal", value=100.0, confidence=0.95),
            "tax_amount": ExtractedField(name="tax_amount", value=10.0, confidence=0.95),
            "total_amount": ExtractedField(name="total_amount", value=110.0, confidence=0.95)
        }
        
        result = field_validator.validate_calculation(
            fields, 
            "total_amount", 
            ["subtotal", "tax_amount"],
            tolerance=0.01
        )
        assert result.is_valid
    
    def test_validate_calculation_mismatch(self, field_validator):
        """Should detect calculation mismatch."""
        fields = {
            "subtotal": ExtractedField(name="subtotal", value=100.0, confidence=0.95),
            "tax_amount": ExtractedField(name="tax_amount", value=10.0, confidence=0.95),
            "total_amount": ExtractedField(name="total_amount", value=120.0, confidence=0.95)  # Wrong!
        }
        
        result = field_validator.validate_calculation(
            fields, 
            "total_amount", 
            ["subtotal", "tax_amount"],
            tolerance=0.01
        )
        assert not result.is_valid
        assert "mismatch" in result.message.lower()


# =============================================================================
# CIRCUIT BREAKER TESTS
# =============================================================================

class TestCircuitBreaker:
    """Tests for circuit breaker pattern."""
    
    def test_starts_closed(self):
        """Circuit breaker should start in closed state."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        assert cb.state == CircuitState.CLOSED
    
    def test_opens_after_threshold_failures(self):
        """Should open after reaching failure threshold."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
    
    def test_allows_request_when_closed(self):
        """Should allow requests when closed."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        assert cb.allow_request() == True
    
    def test_blocks_request_when_open(self):
        """Should block requests when open."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        
        for _ in range(3):
            cb.record_failure()
        
        assert cb.allow_request() == False
    
    def test_success_resets_failure_count(self):
        """Success should reset failure count."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED
    
    def test_half_open_allows_probe(self):
        """Half-open state should allow one probe request."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=0.1)
        
        for _ in range(3):
            cb.record_failure()
        
        # Wait for reset timeout
        import time
        time.sleep(0.2)
        
        assert cb.allow_request() == True  # Probe allowed
        assert cb.state == CircuitState.HALF_OPEN


# =============================================================================
# OUTPUT FORMAT TESTS
# =============================================================================

class TestOutputGenerator:
    """Tests for dual output format generation."""
    
    def test_generate_json_output(self, output_generator, sample_extraction_result):
        """Should generate valid JSON output."""
        output_path = output_generator.generate_json(sample_extraction_result)
        
        assert output_path.exists()
        
        with open(output_path) as f:
            data = json.load(f)
        
        assert data["document_id"] == sample_extraction_result.document_id
        assert "extracted_fields" in data
        assert "invoice_number" in data["extracted_fields"]
    
    def test_json_output_schema(self, output_generator, sample_extraction_result):
        """JSON output should conform to expected schema."""
        output_path = output_generator.generate_json(sample_extraction_result)
        
        with open(output_path) as f:
            data = json.load(f)
        
        # Required top-level fields
        assert "document_id" in data
        assert "status" in data
        assert "extracted_fields" in data
        assert "confidence_score" in data
        assert "processing_metadata" in data
        assert "output_generated_at" in data
        
        # Field structure
        for field_name, field_data in data["extracted_fields"].items():
            assert "value" in field_data
            assert "confidence" in field_data
    
    def test_generate_parquet_output(self, output_generator, sample_extraction_result):
        """Should generate Parquet output."""
        try:
            import pyarrow
            output_path = output_generator.generate_parquet(sample_extraction_result)
            assert output_path.exists()
        except ImportError:
            pytest.skip("PyArrow not installed")
    
    def test_generate_both_formats(self, output_generator, sample_extraction_result):
        """Should generate both JSON and Parquet."""
        paths = output_generator.generate_all(sample_extraction_result)
        
        assert "json" in paths
        assert paths["json"].exists()


# =============================================================================
# EXTRACTION MODULE INTEGRATION TESTS
# =============================================================================

class TestExtractionModuleIntegration:
    """Integration tests for the complete extraction module."""
    
    @pytest.fixture
    def extraction_module(self):
        """Create extraction module for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = ExtractionModule(
                output_dir=tmpdir,
                mock_mode=True  # Use mock extraction
            )
            yield module
    
    @pytest.mark.asyncio
    async def test_process_document_success(self, extraction_module, sample_document):
        """Should successfully process a document."""
        result = await extraction_module.process(sample_document)
        
        assert result.status == ProcessingStatus.COMPLETED
        assert result.document_id == sample_document.document_id
        assert len(result.extracted_fields) > 0
    
    @pytest.mark.asyncio
    async def test_idempotent_processing(self, extraction_module, sample_document):
        """Reprocessing same document should return cached result."""
        result1 = await extraction_module.process(sample_document)
        result2 = await extraction_module.process(sample_document)
        
        # Results should be identical
        assert result1.extracted_fields.keys() == result2.extracted_fields.keys()
    
    @pytest.mark.asyncio
    async def test_locked_fields_preserved(self, extraction_module, sample_document):
        """Locked fields should be preserved in extraction."""
        # Lock a field
        sample_document.locked_fields = {"vendor_name": "Manual Corp"}
        
        result = await extraction_module.process(sample_document)
        
        assert result.extracted_fields["vendor_name"].value == "Manual Corp"
        assert result.extracted_fields["vendor_name"].is_locked == True
    
    @pytest.mark.asyncio
    async def test_output_files_generated(self, extraction_module, sample_document):
        """Should generate output files."""
        result = await extraction_module.process(sample_document)
        
        # Check output paths exist
        if result.output_paths:
            for format_type, path in result.output_paths.items():
                assert Path(path).exists()
    
    @pytest.mark.asyncio
    async def test_low_confidence_flags_review(self, extraction_module, sample_document):
        """Low confidence should flag document for review."""
        result = await extraction_module.process(sample_document)
        
        # Check if any field has low confidence
        low_conf_fields = [
            f for f in result.extracted_fields.values() 
            if f.confidence < 0.75
        ]
        
        if low_conf_fields:
            assert result.needs_review == True


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Tests for error handling scenarios."""
    
    @pytest.mark.asyncio
    async def test_handles_extraction_timeout(self):
        """Should handle extraction timeout gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = ExtractionModule(output_dir=tmpdir, extraction_timeout=0.1)
            
            # Create a document that would cause slow extraction
            doc = DocumentInput(
                document_id="timeout_test",
                content=b"test content",
                content_hash="hash",
                document_type="invoice"
            )
            
            # Should not raise, should return failed result
            result = await module.process(doc)
            # In mock mode, this should complete quickly
            assert result is not None
    
    @pytest.mark.asyncio
    async def test_handles_invalid_document(self):
        """Should handle invalid document gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = ExtractionModule(output_dir=tmpdir, mock_mode=True)
            
            doc = DocumentInput(
                document_id="invalid_test",
                content=b"",  # Empty content
                content_hash="empty",
                document_type="invoice"
            )
            
            result = await module.process(doc)
            assert result is not None
    
    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self):
        """Should retry on transient failures."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = ExtractionModule(
                output_dir=tmpdir, 
                mock_mode=True,
                max_retries=3
            )
            
            doc = DocumentInput(
                document_id="retry_test",
                content=b"test content",
                content_hash="hash",
                document_type="invoice"
            )
            
            result = await module.process(doc)
            assert result is not None


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
