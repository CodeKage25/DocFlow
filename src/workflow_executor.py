"""
Workflow Executor Module

Production-grade workflow orchestration system with DAG-based execution,
parallelism control, rate limiting, and robust failure handling.

"""

import asyncio
import logging
import random
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Type

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)




class StepType(str, Enum):
    """Types of workflow steps."""
    INGEST = "ingest"
    EXTRACT = "extract"
    VALIDATE = "validate"
    TRANSFORM = "transform"
    OUTPUT = "output"
    REVIEW = "review"
    CUSTOM = "custom"


class StepStatus(str, Enum):
    """Status of a workflow step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WorkflowStatus(str, Enum):
    """Status of a workflow execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorPropagationStrategy(str, Enum):
    """How errors propagate through the workflow."""
    FAIL_FAST = "fail_fast"
    CONTINUE = "continue"
    FAIL_BRANCH = "fail_branch"




@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.1
    retryable_exceptions: Tuple[Type[Exception], ...] = (
        TimeoutError,
        ConnectionError,
        OSError,
    )


@dataclass
class StepResult:
    """Result of executing a workflow step."""
    step_id: str
    status: StepStatus
    output: Any = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    attempts: int = 1
    retryable: bool = True


@dataclass
class WorkflowStep:
    """A single step in the workflow DAG."""
    step_id: str
    step_type: StepType
    name: str
    handler: Callable[[Any], Coroutine[Any, Any, Any]]
    config: Dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    retry_config: RetryConfig = field(default_factory=RetryConfig)
    dependencies: Set[str] = field(default_factory=set)
    condition: Optional[Callable[[Any], bool]] = None
    on_error: ErrorPropagationStrategy = ErrorPropagationStrategy.FAIL_BRANCH
    
    def __hash__(self):
        return hash(self.step_id)


@dataclass
class ExecutionContext:
    """Context passed through workflow execution."""
    document_id: str
    workflow_id: str
    step_outputs: Dict[str, Any] = field(default_factory=dict)
    step_results: Dict[str, StepResult] = field(default_factory=dict)
    completed_steps: Set[str] = field(default_factory=set)
    failed_steps: Set[str] = field(default_factory=set)
    skipped_steps: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    
    def mark_completed(self, step_id: str, output: Any):
        """Mark a step as completed."""
        self.completed_steps.add(step_id)
        self.step_outputs[step_id] = output
    
    def mark_failed(self, step_id: str, error: str):
        """Mark a step as failed."""
        self.failed_steps.add(step_id)
    
    def mark_skipped(self, step_id: str, reason: str):
        """Mark a step as skipped."""
        self.skipped_steps.add(step_id)
        self.step_outputs[step_id] = {"skipped": True, "reason": reason}


@dataclass
class WorkflowResult:
    """Result of a complete workflow execution."""
    workflow_id: str
    document_id: str
    status: WorkflowStatus
    step_results: Dict[str, StepResult]
    outputs: Dict[str, Any]
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    error: Optional[str] = None


@dataclass
class ValidationError:
    """DAG validation error."""
    code: str
    message: str
    step_id: Optional[str] = None




class TokenBucketRateLimiter:
    """Token bucket rate limiter for controlling throughput."""
    
    def __init__(self, rate: float, capacity: float):
        """
        Initialize rate limiter.
        
        Args:
            rate: Tokens per second
            capacity: Maximum burst capacity
        """
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> float:
        """
        Acquire tokens, waiting if necessary.
        
        Returns the time spent waiting.
        """
        async with self._lock:
            waited = 0.0
            
            while True:
                now = time.monotonic()
                elapsed = now - self._last_update
                self._tokens = min(
                    self.capacity,
                    self._tokens + elapsed * self.rate
                )
                self._last_update = now
                
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                
                needed = tokens - self._tokens
                wait_time = needed / self.rate
                await asyncio.sleep(wait_time)
                waited += wait_time
    
    @property
    def available_tokens(self) -> float:
        """Get current available tokens."""
        now = time.monotonic()
        elapsed = now - self._last_update
        return min(self.capacity, self._tokens + elapsed * self.rate)




