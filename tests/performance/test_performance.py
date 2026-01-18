"""
Performance Tests for DocFlow

Tests for latency, throughput, and concurrency.
"""

import asyncio
import pytest
import time
from datetime import datetime
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from extraction_module import DocumentType, ProcessingConfig


class TestLatency:
    """Tests for processing latency."""
    
    @pytest.mark.asyncio
    async def test_single_document_latency(self):
        """Single document should process under P95 target (30s)."""
        from extraction_module import ExtractionModule, DocumentInput
        
        with tempfile.TemporaryDirectory() as tmpdir:
            module = ExtractionModule(output_dir=tmpdir)
            
            doc = DocumentInput(
                document_id="latency_test",
                content=b"Test invoice content",
                content_hash="hash123",
                document_type=DocumentType.INVOICE,
                processing_config=ProcessingConfig(enable_ocr=False)
            )
            
            start = time.time()
            result = await module.process(doc)
            duration = time.time() - start
            
            assert result is not None
            assert duration < 30  # P95 target
    
    @pytest.mark.asyncio
    async def test_workflow_step_latency(self):
        """Individual workflow steps should complete quickly."""
        from workflow_executor import WorkflowDAG, WorkflowStep, StepType, WorkflowExecutor
        
        dag = WorkflowDAG("latency", "Latency Test")
        
        async def fast_handler(ctx):
            return {"done": True}
        
        dag.add_step(WorkflowStep("fast", StepType.CUSTOM, "Fast Step", handler=fast_handler))
        
        executor = WorkflowExecutor(dag)
        
        start = time.time()
        result = await executor.execute("doc_001")
        duration = time.time() - start
        
        assert duration < 1  # Single step should be very fast


class TestThroughput:
    """Tests for processing throughput."""
    
    @pytest.mark.asyncio
    async def test_batch_processing_rate(self):
        """Batch processing should meet throughput targets."""
        from workflow_executor import WorkflowDAG, WorkflowStep, StepType, BatchWorkflowExecutor
        
        dag = WorkflowDAG("throughput", "Throughput Test")
        
        async def handler(ctx):
            await asyncio.sleep(0.01)  # Simulate 10ms processing
            return {"done": True}
        
        dag.add_step(WorkflowStep("process", StepType.CUSTOM, "Process", handler=handler))
        
        executor = BatchWorkflowExecutor(dag, max_concurrent_documents=50)
        
        doc_ids = [f"doc_{i:04d}" for i in range(100)]
        
        start = time.time()
        results = await executor.execute_batch(doc_ids)
        duration = time.time() - start
        
        docs_per_second = len(results) / duration
        docs_per_hour = docs_per_second * 3600
        
        # With 50 concurrent and 10ms each, should be ~5000/s theoretical
        # We just check it's reasonably fast
        assert len(results) == 100
        assert docs_per_hour > 1000  # At least 1000/hr


class TestConcurrency:
    """Tests for concurrent processing."""
    
    @pytest.mark.asyncio
    async def test_concurrent_claims(self):
        """Multiple reviewers should be able to claim different items."""
        from review_queue import ReviewQueueManager, ExtractedFieldData
        
        queue = ReviewQueueManager()
        
        # Add multiple items
        items = []
        for i in range(5):
            item = await queue.add_item(
                document_id=f"doc_{i:03d}",
                workflow_id="wf_001",
                extraction_result={"field": ExtractedFieldData(f"value_{i}", 0.9)},
                document_preview_url=f"/preview/doc_{i}.pdf",
                document_type=DocumentType.INVOICE
            )
            items.append(item)
        
        # Concurrent claims by different reviewers
        async def claim(reviewer_id, item_id):
            return await queue.claim_item(item_id, reviewer_id)
        
        claims = await asyncio.gather(*[
            claim(f"reviewer_{i}", items[i].item_id)
            for i in range(5)
        ])
        
        # All claims should succeed
        assert all(result[0] for result in claims)
    
    @pytest.mark.asyncio
    async def test_concurrent_workflow_execution(self):
        """Multiple workflows should execute concurrently."""
        from workflow_executor import WorkflowDAG, WorkflowStep, StepType, WorkflowExecutor
        
        dag = WorkflowDAG("concurrent", "Concurrent Test")
        
        async def slow_handler(ctx):
            await asyncio.sleep(0.1)
            return {"done": True}
        
        dag.add_step(WorkflowStep("slow", StepType.CUSTOM, "Slow", handler=slow_handler))
        
        executor = WorkflowExecutor(dag)
        
        # Run 5 workflows concurrently
        start = time.time()
        results = await asyncio.gather(*[
            executor.execute(f"doc_{i:03d}")
            for i in range(5)
        ])
        duration = time.time() - start
        
        # 5 workflows at 0.1s each should complete in ~0.1s (parallel), not 0.5s (sequential)
        assert len(results) == 5
        assert duration < 0.3  # Allow some overhead


class TestStressTest:
    """Stress tests for high load scenarios."""
    
    @pytest.mark.asyncio
    async def test_queue_under_load(self):
        """Queue should handle many items without degradation."""
        from review_queue import ReviewQueueManager, ExtractedFieldData
        
        queue = ReviewQueueManager()
        
        # Add 100 items
        for i in range(100):
            await queue.add_item(
                document_id=f"doc_{i:04d}",
                workflow_id="wf_001",
                extraction_result={"field": ExtractedFieldData(f"value_{i}", 0.9)},
                document_preview_url=f"/preview/doc_{i}.pdf",
                document_type=DocumentType.INVOICE
            )
        
        # Get stats
        stats = await queue.get_stats()
        assert stats.total_pending == 100
        
        # Get queue - should be fast even with many items
        start = time.time()
        items, total = await queue.get_queue(limit=20)
        duration = time.time() - start
        
        assert len(items) == 20
        assert total == 100
        assert duration < 1  # Should be fast


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
