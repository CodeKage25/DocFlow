"""
Integration Tests for DocFlow

Tests for component interactions and end-to-end flows.
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.fixture
def temp_output_dir():
    """Create temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestWorkflowExecution:
    """Integration tests for workflow execution."""
    
    @pytest.mark.asyncio
    async def test_simple_workflow_execution(self):
        """Should execute a simple workflow end-to-end."""
        from workflow_executor import WorkflowDAG, WorkflowStep, StepType, WorkflowExecutor, WorkflowStatus
        
        dag = WorkflowDAG("test", "Test Workflow")
        
        async def ingest(ctx):
            return {"ingested": True}
        
        async def extract(ctx):
            return {"extracted": True, "from_ingest": ctx.step_outputs.get("ingest")}
        
        async def output(ctx):
            return {"completed": True}
        
        dag.add_step(WorkflowStep("ingest", StepType.INGEST, "Ingest", handler=ingest))
        dag.add_step(WorkflowStep("extract", StepType.EXTRACT, "Extract", handler=extract, dependencies={"ingest"}))
        dag.add_step(WorkflowStep("output", StepType.OUTPUT, "Output", handler=output, dependencies={"extract"}))
        
        executor = WorkflowExecutor(dag)
        result = await executor.execute("doc_001")
        
        assert result.status == WorkflowStatus.COMPLETED
        assert len(result.step_results) == 3
    
    @pytest.mark.asyncio
    async def test_parallel_steps(self):
        """Independent steps should run in parallel."""
        from workflow_executor import WorkflowDAG, WorkflowStep, StepType, WorkflowExecutor
        
        dag = WorkflowDAG("parallel", "Parallel Workflow")
        
        async def step_a(ctx):
            await asyncio.sleep(0.1)
            return {"step": "A"}
        
        async def step_b(ctx):
            await asyncio.sleep(0.1)
            return {"step": "B"}
        
        async def step_c(ctx):
            return {"step": "C"}
        
        dag.add_step(WorkflowStep("a", StepType.CUSTOM, "A", handler=step_a))
        dag.add_step(WorkflowStep("b", StepType.CUSTOM, "B", handler=step_b))
        dag.add_step(WorkflowStep("c", StepType.CUSTOM, "C", handler=step_c, dependencies={"a", "b"}))
        
        executor = WorkflowExecutor(dag)
        
        start = datetime.utcnow()
        result = await executor.execute("doc_001")
        duration = (datetime.utcnow() - start).total_seconds()
        
        # A and B run in parallel (0.1s each), then C
        # Should be ~0.1s, not 0.2s
        assert duration < 0.2


class TestReviewQueueIntegration:
    """Integration tests for review queue."""
    
    @pytest.mark.asyncio
    async def test_claim_and_submit_flow(self):
        """Should complete claim -> review -> submit flow."""
        from review_queue import ReviewQueueManager, ReviewDecision, ExtractedFieldData
        
        queue = ReviewQueueManager()
        
        # Add item
        item = await queue.add_item(
            document_id="doc_001",
            workflow_id="wf_001",
            extraction_result={
                "invoice_number": ExtractedFieldData("INV-001", 0.95),
                "total_amount": ExtractedFieldData(1234.56, 0.88)
            },
            document_preview_url="/preview/doc_001.pdf",
            document_type="invoice"
        )
        
        # Claim item
        success, claimed_item, error = await queue.claim_item(item.item_id, "reviewer_001")
        assert success
        assert claimed_item.assigned_to == "reviewer_001"
        
        # Submit review
        success, result, error = await queue.submit_review(
            item_id=item.item_id,
            reviewer_id="reviewer_001",
            decision=ReviewDecision.APPROVE
        )
        
        assert success
        assert result.decision == ReviewDecision.APPROVE
    
    @pytest.mark.asyncio
    async def test_sla_tracking(self):
        """Should track SLA deadlines correctly."""
        from review_queue import ReviewQueueManager, ExtractedFieldData
        
        queue = ReviewQueueManager()
        
        # Add item with short SLA
        item = await queue.add_item(
            document_id="doc_001",
            workflow_id="wf_001",
            extraction_result={"field": ExtractedFieldData("value", 0.9)},
            document_preview_url="/preview/doc_001.pdf",
            document_type="invoice",
            sla_hours=1  # 1 hour SLA
        )
        
        # Check that SLA deadline is set
        assert item.sla_deadline is not None
        assert item.sla_deadline > datetime.utcnow()


class TestExtractionPipeline:
    """Integration tests for extraction pipeline."""
    
    @pytest.mark.asyncio
    async def test_extract_and_output(self, temp_output_dir):
        """Should extract fields and generate output."""
        from extraction_module import ExtractionModule, DocumentInput
        
        module = ExtractionModule(output_dir=temp_output_dir, mock_mode=True)
        
        doc = DocumentInput(
            document_id="test_doc",
            content=b"Invoice Number: INV-001\nTotal: $1000.00",
            content_hash="hash123",
            document_type="invoice"
        )
        
        result = await module.process(doc)
        
        assert result is not None
        assert len(result.extracted_fields) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