class WorkflowDAG:
    """DAG representation of a document processing workflow."""
    
    def __init__(self, workflow_id: str, name: str):
        self.workflow_id = workflow_id
        self.name = name
        self.steps: Dict[str, WorkflowStep] = {}
        self._adjacency: Dict[str, Set[str]] = defaultdict(set)  # step -> dependents
        self._reverse_adjacency: Dict[str, Set[str]] = defaultdict(set)  # step -> dependencies
    
    def add_step(self, step: WorkflowStep):
        """Add a step to the DAG."""
        self.steps[step.step_id] = step
        
        for dep in step.dependencies:
            self._adjacency[dep].add(step.step_id)
            self._reverse_adjacency[step.step_id].add(dep)
    
    @property
    def entry_points(self) -> Set[str]:
        """Get steps with no dependencies."""
        return {
            step_id for step_id, step in self.steps.items()
            if not step.dependencies
        }
    
    @property
    def terminal_points(self) -> Set[str]:
        """Get steps with no dependents."""
        return {
            step_id for step_id in self.steps
            if not self._adjacency.get(step_id)
        }
    
    def get_dependents(self, step_id: str) -> Set[str]:
        """Get steps that depend on this step."""
        return self._adjacency.get(step_id, set())
    
    def get_dependencies(self, step_id: str) -> Set[str]:
        """Get steps this step depends on."""
        return self._reverse_adjacency.get(step_id, set())
    
    def get_ready_steps(self, completed: Set[str], failed: Set[str], skipped: Set[str]) -> Set[str]:
        """Get steps ready to execute (all dependencies satisfied)."""
        blocked = completed | failed | skipped
        ready = set()
        
        for step_id, step in self.steps.items():
            if step_id in blocked:
                continue
            
            # Check if all dependencies are satisfied
            deps_satisfied = all(
                dep in completed or dep in skipped
                for dep in step.dependencies
            )
            
            # Check if any dependency failed (for FAIL_BRANCH strategy)
            deps_failed = any(dep in failed for dep in step.dependencies)
            
            if deps_satisfied and not deps_failed:
                ready.add(step_id)
        
        return ready


