# Workflow Engine Design

## Executive Summary

This document describes the architecture for a workflow orchestration system designed to process documents through configurable DAG-based pipelines with parallel execution, rate limiting, and robust failure handling.

---

## 1. DAG Representation

### 1.1 Graph Structure

The workflow is represented as a Directed Acyclic Graph (DAG) where:
- **Nodes** represent processing steps (extraction, validation, output generation, etc.)
- **Edges** represent dependencies between steps
- **Metadata** contains execution configuration for each step

```python
@dataclass
class WorkflowStep:
    """A single step in the workflow DAG."""
    step_id: str
    step_type: StepType              # EXTRACT, VALIDATE, TRANSFORM, OUTPUT, REVIEW
    name: str                         # Human-readable name
    handler: Callable                 # Function to execute
    config: Dict[str, Any]            # Step-specific configuration
    timeout_seconds: int = 30         # Max execution time
    retry_config: RetryConfig         # Retry behavior
    dependencies: Set[str]            # Step IDs that must complete first
    condition: Optional[Callable]     # Predicate for conditional execution

@dataclass
class WorkflowDAG:
    """DAG representation of a document processing workflow."""
    workflow_id: str
    name: str
    steps: Dict[str, WorkflowStep]    # step_id -> WorkflowStep
    entry_points: Set[str]            # Steps with no dependencies
    terminal_points: Set[str]         # Steps with no dependents
```

### 1.2 Graph Visualization

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │                   DOCUMENT WORKFLOW DAG                      │
                    └─────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │     INGEST      │
                                    │   (entry point) │
                                    └─────────────────┘
                                              │
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                    ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
                    │  OCR_TEXT   │   │ PARSE_META  │   │CHECK_CACHE  │
                    └─────────────┘   └─────────────┘   └─────────────┘
                              │               │               │
                              └───────────────┼───────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │  LLM_EXTRACT    │
                                    │  (rate limited) │
                                    └─────────────────┘
                                              │
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                    ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
                    │  VALIDATE   │   │FIELD_ENRICH │   │ CONFIDENCE  │
                    └─────────────┘   └─────────────┘   └─────────────┘
                              │               │               │
                              └───────────────┼───────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │  MERGE_LOCKS    │
                                    └─────────────────┘
                                              │
                      ┌───────────────────────┼───────────────────────┐
                      ▼                       │                       ▼
            ┌─────────────────┐               │             ┌─────────────────┐
            │ ROUTE_TO_REVIEW │◀──────────────┤             │  AUTO_APPROVE   │
            │  (conditional)  │               │             │  (conditional)  │
            └─────────────────┘               │             └─────────────────┘
                      │                       │                       │
                      ▼                       ▼                       ▼
            ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
            │  REVIEW_QUEUE   │     │ OUTPUT_PARQUET  │     │  OUTPUT_JSON    │
            └─────────────────┘     └─────────────────┘     └─────────────────┘
                                              │                       │
                                              └───────────┬───────────┘
                                                          │
                                                          ▼
                                                ┌─────────────────┐
                                                │   AUDIT_LOG     │
                                                │(terminal point) │
                                                └─────────────────┘
