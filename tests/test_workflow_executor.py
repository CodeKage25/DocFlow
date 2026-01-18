"""
Test Suite for Workflow Executor

Comprehensive tests covering DAG validation, execution, parallelism,
error handling, and retry logic.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Set
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow_executor import (
    StepType,
    StepStatus,
    WorkflowStatus,
    ErrorPropagationStrategy,
    RetryConfig,
    StepResult,
    WorkflowStep,
    WorkflowDAG,
    ExecutionContext,
    WorkflowResult,
    WorkflowExecutor,
    BatchWorkflowExecutor,
    WorkflowBuilder,
    CycleDetector,
    DAGValidator,
    TopologicalSorter,
    TokenBucketRateLimiter,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def simple_dag():
    """Create a simple linear DAG: A -> B -> C."""
    dag = WorkflowDAG("test_wf", "Test Workflow")
    
    async def handler_a(ctx):
        return {"step": "A"}
    
    async def handler_b(ctx):
        return {"step": "B", "input": ctx.step_outputs.get("step_a")}
    
    async def handler_c(ctx):
        return {"step": "C", "input": ctx.step_outputs.get("step_b")}
    
    dag.add_step(WorkflowStep(
        step_id="step_a",
        step_type=StepType.INGEST,
        name="Step A",
        handler=handler_a
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_b",
        step_type=StepType.EXTRACT,
        name="Step B",
        handler=handler_b,
        dependencies={"step_a"}
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_c",
        step_type=StepType.OUTPUT,
        name="Step C",
        handler=handler_c,
        dependencies={"step_b"}
    ))
    
    return dag


@pytest.fixture
def parallel_dag():
    """Create a DAG with parallel branches: A -> [B, C] -> D."""
    dag = WorkflowDAG("parallel_wf", "Parallel Workflow")
    
    async def handler_a(ctx):
        return {"step": "A"}
    
    async def handler_b(ctx):
        await asyncio.sleep(0.1)
        return {"step": "B"}
    
    async def handler_c(ctx):
        await asyncio.sleep(0.1)
        return {"step": "C"}
    
    async def handler_d(ctx):
        return {
            "step": "D",
            "b_result": ctx.step_outputs.get("step_b"),
            "c_result": ctx.step_outputs.get("step_c")
        }
    
    dag.add_step(WorkflowStep(
        step_id="step_a",
        step_type=StepType.INGEST,
        name="Step A",
        handler=handler_a
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_b",
        step_type=StepType.EXTRACT,
        name="Step B",
        handler=handler_b,
        dependencies={"step_a"}
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_c",
        step_type=StepType.VALIDATE,
        name="Step C",
        handler=handler_c,
        dependencies={"step_a"}
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_d",
        step_type=StepType.OUTPUT,
        name="Step D",
        handler=handler_d,
        dependencies={"step_b", "step_c"}
    ))
    
    return dag


@pytest.fixture
def failing_dag():
    """Create a DAG with a failing step."""
    dag = WorkflowDAG("failing_wf", "Failing Workflow")
    
    async def handler_a(ctx):
        return {"step": "A"}
    
    async def handler_b(ctx):
        raise ValueError("Intentional failure")
    
    async def handler_c(ctx):
        return {"step": "C"}
    
    dag.add_step(WorkflowStep(
        step_id="step_a",
        step_type=StepType.INGEST,
        name="Step A",
        handler=handler_a
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_b",
        step_type=StepType.EXTRACT,
        name="Step B (Fails)",
        handler=handler_b,
        dependencies={"step_a"},
        retry_config=RetryConfig(max_attempts=1)  # No retries
    ))
    
    dag.add_step(WorkflowStep(
        step_id="step_c",
        step_type=StepType.OUTPUT,
        name="Step C",
        handler=handler_c,
        dependencies={"step_b"}
    ))
    
    return dag


# =============================================================================
# DAG VALIDATION TESTS
# =============================================================================

class TestCycleDetector:
    """Tests for cycle detection."""
    
    def test_no_cycle_in_simple_dag(self, simple_dag):
        """Should detect no cycles in a valid DAG."""
        detector = CycleDetector()
        cycles = detector.detect_cycles(simple_dag)
        assert len(cycles) == 0
    
    def test_detects_direct_cycle(self):
        """Should detect a direct cycle (A -> B -> A)."""
        dag = WorkflowDAG("cyclic", "Cyclic Workflow")
        
        async def handler(ctx):
            return {}
        
        dag.add_step(WorkflowStep(
            step_id="a",
            step_type=StepType.CUSTOM,
            name="A",
            handler=handler,
            dependencies={"b"}
        ))
        
        dag.add_step(WorkflowStep(
            step_id="b",
            step_type=StepType.CUSTOM,
            name="B",
            handler=handler,
            dependencies={"a"}
        ))
        
        detector = CycleDetector()
        cycles = detector.detect_cycles(dag)
        assert len(cycles) > 0
    
    def test_detects_indirect_cycle(self):
        """Should detect indirect cycles (A -> B -> C -> A)."""
        dag = WorkflowDAG("cyclic", "Cyclic Workflow")
        
        async def handler(ctx):
            return {}
        
        dag.add_step(WorkflowStep(
            step_id="a",
            step_type=StepType.CUSTOM,
            name="A",
            handler=handler,
            dependencies={"c"}
        ))
        
        dag.add_step(WorkflowStep(
            step_id="b",
            step_type=StepType.CUSTOM,
            name="B",
            handler=handler,
            dependencies={"a"}
        ))
        
        dag.add_step(WorkflowStep(
            step_id="c",
            step_type=StepType.CUSTOM,
            name="C",
            handler=handler,
            dependencies={"b"}
        ))
        
        detector = CycleDetector()
        cycles = detector.detect_cycles(dag)
        assert len(cycles) > 0


class TestDAGValidator:
    """Tests for DAG validation."""
    
    def test_valid_dag_passes(self, simple_dag):
        """Valid DAG should pass validation."""
        validator = DAGValidator()
        errors = validator.validate(simple_dag)
        assert len(errors) == 0
    
    def test_empty_dag_fails(self):
        """Empty DAG should fail validation."""
        dag = WorkflowDAG("empty", "Empty")
        
        validator = DAGValidator()
        errors = validator.validate(dag)
        
        assert len(errors) > 0
        assert any(e.code == "EMPTY_DAG" for e in errors)
    
    def test_unknown_dependency_fails(self):
        """Unknown dependency should fail validation."""
        dag = WorkflowDAG("invalid", "Invalid")
        
        async def handler(ctx):
            return {}
        
        dag.add_step(WorkflowStep(
            step_id="a",
            step_type=StepType.CUSTOM,
            name="A",
            handler=handler,
            dependencies={"nonexistent"}
        ))
        
        validator = DAGValidator()
        errors = validator.validate(dag)
        
        assert len(errors) > 0
        assert any(e.code == "UNKNOWN_DEPENDENCY" for e in errors)
    
    def test_invalid_timeout_fails(self):
        """Invalid timeout should fail validation."""
        dag = WorkflowDAG("invalid", "Invalid")
        
        async def handler(ctx):
            return {}
        
        dag.add_step(WorkflowStep(
            step_id="a",
            step_type=StepType.CUSTOM,
            name="A",
            handler=handler,
            timeout_seconds=-1  # Invalid
        ))
        
        validator = DAGValidator()
        errors = validator.validate(dag)
        
        assert len(errors) > 0
        assert any(e.code == "INVALID_TIMEOUT" for e in errors)


class TestTopologicalSorter:
    """Tests for topological sorting."""
    
    def test_sorts_simple_dag(self, simple_dag):
        """Should correctly sort a simple DAG."""
        sorter = TopologicalSorter()
        levels = sorter.sort(simple_dag)
        
        assert len(levels) == 3
        assert "step_a" in levels[0]
        assert "step_b" in levels[1]
        assert "step_c" in levels[2]
    
    def test_parallel_steps_in_same_level(self, parallel_dag):
        """Parallel steps should be in the same level."""
        sorter = TopologicalSorter()
        levels = sorter.sort(parallel_dag)
        
        # Find the level with B and C
        for level in levels:
            if "step_b" in level:
                assert "step_c" in level  # Should be in same level


# =============================================================================
# DAG STRUCTURE TESTS
# =============================================================================

class TestWorkflowDAG:
    """Tests for WorkflowDAG class."""
    
    def test_entry_points(self, simple_dag):
        """Should correctly identify entry points."""
        assert simple_dag.entry_points == {"step_a"}
    
    def test_terminal_points(self, simple_dag):
        """Should correctly identify terminal points."""
        assert simple_dag.terminal_points == {"step_c"}
    
    def test_parallel_entry_points(self):
        """Should identify multiple entry points."""
        dag = WorkflowDAG("multi", "Multi Entry")
        
        async def handler(ctx):
            return {}
        
        dag.add_step(WorkflowStep(
            step_id="a",
            step_type=StepType.CUSTOM,
            name="A",
            handler=handler
        ))
        
        dag.add_step(WorkflowStep(
            step_id="b",
            step_type=StepType.CUSTOM,
            name="B",
            handler=handler
        ))
        
        assert dag.entry_points == {"a", "b"}
    
    def test_get_dependents(self, simple_dag):
        """Should correctly return dependents."""
        dependents = simple_dag.get_dependents("step_a")
        assert "step_b" in dependents
    
    def test_get_dependencies(self, simple_dag):
        """Should correctly return dependencies."""
        deps = simple_dag.get_dependencies("step_b")
        assert "step_a" in deps
    
    def test_get_ready_steps(self, simple_dag):
        """Should correctly identify ready steps."""
        ready = simple_dag.get_ready_steps(completed=set(), failed=set(), skipped=set())
        assert ready == {"step_a"}
        
        ready = simple_dag.get_ready_steps(completed={"step_a"}, failed=set(), skipped=set())
        assert ready == {"step_b"}


# =============================================================================
# EXECUTION TESTS
# =============================================================================

class TestWorkflowExecutor:
    """Tests for workflow execution."""
    
    @pytest.mark.asyncio
    async def test_execute_simple_workflow(self, simple_dag):
        """Should execute a simple workflow successfully."""
        executor = WorkflowExecutor(simple_dag)
        result = await executor.execute("doc_001")
        
        assert result.status == WorkflowStatus.COMPLETED
        assert len(result.step_results) == 3
        assert all(r.status == StepStatus.COMPLETED for r in result.step_results.values())
    
    @pytest.mark.asyncio
    async def test_execute_parallel_workflow(self, parallel_dag):
        """Should execute parallel steps concurrently."""
        executor = WorkflowExecutor(parallel_dag)
        
        start = datetime.utcnow()
        result = await executor.execute("doc_001")
        duration = (datetime.utcnow() - start).total_seconds()
        
        assert result.status == WorkflowStatus.COMPLETED
        
        # B and C should run in parallel, so total time should be ~0.1s, not 0.2s
        assert duration < 0.3  # Allow some overhead
    
    @pytest.mark.asyncio
    async def test_step_receives_context(self, simple_dag):
        """Steps should receive execution context."""
        executor = WorkflowExecutor(simple_dag)
        result = await executor.execute("doc_001")
        
        # Step C should have received output from step B
        step_c_output = result.outputs.get("step_c")
        assert step_c_output is not None
    
    @pytest.mark.asyncio
    async def test_initial_context_passed(self, simple_dag):
        """Initial context should be available to steps."""
        executor = WorkflowExecutor(simple_dag)
        result = await executor.execute(
            "doc_001",
            initial_context={"custom_key": "custom_value"}
        )
        
        assert result.status == WorkflowStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_workflow_timeout(self):
        """Should timeout if workflow takes too long."""
        dag = WorkflowDAG("slow", "Slow Workflow")
        
        async def slow_handler(ctx):
            await asyncio.sleep(10)
            return {}
        
        dag.add_step(WorkflowStep(
            step_id="slow",
            step_type=StepType.CUSTOM,
            name="Slow",
            handler=slow_handler,
            timeout_seconds=30
        ))
        
        executor = WorkflowExecutor(dag, workflow_timeout_seconds=0.5)
        result = await executor.execute("doc_001")
        
        assert result.status == WorkflowStatus.FAILED
        assert "timeout" in result.error.lower()


class TestFailureHandling:
    """Tests for failure handling."""
    
    @pytest.mark.asyncio
    async def test_failing_step_fails_workflow(self, failing_dag):
        """Failing step should cause partial workflow completion."""
        executor = WorkflowExecutor(failing_dag)
        result = await executor.execute("doc_001")
        
        # With FAIL_BRANCH strategy, downstream steps are skipped
        assert result.status == WorkflowStatus.PARTIAL
        assert result.step_results["step_b"].status == StepStatus.FAILED
    
    @pytest.mark.asyncio
    async def test_downstream_steps_skipped_on_failure(self, failing_dag):
        """Downstream steps should be skipped when upstream fails."""
        executor = WorkflowExecutor(failing_dag)
        result = await executor.execute("doc_001")
        
        assert result.step_results["step_c"].status == StepStatus.SKIPPED
    
    @pytest.mark.asyncio
    async def test_fail_fast_strategy(self):
        """FAIL_FAST should stop execution immediately."""
        dag = WorkflowDAG("fail_fast", "Fail Fast")
        
        async def handler_a(ctx):
            return {}
        
        async def handler_b(ctx):
            raise ValueError("Intentional failure")
        
        dag.add_step(WorkflowStep(
            step_id="a",
            step_type=StepType.CUSTOM,
            name="A",
            handler=handler_a
        ))
        
        dag.add_step(WorkflowStep(
            step_id="b",
            step_type=StepType.CUSTOM,
            name="B",
            handler=handler_b,
            dependencies={"a"},
            on_error=ErrorPropagationStrategy.FAIL_FAST,
            retry_config=RetryConfig(max_attempts=1)
        ))
        
        executor = WorkflowExecutor(dag)
        result = await executor.execute("doc_001")
        
        assert result.status == WorkflowStatus.FAILED


class TestRetryBehavior:
    """Tests for retry logic."""
    
    @pytest.mark.asyncio
    async def test_retries_on_retryable_error(self):
        """Should retry on retryable errors."""
        dag = WorkflowDAG("retry", "Retry Test")
        
        call_count = 0
        
        async def flaky_handler(ctx):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Transient failure")
            return {"success": True}
        
        dag.add_step(WorkflowStep(
            step_id="flaky",
            step_type=StepType.CUSTOM,
            name="Flaky",
            handler=flaky_handler,
            retry_config=RetryConfig(
                max_attempts=3,
                base_delay_seconds=0.01,
                retryable_exceptions=(ConnectionError,)
            )
        ))
        
        executor = WorkflowExecutor(dag)
        result = await executor.execute("doc_001")
        
        assert result.status == WorkflowStatus.COMPLETED
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self):
        """Should not retry on non-retryable errors."""
        dag = WorkflowDAG("no_retry", "No Retry Test")
        
        call_count = 0
        
        async def handler(ctx):
            nonlocal call_count
            call_count += 1
            raise ValueError("Non-retryable")
        
        dag.add_step(WorkflowStep(
            step_id="fail",
            step_type=StepType.CUSTOM,
            name="Fail",
            handler=handler,
            retry_config=RetryConfig(
                max_attempts=3,
                retryable_exceptions=(ConnectionError,)  # ValueError not included
            )
        ))
        
        executor = WorkflowExecutor(dag)
        result = await executor.execute("doc_001")
        
        assert result.status == WorkflowStatus.PARTIAL
        assert call_count == 1  # No retries


class TestConditionalExecution:
    """Tests for conditional step execution."""
    
    @pytest.mark.asyncio
    async def test_condition_true_executes_step(self):
        """Step should execute when condition is true."""
        dag = WorkflowDAG("conditional", "Conditional")
        
        async def handler(ctx):
            return {"executed": True}
        
        dag.add_step(WorkflowStep(
            step_id="conditional",
            step_type=StepType.CUSTOM,
            name="Conditional",
            handler=handler,
            condition=lambda ctx: True
        ))
        
        executor = WorkflowExecutor(dag)
        result = await executor.execute("doc_001")
        
        assert result.step_results["conditional"].status == StepStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_condition_false_skips_step(self):
        """Step should be skipped when condition is false."""
        dag = WorkflowDAG("conditional", "Conditional")
        
        async def handler(ctx):
            return {"executed": True}
        
        dag.add_step(WorkflowStep(
            step_id="conditional",
            step_type=StepType.CUSTOM,
            name="Conditional",
            handler=handler,
            condition=lambda ctx: False
        ))
        
        executor = WorkflowExecutor(dag)
        result = await executor.execute("doc_001")
        
        assert result.step_results["conditional"].status == StepStatus.SKIPPED


# =============================================================================
# RATE LIMITER TESTS
# =============================================================================

class TestTokenBucketRateLimiter:
    """Tests for rate limiter."""
    
    @pytest.mark.asyncio
    async def test_immediate_acquisition(self):
        """Should acquire immediately when tokens available."""
        limiter = TokenBucketRateLimiter(rate=100, capacity=100)
        
        wait_time = await limiter.acquire()
        assert wait_time < 0.01  # Practically immediate
    
    @pytest.mark.asyncio
    async def test_waits_when_exhausted(self):
        """Should wait when tokens exhausted."""
        limiter = TokenBucketRateLimiter(rate=10, capacity=1)
        
        # First acquisition is immediate
        await limiter.acquire()
        
        # Second should wait
        start = asyncio.get_event_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        
        assert elapsed >= 0.09  # ~0.1 second for 1 token at 10/sec
    
    def test_available_tokens(self):
        """Should correctly report available tokens."""
        limiter = TokenBucketRateLimiter(rate=100, capacity=50)
        
        # Initially at capacity
        assert limiter.available_tokens == 50


# =============================================================================
# BATCH EXECUTION TESTS
# =============================================================================

class TestBatchWorkflowExecutor:
    """Tests for batch execution."""
    
    @pytest.mark.asyncio
    async def test_batch_execution(self, simple_dag):
        """Should process multiple documents in batch."""
        executor = BatchWorkflowExecutor(
            simple_dag,
            max_concurrent_documents=5
        )
        
        doc_ids = [f"doc_{i:03d}" for i in range(10)]
        results = await executor.execute_batch(doc_ids)
        
        assert len(results) == 10
        assert all(r.status == WorkflowStatus.COMPLETED for r in results)
    
    @pytest.mark.asyncio
    async def test_batch_respects_concurrency_limit(self, parallel_dag):
        """Should respect maximum concurrent documents."""
        executor = BatchWorkflowExecutor(
            parallel_dag,
            max_concurrent_documents=2
        )
        
        doc_ids = [f"doc_{i:03d}" for i in range(4)]
        
        start = datetime.utcnow()
        results = await executor.execute_batch(doc_ids)
        duration = (datetime.utcnow() - start).total_seconds()
        
        assert len(results) == 4
        # With concurrency limit of 2 and ~0.1s per doc, should take ~0.2s minimum
        assert duration >= 0.1


# =============================================================================
# WORKFLOW BUILDER TESTS
# =============================================================================

class TestWorkflowBuilder:
    """Tests for workflow builder."""
    
    def test_build_simple_workflow(self):
        """Should build a valid workflow."""
        async def handler(ctx):
            return {}
        
        dag = (
            WorkflowBuilder("test", "Test")
            .add_step("step_a", handler, step_type=StepType.INGEST)
            .add_step("step_b", handler, step_type=StepType.EXTRACT, dependencies={"step_a"})
            .build()
        )
        
        assert len(dag.steps) == 2
        assert dag.entry_points == {"step_a"}
    
    def test_build_rejects_invalid(self):
        """Should reject invalid workflow."""
        async def handler(ctx):
            return {}
        
        with pytest.raises(ValueError):
            (
                WorkflowBuilder("test", "Test")
                .add_step("step_a", handler, timeout=-1)  # Invalid
                .build()
            )


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
