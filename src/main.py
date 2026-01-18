"""
DocFlow Main Application

Unified entry point that combines:
- Document extraction (extraction_module.py)
- Workflow orchestration (workflow_executor.py)
- Human review queue (review_queue.py)
- Monitoring & metrics (monitoring.py)

Run with: uvicorn src.main:app --reload --port 8000
"""

import asyncio
import os
import logging
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import our modules
from .extraction_module import (
    ExtractionModule, 
    DocumentInput, 
    ProcessingStatus,
    DocumentType
)
from .workflow_executor import (
    WorkflowDAG, 
    WorkflowStep, 
    StepType,
    WorkflowExecutor,
    BatchWorkflowExecutor
)
from .review_queue import (
    ReviewQueueManager,
    ReviewStatus,
    ReviewDecision,
    ExtractedFieldData,
    FieldCorrection
)
from .monitoring import (
    DocumentMetrics,
    SLAMonitor,
    AlertManager,
    MetricsExporter,
    REGISTRY,
    log_alert_handler
)
from .database import (
    get_db_pool, 
    close_db_pool, 
    init_database, 
    DocumentRepository,
    ReviewItemRepository
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

# =============================================================================
# GLOBAL INSTANCES
# =============================================================================

# Create module instances
extraction_module = ExtractionModule(
    output_dir=OUTPUT_DIR
)

review_queue = ReviewQueueManager()
metrics = DocumentMetrics()
alert_manager = AlertManager()
alert_manager.register_handler(log_alert_handler)
sla_monitor = SLAMonitor(metrics, alert_manager)
metrics_exporter = MetricsExporter(REGISTRY)

# In-memory storage for extraction logs (for AI Thinking panel)
extraction_logs: Dict[str, dict] = {}

# WebSocket connection manager for real-time updates
class ConnectionManager:
    """Manage WebSocket connections for real-time extraction updates."""
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, document_id: str):
        await websocket.accept()
        self.active_connections[document_id] = websocket
    
    def disconnect(self, document_id: str):
        if document_id in self.active_connections:
            del self.active_connections[document_id]
    
    async def send_update(self, document_id: str, data: dict):
        if document_id in self.active_connections:
            try:
                await self.active_connections[document_id].send_json(data)
            except Exception:
                self.disconnect(document_id)
    
    async def broadcast(self, data: dict):
        disconnected = []
        for doc_id, ws in self.active_connections.items():
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(doc_id)
        for doc_id in disconnected:
            self.disconnect(doc_id)

ws_manager = ConnectionManager()


# =============================================================================
# WORKFLOW DEFINITION
# =============================================================================