```

### 1.3 Cycle Detection Algorithm

Cycle detection is performed during DAG construction using depth-first search (DFS):

```python
class CycleDetector:
    """Detects cycles in the workflow DAG."""
    
    def detect_cycles(self, dag: WorkflowDAG) -> List[List[str]]:
        """
        Detect all cycles in the DAG using Tarjan's algorithm.
        Returns list of cycles (each cycle is a list of step IDs).
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {step_id: WHITE for step_id in dag.steps}
        parent = {step_id: None for step_id in dag.steps}
        cycles = []
        
        def dfs(node: str):
            color[node] = GRAY
            
            for dependent in self._get_dependents(dag, node):
                if color[dependent] == GRAY:
                    # Back edge found - cycle detected
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
```

### 1.4 Schema Validation

```python
class DAGValidator:
    """Validates workflow DAG structure and configuration."""
    
    def validate(self, dag: WorkflowDAG) -> List[ValidationError]:
        """Run all validation checks on the DAG."""
        errors = []
        
        # Check for cycles
        cycles = CycleDetector().detect_cycles(dag)
        if cycles:
            errors.append(ValidationError(
                "CYCLE_DETECTED",
                f"Workflow contains cycles: {cycles}"
            ))
        
        # Check for orphan steps (no path to entry)
        reachable = self._compute_reachable_from_entries(dag)
        orphans = set(dag.steps.keys()) - reachable
        if orphans:
            errors.append(ValidationError(
                "ORPHAN_STEPS",
                f"Steps not reachable from entry: {orphans}"
            ))
        
        # Check for dangling dependencies
        for step in dag.steps.values():
            for dep in step.dependencies:
                if dep not in dag.steps:
                    errors.append(ValidationError(
                        "UNKNOWN_DEPENDENCY",
                        f"Step {step.step_id} depends on unknown step {dep}"
                    ))
        
        # Check timeout configuration
        for step in dag.steps.values():
            if step.timeout_seconds <= 0:
                errors.append(ValidationError(
                    "INVALID_TIMEOUT",
                    f"Step {step.step_id} has invalid timeout: {step.timeout_seconds}"
                ))
        
        return errors
```

---

## 2. Parallelism Model

### 2.1 Document-Level Parallelism

Multiple documents are processed concurrently using a worker pool:

```python
class DocumentParallelExecutor:
    """Executes workflows for multiple documents in parallel."""
    
    def __init__(
        self,
        max_concurrent_documents: int = 100,
        max_concurrent_steps_per_document: int = 10,
    ):
        self._doc_semaphore = asyncio.Semaphore(max_concurrent_documents)
        self._step_semaphore = asyncio.Semaphore(
            max_concurrent_documents * max_concurrent_steps_per_document
        )
    
    async def process_batch(
        self, 
        documents: List[DocumentInput],
        workflow: WorkflowDAG
    ) -> List[WorkflowResult]:
        """Process multiple documents with bounded concurrency."""
        tasks = [
            self._process_with_limit(doc, workflow) 
            for doc in documents
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _process_with_limit(
        self, 
        document: DocumentInput,
        workflow: WorkflowDAG
    ) -> WorkflowResult:
        """Process single document with semaphore control."""
        async with self._doc_semaphore:
            executor = WorkflowExecutor(workflow)
            return await executor.execute(document)
```

### 2.2 Step-Level Parallelism

Within a single document's workflow, independent steps execute in parallel:

```python
class StepParallelExecutor:
    """Executes independent workflow steps in parallel."""
    
    async def execute_parallel_steps(
        self,
        ready_steps: List[WorkflowStep],
        context: ExecutionContext,
        semaphore: asyncio.Semaphore
    ) -> Dict[str, StepResult]:
        """Execute all ready steps concurrently."""
        
        async def execute_one(step: WorkflowStep) -> Tuple[str, StepResult]:
            async with semaphore:
                result = await self._execute_step(step, context)
                return step.step_id, result
        
        tasks = [execute_one(step) for step in ready_steps]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return {step_id: result for step_id, result in results}
```

### 2.3 Rate Limiting

Rate limiting protects external services (especially LLM APIs) from being overwhelmed:

```python
class TokenBucketRateLimiter:
    """Token bucket rate limiter for API calls."""
    
    def __init__(
        self,
        rate: float,          # Tokens per second
        capacity: float,      # Maximum burst capacity
    ):
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
                
                # Calculate wait time
                needed = tokens - self._tokens
                wait_time = needed / self.rate
                await asyncio.sleep(wait_time)
                waited += wait_time
```

### 2.4 Concurrency Control Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONCURRENCY CONTROL                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Document Queue                                                             │
│   ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐            │
│   │Doc 1│Doc 2│Doc 3│Doc 4│Doc 5│Doc 6│Doc 7│Doc 8│Doc 9│...  │            │
│   └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘            │
│                              │                                               │
│                              ▼                                               │
│   ┌──────────────────────────────────────────────────────────┐              │
│   │           Document Semaphore (max_concurrent=100)         │              │
│   └──────────────────────────────────────────────────────────┘              │
│                              │                                               │
│          ┌───────────────────┼───────────────────┐                          │
│          ▼                   ▼                   ▼                          │
│   ┌────────────┐      ┌────────────┐      ┌────────────┐                   │
│   │  Worker 1  │      │  Worker 2  │      │  Worker N  │                   │
│   │            │      │            │      │            │                   │
│   │ ┌────────┐ │      │ ┌────────┐ │      │ ┌────────┐ │                   │
│   │ │Step Sem│ │      │ │Step Sem│ │      │ │Step Sem│ │                   │
│   │ │ (10)   │ │      │ │ (10)   │ │      │ │ (10)   │ │                   │
│   │ └────────┘ │      │ └────────┘ │      │ └────────┘ │                   │
│   └────────────┘      └────────────┘      └────────────┘                   │
│                              │                                               │
│                              ▼                                               │
│   ┌──────────────────────────────────────────────────────────┐              │
│   │            Rate Limiter (LLM API: 50 req/sec)            │              │
│   └──────────────────────────────────────────────────────────┘              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Execution Semantics

### 3.1 Fan-Out Pattern

Fan-out enables parallel execution of independent processing branches:

```python
async def execute_fanout(
    self,
    source_step: str,
    target_steps: List[str],
    context: ExecutionContext
) -> Dict[str, StepResult]:
    """
    Execute multiple downstream steps from a single source.
    All target steps receive the same input from source.
    """
    source_output = context.step_outputs[source_step]
    
    async def execute_branch(step_id: str) -> Tuple[str, StepResult]:
        step = self.dag.steps[step_id]
        result = await self._execute_step(step, context)
        return step_id, result
    
    tasks = [execute_branch(step_id) for step_id in target_steps]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return dict(results)
```

### 3.2 Fan-In/Join Pattern

Fan-in collects results from multiple parallel branches before proceeding:

```python
class BarrierJoin:
    """Waits for all upstream steps to complete before proceeding."""
    
    def __init__(self, required_steps: Set[str]):
        self.required_steps = required_steps
        self._completed: Set[str] = set()
        self._results: Dict[str, StepResult] = {}
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()
    
    async def signal_completion(self, step_id: str, result: StepResult):
        """Signal that an upstream step has completed."""
        async with self._lock:
            self._completed.add(step_id)
            self._results[step_id] = result
            
            if self._completed >= self.required_steps:
                self._event.set()
    
    async def wait(self, timeout: float = None) -> Dict[str, StepResult]:
        """Wait for all required steps to complete."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
            return self._results
        except asyncio.TimeoutError:
            raise JoinTimeoutError(
                f"Timeout waiting for steps: {self.required_steps - self._completed}"
            )