class CycleDetector:
    """Detects cycles in the workflow DAG."""
    
    def detect_cycles(self, dag: WorkflowDAG) -> List[List[str]]:
        """
        Detect all cycles using DFS coloring.
        Returns list of cycles found.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {step_id: WHITE for step_id in dag.steps}
        parent = {step_id: None for step_id in dag.steps}
        cycles = []
        
        def dfs(node: str):
            color[node] = GRAY
            
            for dependent in dag.get_dependents(node):
                if color[dependent] == GRAY:
                    # Back edge - cycle detected
                    cycle = self._extract_cycle(parent, node, dependent)
                    cycles.append(cycle)
                elif color[dependent] == WHITE:
                    parent[dependent] = node
                    dfs(dependent)
            
            color[node] = BLACK
        
        for step_id in dag.steps:
            if color[step_id] == WHITE:
                dfs(step_id)
        
        return cycles
    
    def _extract_cycle(self, parent: Dict, start: str, end: str) -> List[str]:
        """Extract cycle path from parent map."""
        cycle = [end]
        current = start
        while current != end:
            cycle.append(current)
            current = parent.get(current)
            if current is None:
                break
        cycle.append(end)
        return list(reversed(cycle))


class DAGValidator:
    """Validates workflow DAG structure and configuration."""
    
    def validate(self, dag: WorkflowDAG) -> List[ValidationError]:
        """Run all validation checks on the DAG."""
        errors = []
        
        # Check for empty DAG
        if not dag.steps:
            errors.append(ValidationError(
                "EMPTY_DAG",
                "Workflow has no steps"
            ))
            return errors
        
        # Check for cycles
        cycles = CycleDetector().detect_cycles(dag)
        if cycles:
            errors.append(ValidationError(
                "CYCLE_DETECTED",
                f"Workflow contains cycles: {cycles}"
            ))
        
        # Check for unknown dependencies
        for step in dag.steps.values():
            for dep in step.dependencies:
                if dep not in dag.steps:
                    errors.append(ValidationError(
                        "UNKNOWN_DEPENDENCY",
                        f"Step '{step.step_id}' depends on unknown step '{dep}'",
                        step_id=step.step_id
                    ))
        
        # Check for no entry points
        if not dag.entry_points:
            errors.append(ValidationError(
                "NO_ENTRY_POINT",
                "Workflow has no entry points (all steps have dependencies)"
            ))
        
        # Check timeout configuration
        for step in dag.steps.values():
            if step.timeout_seconds <= 0:
                errors.append(ValidationError(
                    "INVALID_TIMEOUT",
                    f"Step '{step.step_id}' has invalid timeout: {step.timeout_seconds}",
                    step_id=step.step_id
                ))
        
        # Check retry configuration
        for step in dag.steps.values():
            if step.retry_config.max_attempts < 1:
                errors.append(ValidationError(
                    "INVALID_RETRY",
                    f"Step '{step.step_id}' has invalid max_attempts: {step.retry_config.max_attempts}",
                    step_id=step.step_id
                ))
        
        return errors




class TopologicalSorter:
    """Sorts DAG steps in topological order."""
    
    def sort(self, dag: WorkflowDAG) -> List[List[str]]:
        """
        Sort steps into execution levels.
        Each level can be executed in parallel.
        Returns list of levels, where each level is a list of step IDs.
        """
        in_degree = {step_id: len(step.dependencies) for step_id, step in dag.steps.items()}
        levels = []
        remaining = set(dag.steps.keys())
        
        while remaining:
            # Find all steps with no remaining dependencies
            current_level = [
                step_id for step_id in remaining
                if in_degree[step_id] == 0
            ]
            
            if not current_level:
                # Cycle detected (shouldn't happen if validation passed)
                raise ValueError(f"Cycle detected in DAG, remaining steps: {remaining}")
            
            levels.append(current_level)
            
            # Remove current level and update in-degrees
            for step_id in current_level:
                remaining.remove(step_id)
                for dependent in dag.get_dependents(step_id):
                    if dependent in remaining:
                        in_degree[dependent] -= 1
        
        return levels




class WorkflowExecutor:
    """
    Executes workflow DAGs with parallelism and error handling.
    
    Features:
    - DAG validation and topological sort
    - Concurrent step execution with semaphores
    - Rate limiting for external API calls
    - Retry with exponential backoff
    - Conditional step execution
    - Comprehensive error handling
    """
    
    def __init__(
        self,
        dag: WorkflowDAG,
        max_concurrent_steps: int = 10,
        workflow_timeout_seconds: float = 300.0,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ):
        # Validate DAG
        errors = DAGValidator().validate(dag)
        if errors:
            error_msgs = [f"{e.code}: {e.message}" for e in errors]
            raise ValueError(f"Invalid DAG: {error_msgs}")
        
        self.dag = dag
        self.max_concurrent_steps = max_concurrent_steps
        self.workflow_timeout_seconds = workflow_timeout_seconds
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(rate=50, capacity=100)
        
        self._step_semaphore = asyncio.Semaphore(max_concurrent_steps)
        self._execution_levels = TopologicalSorter().sort(dag)
    
    async def execute(
        self,
        document_id: str,
        initial_context: Optional[Dict[str, Any]] = None
    ) -> WorkflowResult:
        """
        Execute the workflow for a document.
        
        Args:
            document_id: ID of the document being processed
            initial_context: Optional initial data to pass to first steps
        
        Returns:
            WorkflowResult with all step results and final status
        """
        context = ExecutionContext(
            document_id=document_id,
            workflow_id=self.dag.workflow_id,
            metadata=initial_context or {},
            started_at=datetime.utcnow()
        )
        
        start_time = time.monotonic()
        workflow_error = None
        
        try:
            # Execute with workflow-level timeout
            await asyncio.wait_for(
                self._execute_workflow(context),
                timeout=self.workflow_timeout_seconds
            )
        except asyncio.TimeoutError:
            workflow_error = f"Workflow timed out after {self.workflow_timeout_seconds}s"
            logger.error(f"Workflow {self.dag.workflow_id} timed out for document {document_id}")
        except Exception as e:
            workflow_error = str(e)
            logger.error(f"Workflow {self.dag.workflow_id} failed: {e}")
        
        # Determine final status
        end_time = time.monotonic()
        duration_ms = int((end_time - start_time) * 1000)
        
        if workflow_error:
            status = WorkflowStatus.FAILED
        elif context.failed_steps:
            status = WorkflowStatus.PARTIAL
        else:
            status = WorkflowStatus.COMPLETED
        
        return WorkflowResult(
            workflow_id=self.dag.workflow_id,
            document_id=document_id,
            status=status,
            step_results=context.step_results,
            outputs=context.step_outputs,
            started_at=context.started_at,
            completed_at=datetime.utcnow(),
            duration_ms=duration_ms,
            error=workflow_error
        )
    
    async def _execute_workflow(self, context: ExecutionContext):
        """Execute all steps in the workflow."""
        logger.info(f"Starting workflow {self.dag.workflow_id} for document {context.document_id}")
        
        while True:
            # Get steps ready to execute
            ready_steps = self.dag.get_ready_steps(
                context.completed_steps,
                context.failed_steps,
                context.skipped_steps
            )
            
            if not ready_steps:
                # No more steps to execute
                break
            
            # Execute ready steps in parallel
            results = await self._execute_parallel_steps(ready_steps, context)
            
            # Process results
            for step_id, result in results.items():
                context.step_results[step_id] = result
                
                if result.status == StepStatus.COMPLETED:
                    context.mark_completed(step_id, result.output)
                elif result.status == StepStatus.FAILED:
                    context.mark_failed(step_id, result.error)
                    
                    # Handle error propagation
                    step = self.dag.steps[step_id]
                    if step.on_error == ErrorPropagationStrategy.FAIL_FAST:
                        raise RuntimeError(f"Step {step_id} failed: {result.error}")
                    elif step.on_error == ErrorPropagationStrategy.FAIL_BRANCH:
                        # Skip all downstream steps
                        await self._skip_downstream(step_id, context)
                
                elif result.status == StepStatus.SKIPPED:
                    context.mark_skipped(step_id, result.error or "Condition not met")
        
        logger.info(
            f"Workflow {self.dag.workflow_id} completed: "
            f"{len(context.completed_steps)} completed, "
            f"{len(context.failed_steps)} failed, "
            f"{len(context.skipped_steps)} skipped"
        )
    
    async def _execute_parallel_steps(
        self,
        step_ids: Set[str],
        context: ExecutionContext
    ) -> Dict[str, StepResult]:
        """Execute multiple steps in parallel with semaphore control."""
        async def execute_one(step_id: str) -> Tuple[str, StepResult]:
            step = self.dag.steps[step_id]
            
            # Check condition
            if step.condition:
                try:
                    should_run = step.condition(context)
                    if not should_run:
                        return step_id, StepResult(
                            step_id=step_id,
                            status=StepStatus.SKIPPED,
                            error="Condition not met"
                        )
                except Exception as e:
                    logger.warning(f"Condition evaluation failed for {step_id}: {e}")
            
            async with self._step_semaphore:
                result = await self._execute_step_with_retry(step, context)
                return step_id, result
        
        tasks = [execute_one(step_id) for step_id in step_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        result_dict = {}
        for item in results:
            if isinstance(item, Exception):
                logger.error(f"Unexpected error in parallel execution: {item}")
                continue
            step_id, result = item
            result_dict[step_id] = result
        
        return result_dict
    
    async def _execute_step_with_retry(
        self,
        step: WorkflowStep,
        context: ExecutionContext
    ) -> StepResult:
        """Execute a single step with retry logic."""
        config = step.retry_config
        last_error = None
        start_time = datetime.utcnow()
        
        for attempt in range(config.max_attempts):
            try:
                logger.info(f"Executing step {step.step_id} (attempt {attempt + 1}/{config.max_attempts})")
                
                # Apply rate limiting for external API calls
                if step.step_type == StepType.EXTRACT:
                    await self.rate_limiter.acquire()
                
                # Execute with timeout
                output = await asyncio.wait_for(
                    step.handler(context),
                    timeout=step.timeout_seconds
                )
                
                end_time = datetime.utcnow()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)
                
                logger.info(f"Step {step.step_id} completed in {duration_ms}ms")
                
                return StepResult(
                    step_id=step.step_id,
                    status=StepStatus.COMPLETED,
                    output=output,
                    started_at=start_time,
                    completed_at=end_time,
                    duration_ms=duration_ms,
                    attempts=attempt + 1
                )
                
            except asyncio.TimeoutError:
                last_error = f"Timeout after {step.timeout_seconds}s"
                logger.warning(f"Step {step.step_id} timed out (attempt {attempt + 1})")
                
            except config.retryable_exceptions as e:
                last_error = str(e)
                logger.warning(f"Step {step.step_id} failed (retryable): {e}")
                
            except Exception as e:
                # Non-retryable error
                logger.error(f"Step {step.step_id} failed (non-retryable): {e}")
                return StepResult(
                    step_id=step.step_id,
                    status=StepStatus.FAILED,
                    error=str(e),
                    started_at=start_time,
                    completed_at=datetime.utcnow(),
                    attempts=attempt + 1,
                    retryable=False
                )
            
            # Calculate backoff delay
            if attempt < config.max_attempts - 1:
                delay = self._calculate_delay(attempt, config)
                logger.info(f"Retrying step {step.step_id} in {delay:.2f}s")
                await asyncio.sleep(delay)
        
        # All retries exhausted
        return StepResult(
            step_id=step.step_id,
            status=StepStatus.FAILED,
            error=last_error,
            started_at=start_time,
            completed_at=datetime.utcnow(),
            attempts=config.max_attempts,
            retryable=True
        )
    
    def _calculate_delay(self, attempt: int, config: RetryConfig) -> float:
        """Calculate delay with exponential backoff and jitter."""
        delay = min(
            config.base_delay_seconds * (config.exponential_base ** attempt),
            config.max_delay_seconds
        )
        jitter = delay * config.jitter_factor * (2 * random.random() - 1)
        return max(0, delay + jitter)
    
    async def _skip_downstream(self, failed_step_id: str, context: ExecutionContext):
        """Mark all downstream steps as skipped."""
        to_skip = set()
        queue = list(self.dag.get_dependents(failed_step_id))
        
        while queue:
            step_id = queue.pop(0)
            if step_id in to_skip:
                continue
            to_skip.add(step_id)
            queue.extend(self.dag.get_dependents(step_id))
        
        for step_id in to_skip:
            context.mark_skipped(step_id, f"Upstream step {failed_step_id} failed")
            context.step_results[step_id] = StepResult(
                step_id=step_id,
                status=StepStatus.SKIPPED,
                error=f"Skipped due to upstream failure: {failed_step_id}"
            )




class BatchWorkflowExecutor:
    """Executes workflows for multiple documents in parallel."""
    
    def __init__(
        self,
        dag: WorkflowDAG,
        max_concurrent_documents: int = 100,
        max_concurrent_steps_per_document: int = 10,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ):
        self.dag = dag
        self.max_concurrent_documents = max_concurrent_documents
        self.max_concurrent_steps = max_concurrent_steps_per_document
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(rate=50, capacity=100)
        
        self._doc_semaphore = asyncio.Semaphore(max_concurrent_documents)
    
    async def execute_batch(
        self,
        document_ids: List[str],
        initial_contexts: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> List[WorkflowResult]:
        """
        Execute workflow for multiple documents.
        
        Args:
            document_ids: List of document IDs to process
            initial_contexts: Optional map of document_id -> initial context
        
        Returns:
            List of WorkflowResult for each document
        """
        initial_contexts = initial_contexts or {}
        
        async def process_one(doc_id: str) -> WorkflowResult:
            async with self._doc_semaphore:
                executor = WorkflowExecutor(
                    dag=self.dag,
                    max_concurrent_steps=self.max_concurrent_steps,
                    rate_limiter=self.rate_limiter
                )
                return await executor.execute(
                    document_id=doc_id,
                    initial_context=initial_contexts.get(doc_id)
                )
        
        tasks = [process_one(doc_id) for doc_id in document_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to failed results
        processed_results = []
        for doc_id, result in zip(document_ids, results):
            if isinstance(result, Exception):
                processed_results.append(WorkflowResult(
                    workflow_id=self.dag.workflow_id,
                    document_id=doc_id,
                    status=WorkflowStatus.FAILED,
                    step_results={},
                    outputs={},
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                    duration_ms=0,
                    error=str(result)
                ))
            else:
                processed_results.append(result)
        
        return processed_results


# WORKFLOW BUILDER

class WorkflowBuilder:
    """Fluent builder for creating workflow DAGs."""
    
    def __init__(self, workflow_id: str, name: str):
        self.dag = WorkflowDAG(workflow_id, name)
    
    def add_step(
        self,
        step_id: str,
        handler: Callable,
        step_type: StepType = StepType.CUSTOM,
        name: Optional[str] = None,
        dependencies: Optional[Set[str]] = None,
        timeout: float = 30.0,
        retries: int = 3,
        condition: Optional[Callable] = None,
        on_error: ErrorPropagationStrategy = ErrorPropagationStrategy.FAIL_BRANCH,
        **config
    ) -> "WorkflowBuilder":
        """Add a step to the workflow."""
        step = WorkflowStep(
            step_id=step_id,
            step_type=step_type,
            name=name or step_id,
            handler=handler,
            config=config,
            timeout_seconds=timeout,
            retry_config=RetryConfig(max_attempts=retries),
            dependencies=dependencies or set(),
            condition=condition,
            on_error=on_error
        )
        self.dag.add_step(step)
        return self
    
    def build(self) -> WorkflowDAG:
        """Build and validate the workflow DAG."""
        errors = DAGValidator().validate(self.dag)
        if errors:
            error_msgs = [f"{e.code}: {e.message}" for e in errors]
            raise ValueError(f"Invalid workflow: {error_msgs}")
        return self.dag


# EXAMPLE HANDLERS

async def ingest_handler(context: ExecutionContext) -> Dict[str, Any]:
    """Example ingest handler."""
    logger.info(f"Ingesting document {context.document_id}")
    await asyncio.sleep(0.1)  # Simulate work
    return {
        "document_id": context.document_id,
        "ingested_at": datetime.utcnow().isoformat()
    }


async def extract_handler(context: ExecutionContext) -> Dict[str, Any]:
    """Example extraction handler."""
    logger.info(f"Extracting from document {context.document_id}")
    await asyncio.sleep(0.5)  # Simulate LLM call
    return {
        "fields": {
            "invoice_number": "INV-001",
            "total": 1234.56
        },
        "confidence": 0.92
    }


async def validate_handler(context: ExecutionContext) -> Dict[str, Any]:
    """Example validation handler."""
    logger.info(f"Validating extraction for {context.document_id}")
    extraction = context.step_outputs.get("extract", {})
    await asyncio.sleep(0.1)
    return {
        "valid": True,
        "errors": []
    }


async def output_json_handler(context: ExecutionContext) -> Dict[str, Any]:
    """Example JSON output handler."""
    logger.info(f"Generating JSON output for {context.document_id}")
    await asyncio.sleep(0.1)
    return {
        "output_path": f"/output/{context.document_id}.json"
    }


async def output_parquet_handler(context: ExecutionContext) -> Dict[str, Any]:
    """Example Parquet output handler."""
    logger.info(f"Generating Parquet output for {context.document_id}")
    await asyncio.sleep(0.1)
    return {
        "output_path": f"/output/{context.document_id}.parquet"
    }


# MAIN

def create_document_workflow() -> WorkflowDAG:
    """Create the standard document processing workflow."""
    return (
        WorkflowBuilder("doc_processing_v1", "Document Processing Workflow")
        .add_step(
            step_id="ingest",
            handler=ingest_handler,
            step_type=StepType.INGEST,
            name="Document Ingestion",
            timeout=10.0
        )
        .add_step(
            step_id="extract",
            handler=extract_handler,
            step_type=StepType.EXTRACT,
            name="LLM Extraction",
            dependencies={"ingest"},
            timeout=30.0,
            retries=3
        )
        .add_step(
            step_id="validate",
            handler=validate_handler,
            step_type=StepType.VALIDATE,
            name="Field Validation",
            dependencies={"extract"},
            timeout=10.0
        )
        .add_step(
            step_id="output_json",
            handler=output_json_handler,
            step_type=StepType.OUTPUT,
            name="JSON Output",
            dependencies={"validate"},
            timeout=10.0
        )
        .add_step(
            step_id="output_parquet",
            handler=output_parquet_handler,
            step_type=StepType.OUTPUT,
            name="Parquet Output",
            dependencies={"validate"},
            timeout=10.0
        )
        .build()
    )


async def main():
    """Test the workflow executor."""
    # Create workflow
    dag = create_document_workflow()
    
    print(f"Workflow: {dag.name}")
    print(f"Steps: {list(dag.steps.keys())}")
    print(f"Entry points: {dag.entry_points}")
    print(f"Terminal points: {dag.terminal_points}")
    print()
    
    # Execute for single document
    executor = WorkflowExecutor(dag)
    result = await executor.execute("doc_001")
    
    print(f"Status: {result.status.value}")
    print(f"Duration: {result.duration_ms}ms")
    print()
    print("Step Results:")
    for step_id, step_result in result.step_results.items():
        print(f"  {step_id}: {step_result.status.value} ({step_result.duration_ms}ms)")
    
    print()
    
    # Execute batch
    print("Executing batch of 5 documents...")
    batch_executor = BatchWorkflowExecutor(dag, max_concurrent_documents=5)
    batch_results = await batch_executor.execute_batch(
        [f"doc_{i:03d}" for i in range(5)]
    )
    
    print(f"Batch completed: {len([r for r in batch_results if r.status == WorkflowStatus.COMPLETED])}/5 successful")
    
    total_time = sum(r.duration_ms for r in batch_results)
    print(f"Total processing time: {total_time}ms")


if __name__ == "__main__":
    asyncio.run(main())