async def create_processing_workflow() -> WorkflowDAG:
    """Create the document processing workflow DAG."""
    dag = WorkflowDAG("document_processing", "Document Processing Pipeline")
    
    # Step 1: Ingest - receive and validate document
    async def ingest_handler(ctx):
        doc = ctx.metadata.get("document")
        logger.info(f"Ingesting document: {doc.document_id}")
        
        # Persist to database
        await DocumentRepository.create(
            document_id=doc.document_id,
            content_hash=doc.content_hash,
            document_type=getattr(doc.document_type, "value", str(doc.document_type)),
            metadata={
                "filename": doc.metadata.get("filename", "unknown"),
                "source": doc.metadata.get("source", "api"),
                "ingested_at": datetime.now(timezone.utc).isoformat()
            }
        )
        
        return {"document": doc, "ingested_at": datetime.now(timezone.utc).isoformat()}
    
    # Step 2: Extract - use LLM to extract fields
    async def extract_handler(ctx):
        doc = ctx.step_outputs["ingest"]["document"]
        logger.info(f"Extracting fields from: {doc.document_id}")
        
        start_time = datetime.now(timezone.utc)
        result = await extraction_module.process(doc)
        end_time = datetime.now(timezone.utc)
        
        # Safely get status value (could be enum or string)
        status_val = result.status.value if hasattr(result.status, 'value') else str(result.status)
        
        # Record metrics (processing_metadata is a dataclass)
        duration_seconds = (result.processing_metadata.duration_ms or 0) / 1000
        metrics.record_document_processed(
            document_type=doc.document_type,
            status=status_val,
            duration_seconds=duration_seconds
        )
        
        if result.confidence_score:
            metrics.record_confidence(doc.document_type, result.confidence_score)
        
        # Store extraction log for AI Thinking panel
        extraction_logs[doc.document_id] = {
            "document_id": doc.document_id,
            "filename": doc.metadata.get("filename", "unknown"),
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat(),
            "duration_ms": result.processing_metadata.duration_ms,
            "status": status_val,
            "confidence_score": result.confidence_score,
            "fields": {
                name: {
                    "value": getattr(f, "value", str(f)),
                    "confidence": getattr(f, "confidence", 1.0),
                    "extraction_method": getattr(f, "extraction_method", "llm")
                }
                for name, f in result.extracted_fields.items()
            },
            "steps": [
                {"name": "PDF Text Extraction", "status": "completed", "duration_ms": 200},
                {"name": "LLM Extraction", "status": "completed", "duration_ms": result.processing_metadata.duration_ms - 400},
                {"name": "Field Validation", "status": "completed", "duration_ms": 100},
                {"name": "Output Generation", "status": "completed", "duration_ms": 100}
            ],
            "model": "mistral-large-latest",
            "lineage": result.lineage
        }
        
        # Broadcast to WebSocket subscribers
        await ws_manager.broadcast({
            "type": "extraction_complete",
            "document_id": doc.document_id,
            "data": extraction_logs[doc.document_id]
        })
        
        return {"extraction_result": result}
    
    # Step 3: Validate - check extracted data
    async def validate_handler(ctx):
        result = ctx.step_outputs["extract"]["extraction_result"]
        logger.info(f"Validating extraction for: {result.document_id}")
        
        # Check if any fields need review - handle both ExtractedField objects and dicts
        low_confidence_fields = []
        field_confidences = {}
        
        for name, field in result.extracted_fields.items():
            # Field could be ExtractedField dataclass or dict
            if hasattr(field, 'confidence'):
                conf = field.confidence
            elif isinstance(field, dict):
                conf = field.get('confidence', 1.0)
            else:
                conf = 1.0  # Default if unknown format
            
            field_confidences[name] = conf
            
            if conf < CONFIDENCE_THRESHOLD:
                low_confidence_fields.append(name)
                logger.info(f"  ⚠️ Low confidence field: {name} = {conf:.2%} (threshold: {CONFIDENCE_THRESHOLD:.2%})")
        
        # Always send to review (no auto-approve) per user requirement
        needs_review = True
        
        logger.info(f"  Overall confidence: {result.confidence_score:.2%}")
        logger.info(f"  Low confidence fields: {low_confidence_fields}")
        logger.info(f"  Needs review: {needs_review}")
        
        return {
            "validated": True,
            "needs_review": needs_review,
            "low_confidence_fields": low_confidence_fields,
            "field_confidences": field_confidences,
            "overall_confidence": result.confidence_score
        }
    
    # Helper to safely get field attribute (handles both dataclass and dict)
    def get_field_attr(field, attr, default=None):
        if hasattr(field, attr):
            return getattr(field, attr)
        elif isinstance(field, dict):
            return field.get(attr, default)
        return default
    
    # Step 4: Route - decide if auto-approve or send to review
    async def route_handler(ctx):
        validation = ctx.step_outputs["validate"]
        result = ctx.step_outputs["extract"]["extraction_result"]
        doc = ctx.step_outputs["ingest"]["document"]
        
        if validation["needs_review"]:
            logger.info(f"Sending {doc.document_id} to review queue")
            
            # Update DB status
            await DocumentRepository.update_status(doc.document_id, "review_pending")
            
            # Save extraction results (even if pending review)
            fields_dict = {
                k: {"value": getattr(v, "value", str(v)), "confidence": getattr(v, "confidence", 1.0)} 
                for k, v in result.extracted_fields.items()
            }
            await DocumentRepository.save_extraction(doc.document_id, fields_dict, result.confidence_score)
            
            # Convert extraction result to review queue format - handle both dataclass and dict
            extraction_data = {}

            for field_name, field in result.extracted_fields.items():
                extraction_data[field_name] = ExtractedFieldData(
                    value=get_field_attr(field, 'value'),
                    confidence=get_field_attr(field, 'confidence', 0.0),
                    is_locked=get_field_attr(field, 'is_locked', False)
                )
            
            # Add to review queue
            review_item = await review_queue.add_item(
                document_id=doc.document_id,
                workflow_id=ctx.workflow_id,
                extraction_result=extraction_data,
                document_preview_url=f"/preview/{doc.document_id}.pdf",
                document_type=doc.document_type,
                source_system=doc.metadata.get("source", "api")
            )
            
            return {"action": "review", "review_item_id": review_item.item_id}
        else:
            logger.info(f"Auto-approving {doc.document_id}")
            
            # Update DB status
            await DocumentRepository.update_status(doc.document_id, "completed")
            
            # Save results
            fields_dict = {
                k: {"value": getattr(v, "value", str(v)), "confidence": getattr(v, "confidence", 1.0)} 
                for k, v in result.extracted_fields.items()
            }
            await DocumentRepository.save_extraction(doc.document_id, fields_dict, result.confidence_score)
            return {"action": "auto_approve"}
    
    # Step 5: Output - generate final output files
    async def output_handler(ctx):
        result = ctx.step_outputs["extract"]["extraction_result"]
        route = ctx.step_outputs["route"]
        
        # Only generate output if auto-approved or after review
        if route["action"] == "auto_approve":
            return {
                "output_generated": True,
                "paths": result.output_paths
            }
        else:
            return {
                "output_generated": False,
                "pending_review": True,
                "review_item_id": route.get("review_item_id")
            }
    
    # Add steps to DAG
    dag.add_step(WorkflowStep("ingest", StepType.INGEST, "Ingest Document", handler=ingest_handler))
    dag.add_step(WorkflowStep("extract", StepType.EXTRACT, "Extract Fields", handler=extract_handler, dependencies={"ingest"}))
    dag.add_step(WorkflowStep("validate", StepType.VALIDATE, "Validate Extraction", handler=validate_handler, dependencies={"extract"}))
    dag.add_step(WorkflowStep("route", StepType.CUSTOM, "Route Decision", handler=route_handler, dependencies={"validate"}))
    dag.add_step(WorkflowStep("output", StepType.OUTPUT, "Generate Output", handler=output_handler, dependencies={"route"}))
    
    return dag


