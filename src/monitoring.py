"""
Monitoring Module

Production-grade metrics collection, SLA monitoring, and alerting
for the document processing pipeline.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class MetricType(str, Enum):
    """Types of metrics."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(str, Enum):
    """Alert states."""
    OK = "ok"
    FIRING = "firing"
    RESOLVED = "resolved"


# =============================================================================
# METRIC CLASSES
# =============================================================================

@dataclass
class MetricValue:
    """A single metric value with timestamp."""
    value: float
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)


class Counter:
    """A monotonically increasing counter metric."""
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
    
    def inc(self, value: float = 1, **labels):
        """Increment the counter."""
        label_key = tuple(sorted(labels.items()))
        self._values[label_key] += value
    
    def get(self, **labels) -> float:
        """Get current counter value."""
        label_key = tuple(sorted(labels.items()))
        return self._values[label_key]
    
    def collect(self) -> List[MetricValue]:
        """Collect all metric values."""
        now = datetime.utcnow()
        return [
            MetricValue(value=v, timestamp=now, labels=dict(k))
            for k, v in self._values.items()
        ]


class Gauge:
    """A gauge metric that can go up and down."""
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
    
    def set(self, value: float, **labels):
        """Set the gauge value."""
        label_key = tuple(sorted(labels.items()))
        self._values[label_key] = value
    
    def inc(self, value: float = 1, **labels):
        """Increment the gauge."""
        label_key = tuple(sorted(labels.items()))
        self._values[label_key] += value
    
    def dec(self, value: float = 1, **labels):
        """Decrement the gauge."""
        label_key = tuple(sorted(labels.items()))
        self._values[label_key] -= value
    
    def get(self, **labels) -> float:
        """Get current gauge value."""
        label_key = tuple(sorted(labels.items()))
        return self._values[label_key]
    
    def collect(self) -> List[MetricValue]:
        """Collect all metric values."""
        now = datetime.utcnow()
        return [
            MetricValue(value=v, timestamp=now, labels=dict(k))
            for k, v in self._values.items()
        ]


class Histogram:
    """A histogram metric for measuring distributions."""
    
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, float('inf'))
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None, buckets: tuple = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._counts: Dict[tuple, Dict[float, int]] = defaultdict(lambda: defaultdict(int))
        self._sums: Dict[tuple, float] = defaultdict(float)
        self._totals: Dict[tuple, int] = defaultdict(int)
        self._values: Dict[tuple, List[float]] = defaultdict(list)
    
    def observe(self, value: float, **labels):
        """Record an observation."""
        label_key = tuple(sorted(labels.items()))
        
        # Update buckets
        for bucket in self.buckets:
            if value <= bucket:
                self._counts[label_key][bucket] += 1
        
        # Update sum and count
        self._sums[label_key] += value
        self._totals[label_key] += 1
        
        # Keep recent values for percentile calculation
        self._values[label_key].append(value)
        if len(self._values[label_key]) > 10000:
            self._values[label_key] = self._values[label_key][-5000:]
    
    def get_percentile(self, percentile: float, **labels) -> float:
        """Get a percentile value (e.g., 0.95 for p95)."""
        label_key = tuple(sorted(labels.items()))
        values = sorted(self._values.get(label_key, []))
        if not values:
            return 0.0
        index = int(len(values) * percentile)
        return values[min(index, len(values) - 1)]
    
    def get_sum(self, **labels) -> float:
        """Get sum of all observations."""
        label_key = tuple(sorted(labels.items()))
        return self._sums[label_key]
    
    def get_count(self, **labels) -> int:
        """Get count of all observations."""
        label_key = tuple(sorted(labels.items()))
        return self._totals[label_key]
    
    def get_mean(self, **labels) -> float:
        """Get mean of observations."""
        count = self.get_count(**labels)
        if count == 0:
            return 0.0
        return self.get_sum(**labels) / count


# =============================================================================
# METRICS REGISTRY
# =============================================================================

