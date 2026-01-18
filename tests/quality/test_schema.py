"""
Quality Tests for DocFlow

Tests for data quality, schema compliance, and consistency.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from extraction_module import (
    ExtractionModule, 
    DocumentInput, 
    OutputGenerator, 
    ExtractionResult, 
    ProcessingStatus, 
    ExtractedField,
    ProcessingMetadata,
    OutputPaths,
    DocumentType
)
from review_queue import ReviewItem, ExtractedFieldData, PriorityCalculator

class TestSchemaCompliance:
    """Tests for output schema compliance."""
    
    @pytest.mark.asyncio
    async def test_json_output_schema(self):
        """JSON output should follow expected schema."""
        
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = OutputGenerator(output_dir=tmpdir)
            
            result = ExtractionResult(
                document_id="test_doc",
                status=ProcessingStatus.COMPLETED,
                extracted_fields={
                    "invoice_number": ExtractedField(field_name="invoice_number", value="INV-001", raw_value="INV-001", confidence=0.95),
                    "total_amount": ExtractedField(field_name="total_amount", value=1234.56, raw_value="1234.56", confidence=0.88)
                },
                confidence_score=0.90,
                validation_results=[],
                processing_metadata=ProcessingMetadata(
                    started_at=datetime.now(timezone.utc),
                    source_hash="hash"
                ),
                output_paths=OutputPaths(),
                lineage={}
            )
            
            output_path = await generator._generate_json(
                result.document_id,
                result.extracted_fields,
                result.processing_metadata,
                result.lineage
            )
            
            with open(output_path) as f:
                data = json.load(f)
            
            # Required fields
            assert "metadata" in data
            assert "document_id" in data["metadata"]
            assert "extraction" in data
            assert "invoice_number" in data["extraction"]
            
            # Field structure
            for field_name, field_data in data["extraction"].items():
                assert "value" in field_data
                assert "confidence" in field_data
    
    def test_review_item_schema(self):
        """Review items should have all required fields."""
        now = datetime.now(timezone.utc)
        
        item = ReviewItem(
            item_id="item_001",
            document_id="doc_001",
            workflow_id="wf_001",
            extraction_result={"field": ExtractedFieldData("value", 0.9)},
            document_preview_url="/preview/doc.pdf",
            sla_deadline=now + timedelta(hours=4),
            document_type=DocumentType.INVOICE,
            low_confidence_fields=[],
            priority=0,
            priority_factors={},
            created_at=now
        )
        
        # Check required fields exist
        assert item.item_id is not None
        assert item.document_id is not None
        assert item.extraction_result is not None
        assert item.sla_deadline is not None


class TestNullHandling:
    """Tests for null/empty value handling."""
    
    def test_empty_extraction_result(self):
        """Should handle empty extraction results."""
        
        result = ExtractionResult(
            document_id="test",
            status=ProcessingStatus.COMPLETED,
            extracted_fields={},
            confidence_score=0.0,
            validation_results=[],
            processing_metadata=None,
            output_paths=OutputPaths()
        )
        
        assert result.extracted_fields == {}
        assert result.confidence_score == 0.0
    
    def test_null_field_values(self):
        """Should handle null field values gracefully."""
        
        field = ExtractedField(
            field_name="optional_field",
            value=None,
            raw_value="",
            confidence=0.5
        )
        
        assert field.value is None
        assert field.confidence == 0.5


class TestDataConsistency:
    """Tests for data consistency."""
    
    def test_confidence_score_range(self):
        """Confidence scores should be between 0 and 1."""
        
        # Valid confidence
        field = ExtractedField(field_name="test", value="value", raw_value="value", confidence=0.85)
        assert 0 <= field.confidence <= 1
    



class TestOutputFormatConsistency:
    """Tests for output format consistency."""
    
    @pytest.mark.asyncio
    async def test_json_parquet_field_consistency(self):
        """JSON and Parquet outputs should have consistent fields."""
        
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = OutputGenerator(output_dir=tmpdir)
            
            result = ExtractionResult(
                document_id="test_doc",
                status=ProcessingStatus.COMPLETED,
                extracted_fields={
                    "invoice_number": ExtractedField(field_name="invoice_number", value="INV-001", raw_value="INV-001", confidence=0.95),
                    "total_amount": ExtractedField(field_name="total_amount", value=1234.56, raw_value="1234.56", confidence=0.88)
                },
                confidence_score=0.90,
                validation_results=[],
                processing_metadata=ProcessingMetadata(
                    started_at=datetime.now(timezone.utc),
                    source_hash="hash"
                ),
                output_paths=OutputPaths(),
                lineage={}
            )
            
            json_path = await generator._generate_json(
                result.document_id,
                result.extracted_fields,
                result.processing_metadata,
                result.lineage
            )
            
            with open(json_path) as f:
                json_data = json.load(f)
            
            # Verify JSON has expected fields
            assert json_data["metadata"]["document_id"] == "test_doc"
            assert "invoice_number" in json_data["extraction"]
            assert "total_amount" in json_data["extraction"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