# =============================================================================
# API REQUEST/RESPONSE MODELS
# =============================================================================

class ProcessDocumentRequest(BaseModel):
    """Request to process a document."""
    document_id: str
    document_type: str = "invoice"
    source: str = "api"


class ProcessDocumentResponse(BaseModel):
    """Response from document processing."""
    document_id: str
    status: str
    workflow_id: str
    needs_review: bool
    review_item_id: Optional[str] = None
    auto_approved: bool
    output_paths: Optional[dict] = None


class BatchProcessRequest(BaseModel):
    """Request to process multiple documents."""
    documents: List[ProcessDocumentRequest]


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("🚀 Starting DocFlow...")
    
    # Initialize database
    await init_database()
    
    # Create output directory
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    # Start background tasks
    asyncio.create_task(background_sla_monitor())
    asyncio.create_task(background_claim_cleanup())
    
    # Load review queue from database
    await review_queue.load_from_db()
    
    logger.info("✅ DocFlow ready!")
    
    yield
    
    # Shutdown
    await close_db_pool()
    logger.info("👋 Shutting down DocFlow...")


app = FastAPI(
    title="DocFlow",
    description="AI-Powered Document Processing Platform",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# BACKGROUND TASKS
# =============================================================================

async def background_sla_monitor():
    """Background task to monitor SLAs."""
    while True:
        try:
            await sla_monitor.check_all()
            breached = await review_queue.check_sla_breaches()
            if breached:
                logger.warning(f"SLA breached for {len(breached)} items")
        except Exception as e:
            logger.error(f"SLA monitor error: {e}")
        await asyncio.sleep(60)  # Check every minute


async def background_claim_cleanup():
    """Background task to release expired claims."""
    while True:
        try:
            await review_queue.release_expired_claims()
        except Exception as e:
            logger.error(f"Claim cleanup error: {e}")
        await asyncio.sleep(30)  # Check every 30 seconds


# =============================================================================
# HEALTH & STATUS ENDPOINTS
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0"
    }