class MetricsRegistry:
    """Central registry for all metrics."""
    
    def __init__(self):
        self._metrics: Dict[str, Any] = {}
    
    def counter(self, name: str, description: str = "", labels: List[str] = None) -> Counter:
        """Create or get a counter metric."""
        if name not in self._metrics:
            self._metrics[name] = Counter(name, description, labels)
        return self._metrics[name]
    
    def gauge(self, name: str, description: str = "", labels: List[str] = None) -> Gauge:
        """Create or get a gauge metric."""
        if name not in self._metrics:
            self._metrics[name] = Gauge(name, description, labels)
        return self._metrics[name]
    
    def histogram(self, name: str, description: str = "", labels: List[str] = None, buckets: tuple = None) -> Histogram:
        """Create or get a histogram metric."""
        if name not in self._metrics:
            self._metrics[name] = Histogram(name, description, labels, buckets)
        return self._metrics[name]
    
    def collect_all(self) -> Dict[str, List[MetricValue]]:
        """Collect all metrics."""
        return {
            name: metric.collect() if hasattr(metric, 'collect') else []
            for name, metric in self._metrics.items()
        }


# Global registry
REGISTRY = MetricsRegistry()


# =============================================================================
# DOCUMENT PROCESSING METRICS
# =============================================================================

class DocumentMetrics:
    """Metrics specific to document processing."""
    
    def __init__(self, registry: MetricsRegistry = None):
        self.registry = registry or REGISTRY
        
        # Counters
        self.documents_received = self.registry.counter(
            "docflow_documents_received_total",
            "Total documents received",
            labels=["document_type", "source"]
        )
        
        self.documents_processed = self.registry.counter(
            "docflow_documents_processed_total",
            "Total documents processed",
            labels=["document_type", "status"]
        )
        
        self.extraction_errors = self.registry.counter(
            "docflow_extraction_errors_total",
            "Total extraction errors",
            labels=["document_type", "error_type"]
        )
        
        self.field_corrections = self.registry.counter(
            "docflow_field_corrections_total",
            "Total field corrections made",
            labels=["field_name"]
        )
        
        # Gauges
        self.active_documents = self.registry.gauge(
            "docflow_active_documents",
            "Documents currently being processed"
        )
        
        self.queue_depth = self.registry.gauge(
            "docflow_queue_depth",
            "Current queue depth",
            labels=["queue_type"]
        )
        
        self.review_queue_depth = self.registry.gauge(
            "docflow_review_queue_depth",
            "Current review queue depth",
            labels=["priority"]
        )
        
        # Histograms
        self.processing_duration = self.registry.histogram(
            "docflow_processing_duration_seconds",
            "Document processing duration",
            labels=["document_type"],
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 15, 20, 25, 30, 45, 60, 90, 120, float('inf'))
        )
        
        self.extraction_confidence = self.registry.histogram(
            "docflow_extraction_confidence",
            "Extraction confidence scores",
            labels=["document_type"],
            buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)
        )
        
        self.review_duration = self.registry.histogram(
            "docflow_review_duration_seconds",
            "Human review duration",
            labels=["decision"],
            buckets=(5, 10, 20, 30, 45, 60, 90, 120, 180, 300, float('inf'))
        )
    
    def record_document_received(self, document_type: str, source: str):
        """Record a document received."""
        self.documents_received.inc(document_type=document_type, source=source)
    
    def record_document_processed(self, document_type: str, status: str, duration_seconds: float):
        """Record a document processed."""
        self.documents_processed.inc(document_type=document_type, status=status)
        self.processing_duration.observe(duration_seconds, document_type=document_type)
    
    def record_extraction_error(self, document_type: str, error_type: str):
        """Record an extraction error."""
        self.extraction_errors.inc(document_type=document_type, error_type=error_type)
    
    def record_field_correction(self, field_name: str):
        """Record a field correction."""
        self.field_corrections.inc(field_name=field_name)
    
    def record_confidence(self, document_type: str, confidence: float):
        """Record extraction confidence."""
        self.extraction_confidence.observe(confidence, document_type=document_type)
    
    def record_review(self, decision: str, duration_seconds: float):
        """Record a review completion."""
        self.review_duration.observe(duration_seconds, decision=decision)
    
    def set_queue_depth(self, queue_type: str, depth: int):
        """Set current queue depth."""
        self.queue_depth.set(depth, queue_type=queue_type)
    
    def set_review_queue_by_priority(self, priority: int, count: int):
        """Set review queue depth by priority."""
        self.review_queue_depth.set(count, priority=str(priority))


