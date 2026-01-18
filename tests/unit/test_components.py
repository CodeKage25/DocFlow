"""
Unit Tests for DocFlow

Tests for individual components in isolation.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class TestIdempotencyCache:
    """Unit tests for IdempotencyCache."""
    
    def test_fingerprint_consistency(self):
        """Same input should produce same fingerprint."""
        from extraction_module import IdempotencyCache, DocumentInput, ProcessingConfig
        
        cache = IdempotencyCache()
        doc = DocumentInput(
            document_id="test",
            content=b"test content",
            content_hash="hash123",
            document_type="invoice"
        )
        
        fp1 = cache.compute_fingerprint(doc)
        fp2 = cache.compute_fingerprint(doc)
        assert fp1 == fp2
    
    def test_cache_hit(self):
        """Cache should return stored result."""
        from extraction_module import IdempotencyCache, ExtractionResult, ProcessingStatus
        
        cache = IdempotencyCache()
        result = ExtractionResult(
            document_id="test",
            status=ProcessingStatus.COMPLETED,
            extracted_fields={},
            confidence_score=0.95
        )
        
        cache.set("fingerprint123", result)
        cached = cache.get("fingerprint123")
        
        assert cached is not None
        assert cached.document_id == "test"
    
    def test_cache_miss(self):
        """Cache should return None for unknown fingerprint."""
        from extraction_module import IdempotencyCache
        
        cache = IdempotencyCache()
        result = cache.get("nonexistent")
        assert result is None


class TestFieldValidator:
    """Unit tests for FieldValidator."""
    
    def test_required_field_present(self):
        """Should pass when required field exists."""
        from extraction_module import FieldValidator, ExtractedField
        
        validator = FieldValidator()
        fields = {
            "invoice_number": ExtractedField("invoice_number", "INV-001", 0.95)
        }
        
        errors = validator.validate_required(fields, ["invoice_number"])
        assert len(errors) == 0
    
    def test_required_field_missing(self):
        """Should fail when required field missing."""
        from extraction_module import FieldValidator
        
        validator = FieldValidator()
        errors = validator.validate_required({}, ["invoice_number"])
        assert len(errors) == 1


class TestCircuitBreaker:
    """Unit tests for CircuitBreaker."""
    
    def test_initial_state_closed(self):
        """Circuit breaker should start closed."""
        from extraction_module import CircuitBreaker, CircuitState
        
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        assert cb.state == CircuitState.CLOSED
    
    def test_opens_after_failures(self):
        """Should open after exceeding failure threshold."""
        from extraction_module import CircuitBreaker, CircuitState
        
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
    
    def test_success_resets_count(self):
        """Success should reset failure count."""
        from extraction_module import CircuitBreaker, CircuitState
        
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
        
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        
        assert cb._failure_count == 0


class TestPriorityCalculator:
    """Unit tests for PriorityCalculator."""
    
    def test_sla_urgency(self):
        """Items closer to SLA should have higher priority."""
        from review_queue import PriorityCalculator, ReviewItem, ExtractedFieldData
        
        calc = PriorityCalculator()
        
        # Create two items with different SLA deadlines
        item1 = ReviewItem(
            item_id="1",
            document_id="doc1",
            workflow_id="wf1",
            extraction_result={"field": ExtractedFieldData("val", 0.9)},
            document_preview_url="/preview/1",
            sla_deadline=datetime.utcnow() + timedelta(hours=1),  # Urgent
            document_type="invoice"
        )
        
        item2 = ReviewItem(
            item_id="2",
            document_id="doc2",
            workflow_id="wf1",
            extraction_result={"field": ExtractedFieldData("val", 0.9)},
            document_preview_url="/preview/2",
            sla_deadline=datetime.utcnow() + timedelta(hours=8),  # Not urgent
            document_type="invoice"
        )
        
        priority1, _ = calc.calculate(item1)
        priority2, _ = calc.calculate(item2)
        
        # Lower priority number = higher priority
        assert priority1 < priority2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