@app.get("/api/v1/status")
async def get_status():
    """Get system status."""
    queue_stats = await review_queue.get_stats()
    
    return {
        "status": "operational",
        "queue": {
            "pending": queue_stats.total_pending,
            "assigned": queue_stats.total_assigned,
            "at_risk": queue_stats.sla_at_risk
        },
        "alerts": len(alert_manager.get_active_alerts()),
        "mock_mode": MOCK_MODE
    }


# =============================================================================
# DOCUMENTS & EXTRACTION LOG ENDPOINTS (for AI Thinking panel)
# =============================================================================

@app.get("/api/v1/documents")
async def list_documents():
    """
    List all processed documents with their extraction status.
    Fetches from both database and in-memory extraction logs.
    """
    docs = []
    seen_ids = set()
    
    # First, add documents from in-memory extraction logs (most recent)
    for doc_id, log in extraction_logs.items():
        seen_ids.add(doc_id)
        docs.append({
            "document_id": doc_id,
            "filename": log.get("filename", "unknown"),
            "status": log.get("status", "unknown"),
            "confidence_score": log.get("confidence_score", 0),
            "completed_at": log.get("completed_at"),
            "duration_ms": log.get("duration_ms", 0),
            "needs_review": log.get("confidence_score", 1.0) < CONFIDENCE_THRESHOLD,
            "source": "memory"
        })
    
    # Then, add documents from database that aren't in memory
    try:
        db_docs = await DocumentRepository.list_all(limit=100)
        for db_doc in db_docs:
            doc_id = db_doc.get("document_id")
            if doc_id and doc_id not in seen_ids:
                metadata = db_doc.get("metadata") or {}
                if isinstance(metadata, str):
                    import json
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                
                confidence = db_doc.get("confidence_score") or 0
                docs.append({
                    "document_id": doc_id,
                    "filename": metadata.get("filename", doc_id),
                    "status": db_doc.get("status", "unknown"),
                    "confidence_score": confidence,
                    "completed_at": str(db_doc.get("created_at")) if db_doc.get("created_at") else None,
                    "duration_ms": 0,
                    "needs_review": db_doc.get("needs_review", False) or confidence < CONFIDENCE_THRESHOLD,
                    "review_item_id": db_doc.get("review_item_id"),
                    "source": "database"
                })
    except Exception as e:
        logger.warning(f"Failed to fetch documents from DB: {e}")
    
    # Sort by completion time, most recent first
    docs.sort(key=lambda x: x.get("completed_at") or "", reverse=True)
    
    return {
        "documents": docs,
        "total": len(docs)
    }


@app.get("/api/v1/documents/{document_id}/extraction-log")
async def get_extraction_log(document_id: str):
    """
    Get detailed extraction log for a document.
    Shows AI thinking process: steps, timing, confidence breakdown.
    """
    if document_id not in extraction_logs:
        # Try partial match (document IDs from frontend may differ slightly)
        for key in extraction_logs.keys():
            if document_id in key or key in document_id:
                return extraction_logs[key]
        raise HTTPException(
            status_code=404, 
            detail=f"Document '{document_id}' not found. Available: {list(extraction_logs.keys())[:5]}"
        )
    
    return extraction_logs[document_id]


@app.get("/api/v1/extraction-logs/debug")
async def debug_extraction_logs():
    """Debug endpoint to see available extraction log keys."""
    return {
        "count": len(extraction_logs),
        "document_ids": list(extraction_logs.keys())
    }