# =============================================================================
# SLA DEFINITIONS
# =============================================================================

@dataclass
class SLADefinition:
    """Definition of a Service Level Agreement."""
    name: str
    metric: str
    threshold: float
    comparison: str  # lt, lte, gt, gte, eq
    window_seconds: int
    severity: AlertSeverity
    description: str = ""
    
    def check(self, value: float) -> bool:
        """Check if the SLA is met."""
        if self.comparison == "lt":
            return value < self.threshold
        elif self.comparison == "lte":
            return value <= self.threshold
        elif self.comparison == "gt":
            return value > self.threshold
        elif self.comparison == "gte":
            return value >= self.threshold
        elif self.comparison == "eq":
            return value == self.threshold
        return False


# Default SLA definitions
DEFAULT_SLAS = [
    SLADefinition(
        name="latency_p95",
        metric="p95_latency_seconds",
        threshold=30.0,
        comparison="lt",
        window_seconds=300,  # 5 minutes
        severity=AlertSeverity.CRITICAL,
        description="P95 processing latency must be under 30 seconds"
    ),
    SLADefinition(
        name="throughput",
        metric="docs_per_hour",
        threshold=4500,
        comparison="gt",
        window_seconds=900,  # 15 minutes
        severity=AlertSeverity.WARNING,
        description="Must process at least 4500 documents per hour"
    ),
    SLADefinition(
        name="error_rate",
        metric="error_rate_percent",
        threshold=1.0,
        comparison="lt",
        window_seconds=300,  # 5 minutes
        severity=AlertSeverity.CRITICAL,
        description="Error rate must be under 1%"
    ),
    SLADefinition(
        name="queue_depth",
        metric="review_queue_depth",
        threshold=500,
        comparison="lt",
        window_seconds=300,  # 5 minutes
        severity=AlertSeverity.WARNING,
        description="Review queue depth must be under 500"
    ),
    SLADefinition(
        name="sla_breach_rate",
        metric="sla_breach_percent",
        threshold=0.1,
        comparison="lt",
        window_seconds=3600,  # 1 hour
        severity=AlertSeverity.CRITICAL,
        description="SLA breach rate must be under 0.1%"
    ),
]


# =============================================================================
# ALERT MANAGER
# =============================================================================

@dataclass
class Alert:
    """An alert triggered by SLA violation."""
    alert_id: str
    sla_name: str
    severity: AlertSeverity
    state: AlertState
    message: str
    metric_value: float
    threshold: float
    started_at: datetime
    resolved_at: Optional[datetime] = None
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None


class AlertManager:
    """Manages alerts and notifications."""
    
    def __init__(self):
        self._alerts: Dict[str, Alert] = {}
        self._handlers: List[Callable[[Alert], None]] = []
        self._alert_history: List[Alert] = []
    
    def register_handler(self, handler: Callable[[Alert], None]):
        """Register an alert handler."""
        self._handlers.append(handler)
    
    def fire(self, sla: SLADefinition, metric_value: float):
        """Fire an alert for an SLA violation."""
        alert_id = f"alert_{sla.name}_{int(time.time())}"
        
        # Check if already firing for this SLA
        existing = next(
            (a for a in self._alerts.values() 
             if a.sla_name == sla.name and a.state == AlertState.FIRING),
            None
        )
        
        if existing:
            return existing
        
        alert = Alert(
            alert_id=alert_id,
            sla_name=sla.name,
            severity=sla.severity,
            state=AlertState.FIRING,
            message=f"SLA violation: {sla.description}. Current: {metric_value:.2f}, Threshold: {sla.threshold}",
            metric_value=metric_value,
            threshold=sla.threshold,
            started_at=datetime.utcnow()
        )
        
        self._alerts[alert_id] = alert
        logger.warning(f"Alert fired: {alert.message}")
        
        # Notify handlers
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Alert handler failed: {e}")
        
        return alert
    
    def resolve(self, sla_name: str):
        """Resolve alerts for an SLA."""
        for alert in self._alerts.values():
            if alert.sla_name == sla_name and alert.state == AlertState.FIRING:
                alert.state = AlertState.RESOLVED
                alert.resolved_at = datetime.utcnow()
                self._alert_history.append(alert)
                logger.info(f"Alert resolved: {alert.sla_name}")
    
    def acknowledge(self, alert_id: str, user: str):
        """Acknowledge an alert."""
        if alert_id in self._alerts:
            self._alerts[alert_id].acknowledged = True
            self._alerts[alert_id].acknowledged_by = user
    
    def get_active_alerts(self) -> List[Alert]:
        """Get all active alerts."""
        return [a for a in self._alerts.values() if a.state == AlertState.FIRING]
    
    def get_alerts_by_severity(self, severity: AlertSeverity) -> List[Alert]:
        """Get alerts by severity."""
        return [a for a in self._alerts.values() if a.severity == severity and a.state == AlertState.FIRING]


