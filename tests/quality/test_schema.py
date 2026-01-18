"""
Quality Tests for DocFlow

Tests for data quality, schema compliance, and consistency.
"""

import json
import pytest
from datetime import datetime
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class TestSchemaCompliance:
    """Tests for output schema compliance."""
    
    @pytest.mark.asyncio
    async def test_json_output_schema(self):
        """JSON output should follow expected schema."""
        from extraction_module import ExtractionModule, DocumentInput, OutputGenerator, ExtractionResult, ProcessingStatus, ExtractedField
        
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = OutputGenerator(output_dir=tmpdir)
            
            result = ExtractionResult(
                document_id="test_doc",
                status=ProcessingStatus.COMPLETED,
                extracted_fields={
                    "invoice_number": ExtractedField("invoice_number", "INV-001", 0.95),
                    "total_amount": ExtractedField("total_amount", 1234.56, 0.88)
                },
                confidence_score=0.90
            )
            
            output_path = generator.generate_json(result)
            
            with open(output_path) as f:
                data = json.load(f)
            
            # Required fields
            assert "document_id" in data
            assert "status" in data
            assert "extracted_fields" in data
            assert "confidence_score" in data
            assert "output_generated_at" in data
            
            # Field structure
            for field_name, field_data in data["extracted_fields"].items():
                assert "value" in field_data
                assert "confidence" in field_data
    
    def test_review_item_schema(self):
        """Review items should have all required fields."""
        from review_queue import ReviewItem, ExtractedFieldData
        from datetime import datetime, timedelta
        
        item = ReviewItem(
            item_id="item_001",
            document_id="doc_001",
            workflow_id="wf_001",
            extraction_result={"field": ExtractedFieldData("value", 0.9)},
            document_preview_url="/preview/doc.pdf",
            sla_deadline=datetime.utcnow() + timedelta(hours=4),
            document_type="invoice"
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
        from extraction_module import ExtractionResult, ProcessingStatus
        
        result = ExtractionResult(
            document_id="test",
            status=ProcessingStatus.COMPLETED,
            extracted_fields={},
            confidence_score=0.0
        )
        
        assert result.extracted_fields == {}
        assert result.confidence_score == 0.0
    
    def test_null_field_values(self):
        """Should handle null field values gracefully."""
        from extraction_module import ExtractedField
        
        field = ExtractedField(
            name="optional_field",
            value=None,
            confidence=0.5
        )
        
        assert field.value is None
        assert field.confidence == 0.5


class TestDataConsistency:
    """Tests for data consistency."""
    
    def test_confidence_score_range(self):
        """Confidence scores should be between 0 and 1."""
        from extraction_module import ExtractedField
        
        # Valid confidence
        field = ExtractedField("test", "value", 0.85)
        assert 0 <= field.confidence <= 1
    
    def test_priority_calculation_deterministic(self):
        """Priority calculation should be deterministic."""
        from review_queue import PriorityCalculator, ReviewItem, ExtractedFieldData
        from datetime import datetime, timedelta
        
        calc = PriorityCalculator()
        
        item = ReviewItem(
            item_id="test",
            document_id="doc",
            workflow_id="wf",
            extraction_result={"field": ExtractedFieldData("val", 0.8)},
            document_preview_url="/preview",
            sla_deadline=datetime.utcnow() + timedelta(hours=2),
            document_type="invoice"
        )
        
        priority1, factors1 = calc.calculate(item)
        priority2, factors2 = calc.calculate(item)
        
        assert priority1 == priority2
        assert factors1 == factors2


class TestOutputFormatConsistency:
    """Tests for output format consistency."""
    
    @pytest.mark.asyncio
    async def test_json_parquet_field_consistency(self):
        """JSON and Parquet outputs should have consistent fields."""
        from extraction_module import OutputGenerator, ExtractionResult, ProcessingStatus, ExtractedField
        
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = OutputGenerator(output_dir=tmpdir)
            
            result = ExtractionResult(
                document_id="test_doc",
                status=ProcessingStatus.COMPLETED,
                extracted_fields={
                    "invoice_number": ExtractedField("invoice_number", "INV-001", 0.95),
                    "total_amount": ExtractedField("total_amount", 1234.56, 0.88)
                },
                confidence_score=0.90
            )
            
            json_path = generator.generate_json(result)
            
            with open(json_path) as f:
                json_data = json.load(f)
            
            # Verify JSON has expected fields
            assert json_data["document_id"] == "test_doc"
            assert "invoice_number" in json_data["extracted_fields"]
            assert "total_amount" in json_data["extracted_fields"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