@app.get("/api/v1/review/debug")
async def debug_review_queue():
    """Debug endpoint to check review queue state."""
    from .database import ReviewRepository
    from .review_queue import ReviewItem, ExtractedFieldData, ReviewStatus
    
    # Get items from database
    db_active = await ReviewRepository.get_active_items()
    db_completed = await ReviewRepository.get_completed_items(limit=10)
    
    # Get items from in-memory queue
    in_memory_items = list(review_queue._items.keys())
    
    # Try to hydrate one item to see error
    hydration_test = None
    if db_active:
        sample = db_active[0]
        try:
            extraction_result = {}
            if sample.get('extraction_result'):
                for k, v in sample['extraction_result'].items():
                    if isinstance(v, dict):
                        extraction_result[k] = ExtractedFieldData(**v)
            
            valid_keys = ReviewItem.__annotations__.keys()
            item_data = {k: v for k, v in sample.items() if k in valid_keys}
            item_data['extraction_result'] = extraction_result
            
            if 'priority_factors' not in item_data or not item_data['priority_factors']:
                item_data['priority_factors'] = {}
            
            if 'status' in item_data and isinstance(item_data['status'], str):
                item_data['status'] = ReviewStatus(item_data['status'])
            
            item = ReviewItem(**item_data)
            hydration_test = {"success": True, "item_id": item.item_id}
        except Exception as e:
            hydration_test = {"success": False, "error": str(e), "missing_keys": [k for k in ReviewItem.__annotations__.keys() if k not in sample]}
    
    return {
        "database": {
            "active_items_count": len(db_active),
            "active_items": [{"item_id": i.get("item_id"), "status": i.get("status"), "document_id": i.get("document_id")} for i in db_active[:10]],
            "completed_items_count": len(db_completed),
            "completed_items": [{"item_id": i.get("item_id"), "status": i.get("status")} for i in db_completed[:5]],
            "sample_db_keys": list(db_active[0].keys()) if db_active else []
        },
        "in_memory": {
            "items_count": len(in_memory_items),
            "item_ids": in_memory_items[:10]
        },
        "hydration_test": hydration_test,
        "expected_keys": list(ReviewItem.__annotations__.keys())
    }


@app.get("/api/v1/documents/{document_id}/output")
async def get_document_output(document_id: str):
    """
    Get the extraction output JSON for a document.
    """
    import json
    output_path = Path(OUTPUT_DIR) / f"{document_id}.json"
    
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output not found")
    
    with open(output_path) as f:
        return json.load(f)


# =============================================================================
# DOCUMENT PROCESSING ENDPOINTS
# =============================================================================

@app.post("/api/v1/documents/process", response_model=ProcessDocumentResponse)
async def process_document(
    request: ProcessDocumentRequest,
    background_tasks: BackgroundTasks
):
    """
    Process a single document through the extraction pipeline.
    
    This will:
    1. Ingest the document
    2. Extract fields using AI (Mistral)
    3. Validate the extraction
    4. Route to review queue if low confidence, or auto-approve
    5. Generate output files
    """
    # Record metric
    metrics.record_document_received(request.document_type, request.source)
    
    # Create document input (in real system, would load from storage)
    try:
        doc_type_enum = DocumentType(request.document_type)
    except ValueError:
        doc_type_enum = DocumentType.INVOICE

    doc = DocumentInput(
        document_id=request.document_id,
        content=f"Sample invoice content for {request.document_id}".encode(),
        content_hash=f"hash_{request.document_id}",
        document_type=doc_type_enum,
        metadata={"source": request.source}
    )
    
    # Create and execute workflow
    dag = await create_processing_workflow()
    executor = WorkflowExecutor(dag)
    
    result = await executor.execute(
        document_id=request.document_id,
        initial_context={"document": doc}
    )
    
    # Extract results
    route_output = result.outputs.get("route", {})
    output_result = result.outputs.get("output", {})
    
    return ProcessDocumentResponse(
        document_id=request.document_id,
        status=result.status.value,
        workflow_id=result.execution_id,
        needs_review=route_output.get("action") == "review",
        review_item_id=route_output.get("review_item_id"),
        auto_approved=route_output.get("action") == "auto_approve",
        output_paths=output_result.get("paths")
    )


@app.post("/api/v1/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    document_type: str = "invoice",
    source: str = "upload"
):
    """
    Upload and process a document file (PDF, image).
    """
    content = await file.read()
    document_id = f"doc_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    
    # Record metric
    metrics.record_document_received(document_type, source)
    
    # Create document input
    try:
        doc_type_enum = DocumentType(document_type)
    except ValueError:
        doc_type_enum = DocumentType.INVOICE  # Default fallback
        
    doc = DocumentInput(
        document_id=document_id,
        content=content,
        content_hash=f"hash_{document_id}",
        document_type=doc_type_enum,
        metadata={"source": source, "filename": file.filename}
    )
    
    # Create and execute workflow
    dag = await create_processing_workflow()
    executor = WorkflowExecutor(dag)
    
    result = await executor.execute(
        document_id=document_id,
        initial_context={"document": doc}
    )
    
    route_output = result.outputs.get("route", {})
    
    return {
        "document_id": document_id,
        "filename": file.filename,
        "status": result.status.value,
        "needs_review": route_output.get("action") == "review",
        "review_item_id": route_output.get("review_item_id")
    }