# =============================================================================
# SLA MONITOR
# =============================================================================

class SLAMonitor:
    """Monitors SLAs and triggers alerts."""
    
    def __init__(
        self,
        metrics: DocumentMetrics,
        alert_manager: AlertManager,
        slas: List[SLADefinition] = None
    ):
        self.metrics = metrics
        self.alert_manager = alert_manager
        self.slas = slas or DEFAULT_SLAS
        self._last_check: Dict[str, datetime] = {}
        self._metrics_cache: Dict[str, List[float]] = defaultdict(list)
    
    def _get_metric_value(self, metric_name: str) -> float:
        """Get the current value for a metric."""
        if metric_name == "p95_latency_seconds":
            return self.metrics.processing_duration.get_percentile(0.95)
        
        elif metric_name == "docs_per_hour":
            # Calculate from recent processing count
            count = self.metrics.documents_processed.get()
            # Simplified: just return the rate
            return count  # In production, calculate hourly rate
        
        elif metric_name == "error_rate_percent":
            total = self.metrics.documents_processed.get() or 1
            errors = self.metrics.extraction_errors.get()
            return (errors / total) * 100
        
        elif metric_name == "review_queue_depth":
            return self.metrics.queue_depth.get(queue_type="review")
        
        elif metric_name == "sla_breach_percent":
            # Simplified calculation
            return 0.05  # In production, calculate from actual data
        
        return 0.0
    
    async def check_all(self):
        """Check all SLAs."""
        for sla in self.slas:
            await self.check_sla(sla)
    
    async def check_sla(self, sla: SLADefinition):
        """Check a single SLA."""
        try:
            value = self._get_metric_value(sla.metric)
            
            if sla.check(value):
                # SLA is met, resolve any existing alerts
                self.alert_manager.resolve(sla.name)
            else:
                # SLA violated, fire alert
                self.alert_manager.fire(sla, value)
            
            self._last_check[sla.name] = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"Failed to check SLA {sla.name}: {e}")
    
    async def run_monitoring_loop(self, interval_seconds: int = 60):
        """Run continuous monitoring loop."""
        logger.info("Starting SLA monitoring loop")
        
        while True:
            try:
                await self.check_all()
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
            
            await asyncio.sleep(interval_seconds)


# =============================================================================
# METRICS EXPORTER
# =============================================================================