```

### 3.3 Conditional Routing

Steps can be conditionally executed based on predicates:

```python
class ConditionalRouter:
    """Routes execution based on conditions."""
    
    async def evaluate_routes(
        self,
        context: ExecutionContext,
        routes: List[ConditionalRoute]
    ) -> List[str]:
        """
        Evaluate conditions and return list of step IDs to execute.
        Routes are evaluated in order; first matching route wins (exclusive)
        or all matching routes execute (inclusive).
        """
        matching_steps = []
        
        for route in routes:
            try:
                if await route.condition(context):
                    matching_steps.append(route.target_step)
                    if route.exclusive:
                        break
            except Exception as e:
                logger.error(f"Condition evaluation failed: {e}")
                if route.on_error == "skip":
                    continue
                elif route.on_error == "fail":
                    raise
        
        return matching_steps


# Example conditions
def needs_review_condition(context: ExecutionContext) -> bool:
    """Route to review if confidence is low."""
    confidence = context.step_outputs.get("confidence_check", {}).get("score", 0)
    return confidence < 0.75

def auto_approve_condition(context: ExecutionContext) -> bool:
    """Auto-approve if all validations pass and confidence is high."""
    validation_passed = context.step_outputs.get("validate", {}).get("passed", False)
    confidence = context.step_outputs.get("confidence_check", {}).get("score", 0)
    return validation_passed and confidence >= 0.95
```

### 3.4 Execution Flow Diagram

```
                         EXECUTION SEMANTICS
                         
     ┌─────────────────────────────────────────────────────────┐
     │                                                          │
     │   1. TOPOLOGICAL SORT                                   │
     │      ┌───┐    ┌───┐    ┌───┐    ┌───┐    ┌───┐         │
     │      │ A │───▶│ B │───▶│ D │───▶│ E │───▶│ F │         │
     │      └───┘    └───┘    └───┘    └───┘    └───┘         │
     │                 │                  ▲                     │
     │                 │        ┌───┐     │                     │
     │                 └───────▶│ C │─────┘                     │
     │                          └───┘                           │
     │                                                          │
     └─────────────────────────────────────────────────────────┘
                                │
                                ▼
     ┌─────────────────────────────────────────────────────────┐
     │                                                          │
     │   2. PARALLEL EXECUTION BY LEVEL                        │
     │                                                          │
     │   Level 0: [A]          ──▶ Execute A                   │
     │   Level 1: [B]          ──▶ Execute B                   │
     │   Level 2: [C, D]       ──▶ Execute C, D in parallel    │
     │   Level 3: [E]          ──▶ Wait for barrier, Execute E │
     │   Level 4: [F]          ──▶ Execute F                   │
     │                                                          │
     └─────────────────────────────────────────────────────────┘
                                │
                                ▼
     ┌─────────────────────────────────────────────────────────┐
     │                                                          │
     │   3. CONDITIONAL BRANCHING                              │
     │                                                          │
     │                    ┌───────────┐                        │
     │                    │  VALIDATE │                        │
     │                    └─────┬─────┘                        │
     │                          │                               │
     │              ┌───────────┼───────────┐                  │
     │              ▼           ▼           ▼                  │
     │         ┌────────┐  ┌────────┐  ┌────────┐             │
     │         │ REVIEW │  │  SKIP  │  │APPROVE │             │
     │         │conf<75%│  │ error  │  │conf>95%│             │
     │         └────────┘  └────────┘  └────────┘             │
     │                                                          │
     └─────────────────────────────────────────────────────────┘