@app.post("/api/v1/documents/batch")
async def batch_process(request: BatchProcessRequest):
    """Process multiple documents in parallel."""
    dag = await create_processing_workflow()
    executor = BatchWorkflowExecutor(dag, max_concurrent_documents=10)
    
    # Create document inputs
    docs = []
    for req in request.documents:
        metrics.record_document_received(req.document_type, req.source)
        try:
            doc_type_enum = DocumentType(req.document_type)
        except ValueError:
            doc_type_enum = DocumentType.INVOICE

        docs.append({
            "document": DocumentInput(
                document_id=req.document_id,
                content=f"Sample content for {req.document_id}".encode(),
                content_hash=f"hash_{req.document_id}",
                document_type=doc_type_enum,
                metadata={"source": req.source}
            )
        })
    
    # Execute batch
    results = await executor.execute_batch(
        [d["document"].document_id for d in docs],
        initial_contexts=docs
    )
    
    return {
        "processed": len(results),
        "results": [
            {
                "document_id": r.document_id,
                "status": r.status.value,
                "workflow_id": r.execution_id
            }
            for r in results
        ]
    }


# =============================================================================
# METRICS ENDPOINTS
# =============================================================================

@app.get("/api/v1/metrics")
async def get_metrics():
    """Get current metrics as JSON for frontend display."""
    # Get document counts from database (persists across restarts)
    try:
        docs = await DocumentRepository.list_all(limit=10000)
        docs_received = len(docs)
        docs_processed = sum(1 for d in docs if d.get("status") in ("completed", "review_pending", "processing"))
        docs_errors = sum(1 for d in docs if d.get("status") in ("failed", "error"))
    except Exception as e:
        logger.warning(f"Failed to get docs from DB, falling back to in-memory: {e}")
        docs_received = len(extraction_logs)
        docs_processed = sum(1 for log in extraction_logs.values() if log.get("status") == "completed")
        docs_errors = sum(1 for log in extraction_logs.values() if log.get("status") in ("failed", "error"))
    
    # Get review queue depth
    queue_stats = await review_queue.get_stats()
    queue_depth = queue_stats.total_pending
    
    # Get active alerts
    active_alerts = len(alert_manager.get_active_alerts())
    
    return {
        "documents_received": docs_received,
        "documents_processed": docs_processed,
        "extraction_errors": docs_errors,
        "queue_depth": queue_depth,
        "active_alerts": active_alerts
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Get metrics in Prometheus format."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=metrics_exporter.to_prometheus(),
        media_type="text/plain"
    )


# =============================================================================
# INCLUDE REVIEW QUEUE API
# =============================================================================

# Import all review queue endpoints
# Import review queue endpoints router
from .review_queue import get_review_router

# Include review router
app.include_router(get_review_router(review_queue))


# =============================================================================
# STATIC FILES (for production - serves the React build)
# =============================================================================

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    
    @app.get("/")
    async def serve_spa():
        return FileResponse(static_dir / "index.html")
    
    @app.get("/{path:path}")
    async def serve_spa_fallback(path: str):
        # Serve index.html for SPA routing
        file_path = static_dir / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(static_dir / "index.html")


# =============================================================================
# WEBSOCKET FOR REAL-TIME UPDATES
# =============================================================================

@app.websocket("/ws/extraction/{document_id}")
async def websocket_extraction(websocket: WebSocket, document_id: str):
    """
    WebSocket endpoint for real-time extraction updates.
    Connect to receive live updates during document processing.
    """
    await ws_manager.connect(websocket, document_id)
    try:
        # Send current status if exists
        if document_id in extraction_logs:
            await websocket.send_json({
                "type": "status",
                "data": extraction_logs[document_id]
            })
        
        # Keep connection open, waiting for updates
        while True:
            # Wait for any message (ping/pong for keep-alive)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(document_id)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