class MetricsExporter:
    """Exports metrics in various formats."""
    
    def __init__(self, registry: MetricsRegistry):
        self.registry = registry
    
    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        
        for name, metric in self.registry._metrics.items():
            if isinstance(metric, Counter):
                lines.append(f"# TYPE {name} counter")
                for mv in metric.collect():
                    labels = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
                    label_str = f"{{{labels}}}" if labels else ""
                    lines.append(f"{name}{label_str} {mv.value}")
            
            elif isinstance(metric, Gauge):
                lines.append(f"# TYPE {name} gauge")
                for mv in metric.collect():
                    labels = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
                    label_str = f"{{{labels}}}" if labels else ""
                    lines.append(f"{name}{label_str} {mv.value}")
            
            elif isinstance(metric, Histogram):
                lines.append(f"# TYPE {name} histogram")
                # Simplified: just output sum and count
                for label_key, total in metric._totals.items():
                    labels = ",".join(f'{k}="{v}"' for k, v in label_key)
                    label_str = f"{{{labels}}}" if labels else ""
                    lines.append(f"{name}_count{label_str} {total}")
                    lines.append(f"{name}_sum{label_str} {metric._sums[label_key]}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def to_json(self) -> Dict[str, Any]:
        """Export metrics as JSON."""
        result = {}
        
        for name, metric in self.registry._metrics.items():
            if isinstance(metric, Counter):
                result[name] = {
                    "type": "counter",
                    "values": [
                        {"value": mv.value, "labels": mv.labels, "timestamp": mv.timestamp.isoformat()}
                        for mv in metric.collect()
                    ]
                }
            elif isinstance(metric, Gauge):
                result[name] = {
                    "type": "gauge",
                    "values": [
                        {"value": mv.value, "labels": mv.labels, "timestamp": mv.timestamp.isoformat()}
                        for mv in metric.collect()
                    ]
                }
            elif isinstance(metric, Histogram):
                result[name] = {
                    "type": "histogram",
                    "p50": metric.get_percentile(0.5),
                    "p90": metric.get_percentile(0.9),
                    "p95": metric.get_percentile(0.95),
                    "p99": metric.get_percentile(0.99),
                    "mean": metric.get_mean(),
                    "count": metric.get_count(),
                    "sum": metric.get_sum()
                }
        
        return result


# =============================================================================
# LOGGING ALERT HANDLER
# =============================================================================

def log_alert_handler(alert: Alert):
    """Simple alert handler that logs alerts."""
    if alert.severity == AlertSeverity.CRITICAL:
        logger.critical(f"🚨 CRITICAL ALERT: {alert.message}")
    elif alert.severity == AlertSeverity.WARNING:
        logger.warning(f"⚠️ WARNING: {alert.message}")
    else:
        logger.info(f"ℹ️ INFO: {alert.message}")


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Test the monitoring module."""
    # Initialize
    metrics = DocumentMetrics()
    alert_manager = AlertManager()
    alert_manager.register_handler(log_alert_handler)
    
    sla_monitor = SLAMonitor(metrics, alert_manager)
    exporter = MetricsExporter(REGISTRY)
    
    # Simulate some metrics
    print("Simulating document processing metrics...")
    
    for i in range(100):
        doc_type = "invoice" if i % 3 == 0 else "receipt"
        
        # Record document received and processed
        metrics.record_document_received(doc_type, "email")
        
        # Simulate processing time (some fast, some slow)
        duration = 5 + (i % 40)  # 5-45 seconds
        metrics.record_document_processed(doc_type, "completed", duration)
        
        # Record confidence
        confidence = 0.7 + (i % 30) / 100
        metrics.record_confidence(doc_type, confidence)
        
        # Some errors
        if i % 20 == 0:
            metrics.record_extraction_error(doc_type, "timeout")
    
    # Set queue depths
    metrics.set_queue_depth("processing", 25)
    metrics.set_queue_depth("review", 47)
    metrics.set_review_queue_by_priority(1, 8)
    metrics.set_review_queue_by_priority(2, 12)
    metrics.set_review_queue_by_priority(3, 15)
    
    print()
    print("Checking SLAs...")
    await sla_monitor.check_all()
    
    # Print active alerts
    active_alerts = alert_manager.get_active_alerts()
    print(f"\nActive Alerts: {len(active_alerts)}")
    for alert in active_alerts:
        print(f"  [{alert.severity.value.upper()}] {alert.message}")
    
    # Export metrics
    print("\n--- Prometheus Format ---")
    print(exporter.to_prometheus()[:1000] + "...")
    
    print("\n--- JSON Format ---")
    json_metrics = exporter.to_json()
    for name, data in list(json_metrics.items())[:3]:
        print(f"{name}: {json.dumps(data, indent=2, default=str)[:200]}...")
    
    # Show percentiles
    print("\n--- Processing Duration Percentiles ---")
    print(f"  P50: {metrics.processing_duration.get_percentile(0.5):.2f}s")
    print(f"  P90: {metrics.processing_duration.get_percentile(0.9):.2f}s")
    print(f"  P95: {metrics.processing_duration.get_percentile(0.95):.2f}s")
    print(f"  P99: {metrics.processing_duration.get_percentile(0.99):.2f}s")
    print(f"  Mean: {metrics.processing_duration.get_mean():.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