```

---

## 4. Failure Handling

### 4.1 Step-Level Retry

Each step has configurable retry behavior:

```python
@dataclass
class StepRetryConfig:
    """Retry configuration for a workflow step."""
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.1
    retryable_errors: Set[Type[Exception]] = field(default_factory=lambda: {
        TimeoutError,
        ConnectionError,
        RateLimitError,
    })

async def execute_with_retry(
    self,
    step: WorkflowStep,
    context: ExecutionContext
) -> StepResult:
    """Execute step with retry logic."""
    config = step.retry_config
    last_error = None
    
    for attempt in range(config.max_attempts):
        try:
            return await asyncio.wait_for(
                step.handler(context),
                timeout=step.timeout_seconds
            )
        except tuple(config.retryable_errors) as e:
            last_error = e
            if attempt < config.max_attempts - 1:
                delay = calculate_backoff_delay(attempt, config)
                logger.warning(
                    f"Step {step.step_id} attempt {attempt + 1} failed, "
                    f"retrying in {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
        except asyncio.TimeoutError:
            last_error = TimeoutError(f"Step timed out after {step.timeout_seconds}s")
            # Timeout is retryable
            if attempt < config.max_attempts - 1:
                delay = calculate_backoff_delay(attempt, config)
                await asyncio.sleep(delay)
        except Exception as e:
            # Non-retryable error
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                error=str(e),
                retryable=False
            )
    
    return StepResult(
        step_id=step.step_id,
        status=StepStatus.FAILED,
        error=str(last_error),
        attempts=config.max_attempts,
        retryable=True
    )
```

### 4.2 Workflow Timeout

Entire workflow has a maximum execution time:

```python
class WorkflowTimeoutManager:
    """Manages workflow-level timeouts."""
    
    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self._start_time: Optional[float] = None
        self._cancelled = False
    
    def start(self):
        """Start the timeout timer."""
        self._start_time = time.monotonic()
    
    def remaining(self) -> float:
        """Get remaining time before timeout."""
        if self._start_time is None:
            return self.timeout_seconds
        elapsed = time.monotonic() - self._start_time
        return max(0, self.timeout_seconds - elapsed)
    
    def is_expired(self) -> bool:
        """Check if timeout has expired."""
        return self.remaining() <= 0
    
    async def run_with_timeout(
        self, 
        coro: Coroutine
    ) -> Any:
        """Run coroutine with remaining timeout."""
        remaining = self.remaining()
        if remaining <= 0:
            raise WorkflowTimeoutError("Workflow timeout expired")
        
        try:
            return await asyncio.wait_for(coro, timeout=remaining)
        except asyncio.TimeoutError:
            raise WorkflowTimeoutError(
                f"Workflow timed out after {self.timeout_seconds}s"
            )
```

### 4.3 Partial Completion Handling

When a workflow fails, we track and preserve partial results:

```python
@dataclass
class PartialWorkflowResult:
    """Result of a partially completed workflow."""
    workflow_id: str
    document_id: str
    status: WorkflowStatus  # COMPLETED | PARTIAL | FAILED
    completed_steps: Dict[str, StepResult]
    failed_steps: Dict[str, StepResult]
    pending_steps: Set[str]
    error: Optional[str] = None
    recovery_point: Optional[str] = None  # Step to resume from

class PartialCompletionHandler:
    """Handles partial workflow completion for recovery."""
    
    async def save_checkpoint(
        self,
        workflow_id: str,
        document_id: str,
        completed_steps: Dict[str, StepResult],
        context: ExecutionContext
    ):
        """Save checkpoint for potential recovery."""
        checkpoint = WorkflowCheckpoint(
            workflow_id=workflow_id,
            document_id=document_id,
            completed_steps=set(completed_steps.keys()),
            step_outputs={k: v.output for k, v in completed_steps.items()},
            saved_at=datetime.utcnow()
        )
        await self._store.save(checkpoint)
    
    async def resume_from_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        dag: WorkflowDAG
    ) -> ExecutionContext:
        """Resume workflow execution from checkpoint."""
        context = ExecutionContext(
            document_id=checkpoint.document_id,
            step_outputs=checkpoint.step_outputs,
            completed_steps=checkpoint.completed_steps,
            resumed_from_checkpoint=True
        )
        return context
```

### 4.4 Failure Recovery Matrix

| Failure Type | Retry? | Checkpoint? | Recovery Action |
|--------------|--------|-------------|-----------------|
| Step timeout | Yes | Yes | Retry with backoff |
| LLM rate limit | Yes | No | Wait for retry-after |
| LLM server error | Yes | Yes | Retry with circuit breaker |
| Validation failure | No | Yes | Route to review queue |
| Invalid document | No | Yes | Mark failed, notify |
| Workflow timeout | No | Yes | Save checkpoint, alert |

### 4.5 Error Propagation Strategy

```python
class ErrorPropagationStrategy(Enum):
    """How errors propagate through the workflow."""
    FAIL_FAST = "fail_fast"           # Stop workflow on first error
    CONTINUE = "continue"              # Continue with other branches
    FAIL_BRANCH = "fail_branch"        # Fail only the affected branch

class ErrorHandler:
    """Handles error propagation based on strategy."""
    
    async def handle_step_error(
        self,
        step: WorkflowStep,
        error: Exception,
        strategy: ErrorPropagationStrategy,
        context: ExecutionContext
    ) -> ErrorAction:
        """Determine action based on error and strategy."""
        
        if strategy == ErrorPropagationStrategy.FAIL_FAST:
            return ErrorAction.ABORT_WORKFLOW
        
        elif strategy == ErrorPropagationStrategy.FAIL_BRANCH:
            # Mark all downstream steps as skipped
            downstream = self._get_all_downstream(step.step_id)
            for step_id in downstream:
                context.mark_skipped(step_id, f"Upstream failure: {step.step_id}")
            return ErrorAction.CONTINUE_OTHER_BRANCHES
        
        else:  # CONTINUE
            return ErrorAction.CONTINUE_ALL
```

---

## 5. Performance Characteristics

### 5.1 Throughput Targets

| Metric | Target | Design Rationale |
|--------|--------|------------------|
| Documents/hour | 5,000 | 100 concurrent workers × 50 docs/worker/hour |
| P95 latency | < 30s | Step parallelism + rate limiting |
| Batch (100 docs) | < 5 min | Parallel document processing |
| Memory per doc | < 50 MB | Output streaming, lazy evaluation |

### 5.2 Scalability Considerations

- **Horizontal scaling**: Stateless workers behind load balancer
- **Vertical scaling**: Increase max_concurrent per worker
- **Rate limiting**: Shared rate limiter via Redis for distributed deployment
- **Backpressure**: Queue depth monitoring with admission control

---

## 6. Monitoring Integration

### 6.1 Metrics Emitted

```python
class WorkflowMetrics:
    """Metrics emitted during workflow execution."""
    
    # Counters
    workflow_started_total: Counter
    workflow_completed_total: Counter
    workflow_failed_total: Counter
    step_executed_total: Counter
    step_retried_total: Counter
    
    # Gauges
    active_workflows: Gauge
    pending_steps: Gauge
    rate_limiter_queue_depth: Gauge
    
    # Histograms
    workflow_duration_seconds: Histogram
    step_duration_seconds: Histogram
    retry_delay_seconds: Histogram
```

### 6.2 Tracing

Each workflow execution generates distributed traces:

```python
async def execute_with_tracing(
    self,
    step: WorkflowStep,
    context: ExecutionContext
) -> StepResult:
    """Execute step with distributed tracing."""
    with tracer.start_span(
        f"workflow.step.{step.step_type.value}",
        attributes={
            "step.id": step.step_id,
            "step.name": step.name,
            "document.id": context.document_id,
            "workflow.id": self.dag.workflow_id,
        }
    ) as span:
        try:
            result = await self._execute_step(step, context)
            span.set_attribute("step.status", result.status.value)
            return result
        except Exception as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise
```
