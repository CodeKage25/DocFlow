import { useState, useEffect, useCallback, useRef } from 'react';
import type { ReviewItem, QueueStats, FieldCorrection } from './types';
import { reviewApi } from './api/reviewApi';
import './App.css';

// =============================================================================
// API Configuration
// =============================================================================

const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.PROD ? '' : 'http://127.0.0.1:8000');

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================



function formatSLATime(seconds: number): string {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

function getConfidenceClass(confidence: number): string {
    if (confidence >= 0.9) return 'confidence-high';
    if (confidence >= 0.75) return 'confidence-medium';
    return 'confidence-low';
}

// =============================================================================
// ICONS
// =============================================================================

const Icons = {
    Clock: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 6v6l4 2" />
        </svg>
    ),
    Check: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M20 6L9 17l-5-5" />
        </svg>
    ),
    Edit: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
    ),
    X: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
        </svg>
    ),
    AlertTriangle: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
    ),
    RefreshCw: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M23 4v6h-6M1 20v-6h6" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
        </svg>
    ),
    FileText: () => (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
        </svg>
    ),
    Lock: () => (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
    ),
    Unlock: () => (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 9.9-1" />
        </svg>
    ),
    Upload: () => (
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
    ),
    Activity: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
    ),
    Inbox: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
            <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
        </svg>
    ),
    BarChart: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="20" x2="12" y2="10" />
            <line x1="18" y1="20" x2="18" y2="4" />
            <line x1="6" y1="20" x2="6" y2="16" />
        </svg>
    ),
    Folder: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
    ),
    Menu: () => (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="18" x2="21" y2="18" />
        </svg>
    ),
    ArrowLeft: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
        </svg>
    ),
    CheckCircle: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
            <polyline points="22 4 12 14.01 9 11.01" />
        </svg>
    ),
    XCircle: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
        </svg>
    ),
    Brain: () => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 4.5a2.5 2.5 0 0 0-4.96-.46 2.5 2.5 0 0 0-1.98 3 2.5 2.5 0 0 0-1.32 4.24 3 3 0 0 0 .34 5.58 2.5 2.5 0 0 0 2.96 3.08A2.5 2.5 0 0 0 12 19.5a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 12 4.5" />
            <path d="M12 4.5v15" />
            <path d="M8 16.5a3 3 0 0 0-1-2.16" />
            <path d="M16 16.5a3 3 0 0 1 1-2.16" />
        </svg>
    ),
};

// =============================================================================
// NAVIGATION
// =============================================================================

type ViewType = 'documents' | 'review' | 'metrics' | 'ai-logs';

interface NavItemProps {
    icon: React.ReactNode;
    label: string;
    active: boolean;
    onClick: () => void;
    badge?: number;
}

function NavItem({ icon, label, active, onClick, badge }: NavItemProps) {
    return (
        <button className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>
            {icon}
            <span className="nav-label">{label}</span>
            {badge !== undefined && badge > 0 && (
                <span className="nav-badge">{badge}</span>
            )}
        </button>
    );
}

// =============================================================================
// DOCUMENT UPLOAD VIEW - Enhanced
// =============================================================================

interface ProcessedDocument {
    document_id: string;
    filename: string;
    status: 'pending' | 'processing' | 'completed' | 'error' | 'needs_review';
    needs_review: boolean;
    review_item_id?: string;
    processed_at: string;
}

interface DocumentsViewProps {
    onGoToReview: () => void;
    files: File[];
    setFiles: React.Dispatch<React.SetStateAction<File[]>>;
    processedDocs: ProcessedDocument[];
    setProcessedDocs: React.Dispatch<React.SetStateAction<ProcessedDocument[]>>;
    queueCount: number;
    refreshTrigger?: number;
}

function DocumentsView({
    onGoToReview,
    files,
    setFiles,
    processedDocs,
    setProcessedDocs,
    queueCount,
    refreshTrigger = 0
}: DocumentsViewProps) {
    const [processing, setProcessing] = useState(false);
    const [dragActive, setDragActive] = useState(false);
    const [expandedDoc, setExpandedDoc] = useState<string | null>(null);
    const [extractionLogs, setExtractionLogs] = useState<Record<string, unknown>>({});
    const [isLoading, setIsLoading] = useState(true);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const wsRef = useRef<WebSocket | null>(null);

    // Load existing documents from database on mount
    useEffect(() => {
        const loadDocuments = async () => {
            try {
                const res = await fetch(`${API_BASE}/api/v1/documents`);
                if (res.ok) {
                    const data = await res.json();
                    const docsFromDb: ProcessedDocument[] = (data.documents || []).map((d: Record<string, unknown>) => ({
                        document_id: d.document_id as string,
                        filename: (d.filename as string) || (d.document_id as string),
                        status: d.needs_review ? 'needs_review' : (d.status as string) || 'completed',
                        needs_review: d.needs_review as boolean,
                        review_item_id: d.review_item_id as string | undefined,
                        processed_at: (d.completed_at as string) || new Date().toISOString(),
                    }));
                    if (docsFromDb.length > 0) {
                        setProcessedDocs(prev => {
                            // Merge strategy: Update existing docs if status changed, add new ones
                            const prevMap = new Map(prev.map(p => [p.document_id, p]));
                            docsFromDb.forEach(d => {
                                prevMap.set(d.document_id, d);
                            });
                            return Array.from(prevMap.values());
                        });
                    }
                }
            } catch (e) {
                console.error('Failed to load documents from DB:', e);
            } finally {
                setIsLoading(false);
            }
        };
        loadDocuments();
    }, [setProcessedDocs, refreshTrigger]);

    // WebSocket connection for real-time updates
    useEffect(() => {
        const wsUrl = API_BASE.replace('http', 'ws') + '/ws/extraction/global';
        const ws = new WebSocket(wsUrl);

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'extraction_complete' && msg.data) {
                    // Update extraction logs
                    setExtractionLogs(prev => ({ ...prev, [msg.document_id]: msg.data }));
                }
            } catch (e) {
                console.error('WebSocket message parse error:', e);
            }
        };

        ws.onerror = () => console.log('WebSocket error - will use polling fallback');
        ws.onclose = () => console.log('WebSocket closed');

        wsRef.current = ws;
        return () => ws.close();
    }, []);

    const handleDrag = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === 'dragenter' || e.type === 'dragover') {
            setDragActive(true);
        } else if (e.type === 'dragleave') {
            setDragActive(false);
        }
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);

        const droppedFiles = Array.from(e.dataTransfer.files).filter(
            file => file.type === 'application/pdf' || file.type.startsWith('image/')
        );
        setFiles(prev => [...prev, ...droppedFiles]);
    }, []);

    const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) {
            setFiles(prev => [...prev, ...Array.from(e.target.files!)]);
        }
    }, []);

    const removeFile = useCallback((index: number) => {
        setFiles(prev => prev.filter((_, i) => i !== index));
    }, []);

    const processDocuments = async () => {
        if (files.length === 0) return;

        setProcessing(true);

        for (const file of files) {
            // Add to UI as processing with temp ID
            const tempId = `temp_${Date.now()}_${file.name}`;
            const processingDoc: ProcessedDocument = {
                document_id: tempId,
                filename: file.name,
                status: 'processing',
                needs_review: false,
                processed_at: new Date().toISOString(),
            };
            setProcessedDocs(prev => [processingDoc, ...prev]);

            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('document_type', 'invoice');

                const response = await fetch(`${API_BASE}/api/v1/documents/upload`, {
                    method: 'POST',
                    body: formData,
                });

                if (response.ok) {
                    const data = await response.json();
                    // Update the processing doc with actual ID from backend
                    setProcessedDocs(prev => prev.map(d =>
                        d.document_id === tempId ? {
                            document_id: data.document_id,
                            filename: file.name,
                            status: data.needs_review ? 'needs_review' : 'completed',
                            needs_review: data.needs_review,
                            review_item_id: data.review_item_id,
                            processed_at: new Date().toISOString(),
                        } : d
                    ));
                } else {
                    // Update to error state
                    setProcessedDocs(prev => prev.map(d =>
                        d.document_id === tempId ? {
                            ...d,
                            status: 'error' as const,
                        } : d
                    ));
                }
            } catch {
                // Update to error state
                setProcessedDocs(prev => prev.map(d =>
                    d.document_id === tempId ? {
                        ...d,
                        status: 'error' as const,
                    } : d
                ));
            }
        }

        setFiles([]);
        setProcessing(false);
    };

    const completedCount = processedDocs.filter(d => d.status === 'completed').length;
    const errorCount = processedDocs.filter(d => d.status === 'error').length;
    const needsReviewCount = queueCount;

    return (
        <div className="documents-view">
            {/* Hero Section */}
            <div className="upload-hero">
                <div className="upload-hero-content">
                    <h1>📄 Document Processing</h1>
                    <p>Upload invoices, receipts, and financial documents for AI-powered extraction</p>
                </div>
            </div>

            {/* Stats Cards */}
            {(processedDocs.length > 0 || isLoading) && (
                <div className="upload-stats">
                    <div className="upload-stat-card">
                        <div className="upload-stat-icon completed"><Icons.CheckCircle /></div>
                        <div className="upload-stat-info">
                            <span className="upload-stat-value">{isLoading ? '...' : completedCount}</span>
                            <span className="upload-stat-label">Completed</span>
                        </div>
                    </div>
                    <div className="upload-stat-card" onClick={needsReviewCount > 0 ? onGoToReview : undefined} style={needsReviewCount > 0 ? { cursor: 'pointer' } : {}}>
                        <div className="upload-stat-icon review"><Icons.AlertTriangle /></div>
                        <div className="upload-stat-info">
                            <span className="upload-stat-value">{isLoading ? '...' : needsReviewCount}</span>
                            <span className="upload-stat-label">Need Review</span>
                        </div>
                        {needsReviewCount > 0 && <span className="upload-stat-action">→</span>}
                    </div>
                    <div className="upload-stat-card">
                        <div className="upload-stat-icon error"><Icons.XCircle /></div>
                        <div className="upload-stat-info">
                            <span className="upload-stat-value">{isLoading ? '...' : errorCount}</span>
                            <span className="upload-stat-label">Errors</span>
                        </div>
                    </div>
                </div>
            )}

            {/* Upload Zone */}
            <div
                className={`upload-zone ${dragActive ? 'drag-active' : ''} ${files.length > 0 ? 'has-files' : ''}`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
            >
                <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept=".pdf,image/*"
                    onChange={handleFileSelect}
                    style={{ display: 'none' }}
                />
                <div className="upload-zone-content">
                    <div className="upload-icon-wrapper">
                        <Icons.Upload />
                    </div>
                    <h3>Drop files here or click to browse</h3>
                    <p>PDF, PNG, JPG up to 10MB each</p>
                </div>
            </div>

            {/* File Queue */}
            {files.length > 0 && (
                <div className="file-queue">
                    <div className="file-queue-header">
                        <h3>{files.length} file{files.length > 1 ? 's' : ''} ready</h3>
                        <button
                            className="btn btn-primary btn-lg"
                            onClick={processDocuments}
                            disabled={processing}
                        >
                            {processing ? (
                                <>
                                    <span className="spinner"></span>
                                    Processing...
                                </>
                            ) : (
                                <>
                                    <Icons.Upload />
                                    Process All
                                </>
                            )}
                        </button>
                    </div>
                    <div className="file-list">
                        {files.map((file, index) => (
                            <div key={index} className="file-item">
                                <div className="file-icon"><Icons.FileText /></div>
                                <div className="file-info">
                                    <span className="file-name">{file.name}</span>
                                    <span className="file-size">{(file.size / 1024).toFixed(1)} KB</span>
                                </div>
                                <button
                                    className="file-remove"
                                    onClick={(e) => { e.stopPropagation(); removeFile(index); }}
                                >
                                    <Icons.X />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Recent Documents with Inline AI Thinking */}
            {processedDocs.length > 0 && (
                <div className="recent-docs">
                    <h3>Processed Documents</h3>
                    <div className="recent-docs-list">
                        {processedDocs.slice(0, 10).map((doc, index) => {
                            const isExpanded = expandedDoc === doc.document_id;
                            const log = extractionLogs[doc.document_id] as { fields?: Record<string, { value: unknown; confidence: number }>, steps?: Array<{ name: string; status: string; duration_ms: number }>, duration_ms?: number, confidence_score?: number } | undefined;

                            const toggleExpand = async () => {
                                if (isExpanded) {
                                    setExpandedDoc(null);
                                } else {
                                    setExpandedDoc(doc.document_id);
                                    // Fetch extraction log if not already fetched
                                    if (!extractionLogs[doc.document_id]) {
                                        try {
                                            const res = await fetch(`${API_BASE}/api/v1/documents/${doc.document_id}/extraction-log`);
                                            if (res.ok) {
                                                const data = await res.json();
                                                setExtractionLogs(prev => ({ ...prev, [doc.document_id]: data }));
                                            }
                                        } catch (e) {
                                            console.error('Failed to fetch extraction log:', e);
                                        }
                                    }
                                }
                            };

                            return (
                                <div key={index} className={`recent-doc-item status-${doc.status} ${isExpanded ? 'expanded' : ''}`}>
                                    {/* Document Header */}
                                    <div className="recent-doc-header">
                                        <div className="recent-doc-icon">
                                            {doc.status === 'completed' && <Icons.CheckCircle />}
                                            {doc.status === 'needs_review' && <Icons.AlertTriangle />}
                                            {doc.status === 'error' && <Icons.XCircle />}
                                            {doc.status === 'processing' && <span className="spinner small"></span>}
                                        </div>
                                        <div className="recent-doc-info">
                                            <span className="recent-doc-name">{doc.filename}</span>
                                            <span className="recent-doc-time">
                                                {new Date(doc.processed_at).toLocaleTimeString()}
                                            </span>
                                        </div>
                                        <span className={`recent-doc-status status-${doc.status}`}>
                                            {doc.status === 'completed' && 'Done'}
                                            {doc.status === 'needs_review' && 'Review'}
                                            {doc.status === 'error' && 'Failed'}
                                            {doc.status === 'processing' && 'Processing'}
                                        </span>
                                    </div>

                                    {/* Claude-style Thought for Xs collapsible */}
                                    {doc.status !== 'processing' && (
                                        <div className="thought-toggle" onClick={toggleExpand}>
                                            <span className="thought-chevron">{isExpanded ? '▼' : '▶'}</span>
                                            <span className="thought-label">
                                                Thought for {log ? Math.round((log.duration_ms || 0) / 1000) : '...'}s
                                            </span>
                                        </div>
                                    )}

                                    {/* Expanded Thought Content */}
                                    {isExpanded && (
                                        <div className="thought-content">
                                            {!log ? (
                                                <div className="thought-loading">Loading extraction details...</div>
                                            ) : (
                                                <>
                                                    <div className="thought-section">
                                                        <div className="thought-text">
                                                            Extracted {Object.keys(log.fields || {}).length} fields from document using Mistral AI:
                                                        </div>

                                                        {/* Field Summary */}
                                                        <ul className="thought-fields">
                                                            {Object.entries(log.fields || {}).map(([name, field]) => (
                                                                <li key={name} className={(field.confidence || 0) < 0.75 ? 'low-conf' : ''}>
                                                                    <strong>{name.replace(/_/g, ' ')}</strong>:
                                                                    <span className="field-val">
                                                                        {typeof field.value === 'object'
                                                                            ? JSON.stringify(field.value).substring(0, 30) + '...'
                                                                            : String(field.value).substring(0, 50)}
                                                                    </span>
                                                                    <span className={`conf-badge ${(field.confidence || 0) < 0.75 ? 'low' : (field.confidence || 0) < 0.85 ? 'med' : 'high'}`}>
                                                                        {Math.round((field.confidence || 0) * 100)}%
                                                                    </span>
                                                                </li>
                                                            ))}
                                                        </ul>

                                                        {/* Processing Steps */}
                                                        <div className="thought-text" style={{ marginTop: '0.75rem' }}>
                                                            Processing steps:
                                                        </div>
                                                        <ol className="thought-steps">
                                                            {(log.steps || []).map((step, i) => (
                                                                <li key={i}>
                                                                    {step.status === 'completed' ? '✓' : '...'} {step.name} ({step.duration_ms}ms)
                                                                </li>
                                                            ))}
                                                        </ol>
                                                    </div>
                                                </>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}

// =============================================================================
// METRICS VIEW
// =============================================================================

interface SystemMetrics {
    documents_received: number;
    documents_processed: number;
    extraction_errors: number;
    queue_depth: number;
    active_alerts: number;
}

function MetricsView() {
    const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadMetrics = useCallback(async () => {
        try {
            const response = await fetch(`${API_BASE}/api/v1/metrics`);
            if (response.ok) {
                const data = await response.json();
                setMetrics(data);
                setError(null);
            } else {
                setError('Failed to load metrics');
            }
        } catch {
            setError('Could not connect to API');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadMetrics();
        const interval = setInterval(loadMetrics, 5000);
        return () => clearInterval(interval);
    }, [loadMetrics]);

    return (
        <div className="metrics-view">
            <div className="view-header">
                <h1><Icons.Activity /> System Metrics</h1>
                <button className="btn btn-ghost" onClick={loadMetrics}>
                    <Icons.RefreshCw />
                </button>
            </div>

            {loading && (
                <div className="loading-state">
                    <span className="spinner large"></span>
                    <p>Loading metrics...</p>
                </div>
            )}

            {error && (
                <div className="error-state">
                    <Icons.AlertTriangle />
                    <h3>Connection Error</h3>
                    <p>{error}</p>
                    <p className="error-hint">Make sure the API server is running</p>
                </div>
            )}

            {metrics && (
                <div className="metrics-grid">
                    <div className="metric-card gradient-blue">
                        <div className="metric-value">{metrics.documents_received || 0}</div>
                        <div className="metric-label">Documents Received</div>
                    </div>
                    <div className="metric-card gradient-green">
                        <div className="metric-value">{metrics.documents_processed || 0}</div>
                        <div className="metric-label">Processed</div>
                    </div>
                    <div className="metric-card gradient-red">
                        <div className="metric-value">{metrics.extraction_errors || 0}</div>
                        <div className="metric-label">Errors</div>
                    </div>
                    <div className="metric-card gradient-purple">
                        <div className="metric-value">{metrics.queue_depth || 0}</div>
                        <div className="metric-label">In Queue</div>
                    </div>
                </div>
            )}
        </div>
    );
}


// =============================================================================
// APPROVED DOCUMENTS LIST
// =============================================================================

function ApprovedDocumentsList({ items, onView }: { items: ReviewItem[]; onView: (item: ReviewItem) => void }) {
    if (items.length === 0) {
        return (
            <div className="queue-empty">
                <Icons.Check />
                <h3>No approved documents</h3>
                <p>Completed documents will appear here</p>
            </div>
        );
    }

    return (
        <div className="queue-list">
            {items.map(item => (
                <div
                    key={item.item_id}
                    className="queue-item"
                    onClick={() => onView(item)}
                >
                    <div className="queue-item-header">
                        <span className={`priority-badge p${item.priority}`}>P{item.priority}</span>
                        <span className="queue-item-time" style={{ fontSize: '0.75rem', color: '#64748b' }}>
                            {new Date(item.created_at).toLocaleDateString()}
                        </span>
                    </div>
                    <div className="queue-item-title">
                        {item.extraction_result.invoice_number?.value || item.document_id}
                    </div>
                    <div className="queue-item-meta">
                        <span>{item.extraction_result.vendor_name?.value || 'Unknown'}</span>
                        <span className={`status-badge status-${item.status}`}>
                            {item.status}
                        </span>
                    </div>
                </div>
            ))}
        </div>
    );
}

// =============================================================================
// REVIEW VIEW
// =============================================================================

function ReviewView({ onDocumentApproved }: { onDocumentApproved?: () => void }) {
    const [queueItems, setQueueItems] = useState<ReviewItem[]>([]);
    const [historyItems, setHistoryItems] = useState<ReviewItem[]>([]);
    const [viewMode, setViewMode] = useState<'queue' | 'history'>('queue');
    const [selectedItem, setSelectedItem] = useState<ReviewItem | null>(null);
    const [queueStats, setQueueStats] = useState<QueueStats | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [showMobileQueue, setShowMobileQueue] = useState(true);
    const [pendingCorrections, setPendingCorrections] = useState<FieldCorrection[]>([]);
    const [toasts, setToasts] = useState<{ id: string; type: string; message: string }[]>([]);

    const showToast = useCallback((type: string, message: string) => {
        const id = Date.now().toString();
        setToasts(prev => [...prev, { id, type, message }]);
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
    }, []);

    const fetchQueue = useCallback(async () => {
        try {
            setLoading(true);
            const [queueResponse, stats, _myStats, myItems] = await Promise.all([
                reviewApi.getQueue(),
                reviewApi.getQueueStats(),
                reviewApi.getMyStats(),
                reviewApi.getMyItems()
            ]);
            setQueueItems(queueResponse.items);
            setQueueStats(stats);

            // Restore active item if one exists and we don't have one selected
            setSelectedItem(prev => {
                if (prev) return prev;
                if (myItems.active && myItems.active.length > 0) {
                    setShowMobileQueue(false);
                    return myItems.active[0];
                }
                return null;
            });

        } catch {
            showToast('error', 'Failed to load queue data');
        } finally {
            setLoading(false);
        }
    }, [showToast]);

    const fetchHistory = useCallback(async () => {
        try {
            setLoading(true);
            const res = await reviewApi.getHistory();
            setHistoryItems(res.items);
        } catch {
            showToast('error', 'Failed to load history');
        } finally {
            setLoading(false);
        }
    }, [showToast]);

    const loadData = useCallback(() => {
        // Always fetch both to avoid flickers/state issues, but optimize later if needed
        // For now, prioritize the current view but ensure we have stats
        if (viewMode === 'queue') {
            fetchQueue();
        } else {
            fetchHistory();
        }
    }, [viewMode, fetchQueue, fetchHistory]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const handleSelectItem = useCallback(async (item: ReviewItem) => {
        if (selectedItem?.item_id === item.item_id) return;

        // If in history mode, just select the item (read-only)
        if (viewMode === 'history' || item.status === 'completed') {
            setSelectedItem(item);
            setShowMobileQueue(false);
            return;
        }

        // If clicking the ALREADY selected item, just show mobile view
        if (selectedItem?.item_id === item.item_id) {
            setShowMobileQueue(false);
            return;
        }

        try {
            const response = await reviewApi.claimItem(item.item_id);
            setSelectedItem(response.item);
            setShowMobileQueue(false); // Hide queue on mobile when item selected
        } catch (error: any) {
            showToast('error', error.message || 'Failed to claim item');
        }
    }, [selectedItem, viewMode, showToast]);

    const handleRelease = useCallback(async () => {
        if (!selectedItem) return;
        try {
            await reviewApi.releaseItem(selectedItem.item_id);
            setSelectedItem(null);
            setShowMobileQueue(true);
            loadData();
        } catch (error: any) {
            showToast('error', error.message);
        }
    }, [selectedItem, loadData, showToast]);

    const handleApprove = useCallback(async () => {
        if (!selectedItem) return;
        setActionLoading(true);
        try {
            // If there are pending corrections, submit as 'correct', otherwise 'approve'
            const decision = pendingCorrections.length > 0 ? 'correct' : 'approve';
            await reviewApi.submitReview(selectedItem.item_id, {
                decision,
                corrections: pendingCorrections
            });
            showToast('success', pendingCorrections.length > 0
                ? 'Document corrected and approved'
                : 'Document approved and moved to History');
            setPendingCorrections([]);
            setSelectedItem(null);
            setShowMobileQueue(true);
            loadData();
            // Trigger refresh of documents view count
            onDocumentApproved?.();
        } catch (error: any) {
            showToast('error', error.message);
        } finally {
            setActionLoading(false);
        }
    }, [selectedItem, showToast, loadData, pendingCorrections, onDocumentApproved]);

    const handleReleaseAll = useCallback(async () => {
        if (!window.confirm('Are you sure you want to release ALL claimed items? This will unassign everything for everyone. Use only for debugging.')) return;
        try {
            setLoading(true);
            const res = await reviewApi.debugReleaseAll();
            showToast('success', `Released ${res.released_count} items`);
            setSelectedItem(null);
            loadData();
        } catch (error: any) {
            showToast('error', error.message);
        } finally {
            setLoading(false);
        }
    }, [loadData, showToast]);

    const handleCorrect = useCallback((corrections: FieldCorrection[]) => {
        if (!selectedItem) return;

        // Update local state with corrections so user can review before approving
        const updatedExtractionResult = { ...selectedItem.extraction_result };
        corrections.forEach((c) => {
            if (updatedExtractionResult[c.field_name]) {
                updatedExtractionResult[c.field_name] = {
                    ...updatedExtractionResult[c.field_name],
                    value: c.corrected_value,
                    confidence: 1.0,
                };
            }
        });

        // Update selectedItem locally so user sees changes
        setSelectedItem({
            ...selectedItem,
            extraction_result: updatedExtractionResult,
        });

        // Accumulate corrections for when user clicks Approve
        setPendingCorrections(prev => [...prev, ...corrections]);

        showToast('success', `${corrections.length} field(s) updated. Review and click Approve when ready.`);
    }, [selectedItem, showToast]);

    const handleReject = useCallback(async (reason: string, _category: string) => {
        if (!selectedItem) return;
        setActionLoading(true);
        try {
            await reviewApi.submitReview(selectedItem.item_id, {
                decision: 'reject',
                corrections: [],
                rejection_reason: reason,
            });
            showToast('success', 'Document rejected');
            setSelectedItem(null);
            setShowMobileQueue(true);
            loadData();
        } catch (error: any) {
            showToast('error', error.message);
        } finally {
            setActionLoading(false);
        }
    }, [selectedItem, showToast, loadData]);

    const handleBack = () => {
        setSelectedItem(null);
        setShowMobileQueue(true);
    };

    return (
        <div className="review-view-container">
            {/* Queue Sidebar - Desktop always, Mobile toggle */}
            <div className={`queue-sidebar ${showMobileQueue ? 'show' : ''}`}>
                <div className="queue-header" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '0.5rem' }}>
                    <div style={{ display: 'flex', width: '100%', alignItems: 'center', justifyContent: 'space-between' }}>
                        <h2>Review Queue</h2>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                            <button className="btn btn-ghost btn-danger btn-sm" onClick={handleReleaseAll} title="Debug: Release All Claims">
                                <Icons.Unlock />
                            </button>
                            <button className="btn btn-ghost btn-sm" onClick={loadData} title="Refresh">
                                <Icons.RefreshCw />
                            </button>
                        </div>
                    </div>

                    <div className="queue-tabs">
                        <button
                            className={`queue-tab ${viewMode === 'queue' ? 'active' : ''}`}
                            onClick={() => { setViewMode('queue'); setSelectedItem(null); }}
                        >
                            Pending {queueStats && `(${queueStats.total_pending + queueStats.total_assigned})`}
                        </button>
                        <button
                            className={`queue-tab ${viewMode === 'history' ? 'active' : ''}`}
                            onClick={() => { setViewMode('history'); setSelectedItem(null); }}
                        >
                            Approved
                        </button>
                    </div>
                </div>

                {/* Quick Stats - Only for Queue Mode */}
                {viewMode === 'queue' && queueStats && (
                    <div className="queue-quick-stats">
                        <div className="quick-stat">
                            <span className="quick-stat-value">{queueStats.total_pending + queueStats.total_assigned}</span>
                            <span className="quick-stat-label">Pending</span>
                        </div>
                        <div className="quick-stat urgent">
                            <span className="quick-stat-value">{queueStats.sla_at_risk}</span>
                            <span className="quick-stat-label">At Risk</span>
                        </div>
                    </div>
                )}

                <div className="queue-items-container">
                    {loading ? (
                        <div className="loading-state">
                            <span className="spinner"></span>
                        </div>
                    ) : viewMode === 'history' ? (
                        <ApprovedDocumentsList items={historyItems} onView={handleSelectItem} />
                    ) : queueItems.length === 0 ? (
                        <div className="queue-empty">
                            <Icons.Inbox />
                            <p>No items to review</p>
                        </div>
                    ) : (
                        queueItems.map(item => (
                            <div
                                key={item.item_id}
                                className={`queue-item ${selectedItem?.item_id === item.item_id ? 'selected' : ''} ${item.is_at_risk ? 'at-risk' : ''}`}
                                onClick={() => handleSelectItem(item)}
                            >
                                <div className="queue-item-header">
                                    <span className={`priority-badge p${item.priority}`}>P{item.priority}</span>
                                    <span className={`sla-timer ${item.sla_remaining_seconds < 3600 ? 'critical' : ''}`}>
                                        <Icons.Clock /> {formatSLATime(item.sla_remaining_seconds)}
                                    </span>
                                </div>
                                <div className="queue-item-title">
                                    {item.extraction_result.invoice_number?.value || item.document_id}
                                </div>
                                <div className="queue-item-meta">
                                    <span>{item.extraction_result.vendor_name?.value || 'Unknown'}</span>
                                    <span className="doc-type">{item.document_type}</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Main Review Area */}
            <div className={`review-main ${!showMobileQueue ? 'show' : ''}`}>
                {selectedItem ? (
                    <DocumentReviewPanel
                        item={selectedItem}
                        onApprove={handleApprove}
                        onCorrect={handleCorrect}
                        onReject={handleReject}
                        onRelease={handleRelease}
                        onBack={handleBack}
                        loading={actionLoading}
                        readOnly={viewMode === 'history'}
                    />
                ) : (
                    <div className="empty-state">
                        <div className="empty-state-icon"><Icons.FileText /></div>
                        <h3>Select a document</h3>
                        <p>Choose an item from the queue to start reviewing</p>
                    </div>
                )}
            </div>

            {/* Toast notifications */}
            <div className="toast-container">
                {toasts.map(toast => (
                    <div key={toast.id} className={`toast toast-${toast.type}`}>
                        {toast.type === 'success' && <Icons.Check />}
                        {toast.type === 'error' && <Icons.X />}
                        <span>{toast.message}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// =============================================================================
// DOCUMENT REVIEW PANEL - Enhanced
// =============================================================================

function DocumentPreview({ url, type }: { url: string; type: string }) {
    const [isLoading, setIsLoading] = useState(true);
    const [hasError, setHasError] = useState(false);

    useEffect(() => {
        setIsLoading(true);
        setHasError(false);
    }, [url]);

    const handleLoad = () => {
        setIsLoading(false);
    };

    const handleError = () => {
        setIsLoading(false);
        setHasError(true);
    };

    return (
        <div className="document-preview-container">
            {isLoading && (
                <div className="preview-loader">
                    <span className="spinner"></span>
                    <p>Loading document...</p>
                </div>
            )}

            {hasError ? (
                <div className="preview-error">
                    <Icons.AlertTriangle />
                    <p>Failed to load document preview</p>
                </div>
            ) : type === 'pdf' || url.endsWith('.pdf') ? (
                <iframe
                    src={url}
                    className={`document-frame ${isLoading ? 'hidden' : ''}`}
                    onLoad={handleLoad}
                    onError={handleError}
                />
            ) : (
                <img
                    src={url}
                    className={`document-image ${isLoading ? 'hidden' : ''}`}
                    onLoad={handleLoad}
                    onError={handleError}
                    alt="Document Preview"
                />
            )}
        </div>
    );
}

interface DocumentReviewPanelProps {
    item: ReviewItem;
    onApprove: () => void;
    onCorrect: (corrections: FieldCorrection[]) => void;
    onReject: (reason: string, category: string) => void;
    onRelease: () => void;
    onBack: () => void;
    loading: boolean;
    readOnly?: boolean;
}

function DocumentReviewPanel({ item, onApprove, onCorrect, onReject, onRelease, onBack, loading, readOnly = false }: DocumentReviewPanelProps) {
    const [editMode, setEditMode] = useState(false);
    const [editedFields, setEditedFields] = useState<Record<string, any>>({});
    const [showRejectModal, setShowRejectModal] = useState(false);

    useEffect(() => {
        const fields: Record<string, any> = {};
        Object.entries(item.extraction_result).forEach(([key, field]) => {
            const val = field.value;
            // Stringify objects/arrays for editing
            fields[key] = (typeof val === 'object' && val !== null)
                ? JSON.stringify(val, null, 2)
                : val;
        });
        setEditedFields(fields);
        setEditMode(false);
    }, [item.item_id]);

    const handleSaveCorrections = () => {
        const corrections: FieldCorrection[] = [];
        Object.entries(editedFields).forEach(([fieldName, newValue]) => {
            const field = item.extraction_result[fieldName];
            const original = field?.value;
            let finalValue: any = newValue;

            // Handle empty string - treat as null for numeric fields, keep as empty for strings
            if (newValue === '' || newValue === null || newValue === undefined) {
                // If original was null/undefined too, no change
                if (original === null || original === undefined || original === '') return;
                // Otherwise, this is clearing the field
                finalValue = null;
            } else if (typeof original === 'object' && original !== null) {
                // Parse JSON for objects/arrays
                try {
                    finalValue = JSON.parse(newValue);
                    if (JSON.stringify(original) === JSON.stringify(finalValue)) return;
                } catch {
                    // Invalid JSON - skip this field
                    return;
                }
            } else if (typeof original === 'number') {
                // Convert string to number
                const num = parseFloat(newValue);
                if (isNaN(num)) {
                    finalValue = null; // Invalid number becomes null
                } else {
                    finalValue = num;
                }
                if (original === finalValue) return;
            } else if (typeof original === 'boolean') {
                // Convert string to boolean
                finalValue = newValue === 'true' || newValue === '1';
                if (original === finalValue) return;
            } else if (original === null || original === undefined) {
                // Original was null/undefined, keep new value as string or try to parse
                const num = parseFloat(newValue);
                if (!isNaN(num) && String(num) === newValue.trim()) {
                    finalValue = num;
                } else {
                    finalValue = newValue; // Keep as string
                }
            } else {
                // String comparison
                if (original === newValue) return;
            }

            corrections.push({
                field_name: fieldName,
                original_value: original,
                corrected_value: finalValue,
                correction_type: 'value_change',
            });
        });
        if (corrections.length > 0) {
            onCorrect(corrections);
        }
        setEditMode(false);
    };

    // Keyboard shortcuts
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
            switch (e.key.toLowerCase()) {
                case 'a': if (!editMode) onApprove(); break;
                case 'c': setEditMode(prev => !prev); break;
                case 'r': setShowRejectModal(true); break;
                case 'escape': setEditMode(false); setShowRejectModal(false); break;
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [editMode, onApprove]);

    return (
        <div className="document-review-panel">
            {/* Mobile Header */}
            <div className="review-mobile-header">
                <button className="btn btn-ghost" onClick={onBack}>
                    <Icons.ArrowLeft /> Back
                </button>
                <button className="btn btn-ghost" onClick={onRelease}>Release</button>
            </div>

            <div className="review-header">
                <div>
                    <h2>{item.extraction_result.invoice_number?.value || 'Document Review'}</h2>
                    <p className="text-muted">{item.document_id} • {item.document_type}</p>
                </div>
                <button className="btn btn-ghost hide-mobile" onClick={onRelease}>Release</button>
            </div>

            <div className="review-content">
                {/* Document Preview */}
                <DocumentPreview
                    url={item.document_preview_url ? `${API_BASE}${item.document_preview_url}` : ''}
                    type={item.document_type === 'invoice' && item.document_preview_url?.endsWith('.pdf') ? 'pdf' : 'image'}
                />

                {/* Extracted Fields */}
                <div className="extracted-fields-card">
                    <div className="card-header">
                        <h3>Extracted Fields</h3>
                        {editMode && <span className="edit-badge">Editing</span>}
                    </div>
                    <div className="fields-grid">
                        {Object.entries(item.extraction_result).map(([fieldName, field]) => {
                            const isLowConfidence = item.low_confidence_fields.includes(fieldName);
                            const isModified = editedFields[fieldName] !== field.value;
                            return (
                                <div key={fieldName} className={`field-card ${isLowConfidence ? 'low-confidence' : ''} ${isModified ? 'modified' : ''}`}>
                                    <div className="field-label">
                                        {fieldName.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}
                                        {field.is_locked && <Icons.Lock />}
                                    </div>
                                    {editMode && !field.is_locked ? (
                                        (String(editedFields[fieldName] ?? '').includes('\n') || (typeof item.extraction_result[fieldName]?.value === 'object' && item.extraction_result[fieldName]?.value !== null)) ? (
                                            <textarea
                                                className="field-input"
                                                rows={5}
                                                value={editedFields[fieldName] ?? ''}
                                                onChange={(e) => setEditedFields(prev => ({ ...prev, [fieldName]: e.target.value }))}
                                                style={{ fontFamily: 'monospace', fontSize: '12px' }}
                                            />
                                        ) : (
                                            <input
                                                type="text"
                                                className="field-input"
                                                value={editedFields[fieldName] ?? ''}
                                                onChange={(e) => setEditedFields(prev => ({ ...prev, [fieldName]: e.target.value }))}
                                            />
                                        )
                                    ) : (
                                        <div className="field-value">
                                            {typeof field.value === 'object' && field.value !== null
                                                ? <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: '0.85em' }}>{JSON.stringify(field.value, null, 2)}</pre>
                                                : String(field.value)
                                            }
                                        </div>
                                    )}
                                    <div className={`field-confidence ${getConfidenceClass(field.confidence)}`}>
                                        {isLowConfidence && <Icons.AlertTriangle />}
                                        {Math.round(field.confidence * 100)}%
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>

            {/* Actions */}
            {!readOnly && (
                <div className="review-actions">
                    {editMode ? (
                        <>
                            <button className="btn btn-ghost" onClick={() => setEditMode(false)}>Cancel</button>
                            <button className="btn btn-primary btn-lg" onClick={handleSaveCorrections} disabled={loading}>
                                <Icons.Check /> Save Changes
                            </button>
                        </>
                    ) : (
                        <>
                            <button className="btn btn-danger" onClick={() => setShowRejectModal(true)} disabled={loading}>
                                <Icons.X /> <span className="btn-text">Reject</span>
                            </button>
                            <button className="btn btn-warning" onClick={() => setEditMode(true)} disabled={loading}>
                                <Icons.Edit /> <span className="btn-text">Correct</span>
                            </button>
                            <button className="btn btn-success btn-lg" onClick={onApprove} disabled={loading}>
                                <Icons.Check /> <span className="btn-text">Approve</span>
                            </button>
                        </>
                    )}
                </div>
            )}

            {/* Reject Modal */}
            {showRejectModal && (
                <RejectModal
                    onConfirm={(reason, category) => {
                        onReject(reason, category);
                        setShowRejectModal(false);
                    }}
                    onCancel={() => setShowRejectModal(false)}
                />
            )}
        </div>
    );
}

// =============================================================================
// REJECT MODAL
// =============================================================================

function RejectModal({ onConfirm, onCancel }: { onConfirm: (reason: string, category: string) => void; onCancel: () => void }) {
    const [reason, setReason] = useState('');
    const [category, setCategory] = useState('other');

    return (
        <div className="modal-overlay" onClick={onCancel}>
            <div className="modal" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Reject Document</h3>
                    <button className="btn btn-ghost btn-sm" onClick={onCancel}><Icons.X /></button>
                </div>
                <div className="modal-body">
                    <div className="form-group">
                        <label>Reason</label>
                        <select className="input" value={category} onChange={e => setCategory(e.target.value)}>
                            <option value="illegible">Document illegible</option>
                            <option value="invalid">Invalid format</option>
                            <option value="duplicate">Duplicate</option>
                            <option value="other">Other</option>
                        </select>
                    </div>
                    <div className="form-group">
                        <label>Details</label>
                        <textarea className="input" rows={3} value={reason} onChange={e => setReason(e.target.value)} placeholder="Provide details..." />
                    </div>
                </div>
                <div className="modal-footer">
                    <button className="btn btn-ghost" onClick={onCancel}>Cancel</button>
                    <button className="btn btn-danger" onClick={() => onConfirm(reason, category)} disabled={!reason.trim()}>Reject</button>
                </div>
            </div>
        </div>
    );
}

// =============================================================================
// MAIN APP COMPONENT - State lifted for persistence across views
// =============================================================================

export default function App() {
    const [currentView, setCurrentView] = useState<ViewType>('documents');
    const [queueCount, setQueueCount] = useState(0);

    // Lifted state for document persistence across tab switches
    const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
    const [processedDocs, setProcessedDocs] = useState<ProcessedDocument[]>([]);
    const [docsRefreshTrigger, setDocsRefreshTrigger] = useState(0);

    useEffect(() => {
        const fetchStats = () => {
            reviewApi.getQueueStats().then(stats => {
                setQueueCount(stats.total_pending + stats.total_assigned);
                // Check for SLA breaches
                if (stats.sla_breached > 0) {
                }
            }).catch(() => { });
        };

        fetchStats();
        const interval = setInterval(fetchStats, 10000); // Check every 10s
        return () => clearInterval(interval);
    }, [currentView]);

    // SLA Warning Banner
    const [slaBreachedCount, setSlaBreachedCount] = useState(0);
    useEffect(() => {
        const checkSLA = async () => {
            try {
                const stats = await reviewApi.getQueueStats();
                setSlaBreachedCount(stats.sla_breached);
            } catch (e) { console.error(e); }
        };
        checkSLA();
        const interval = setInterval(checkSLA, 5000);
        return () => clearInterval(interval);
    }, []);



    return (
        <div className="app">
            {/* SLA Breach Banner */}
            {slaBreachedCount > 0 && (
                <div className="sla-banner" style={{ background: '#ef4444', color: 'white', padding: '0.5rem', textAlign: 'center', fontWeight: 'bold', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
                    <Icons.AlertTriangle />
                    <span>⚠️ ALERT: {slaBreachedCount} document{slaBreachedCount > 1 ? 's' : ''} have breached SLA deadlines! Please review immediately.</span>
                </div>
            )}

            {/* Top Navigation */}
            <header className="app-header">
                <div className="logo">
                    <span className="logo-icon">📄</span>
                    <span className="logo-text">DocFlow</span>
                </div>
                <nav className="main-nav">
                    <NavItem
                        icon={<Icons.Folder />}
                        label="Upload"
                        active={currentView === 'documents'}
                        onClick={() => setCurrentView('documents')}
                    />
                    <NavItem
                        icon={<Icons.Inbox />}
                        label="Review"
                        active={currentView === 'review'}
                        onClick={() => setCurrentView('review')}
                        badge={queueCount}
                    />
                    <NavItem
                        icon={<Icons.BarChart />}
                        label="Metrics"
                        active={currentView === 'metrics'}
                        onClick={() => setCurrentView('metrics')}
                    />
                </nav>
            </header>

            {/* Main Content */}
            <main className="app-main">
                {currentView === 'documents' && (
                    <DocumentsView
                        onGoToReview={() => setCurrentView('review')}
                        files={uploadedFiles}
                        setFiles={setUploadedFiles}
                        processedDocs={processedDocs}
                        setProcessedDocs={setProcessedDocs}
                        queueCount={queueCount}
                        refreshTrigger={docsRefreshTrigger}
                    />
                )}
                {currentView === 'review' && <ReviewView onDocumentApproved={() => setDocsRefreshTrigger(t => t + 1)} />}
                {currentView === 'metrics' && <MetricsView />}
            </main>
        </div>
    );
}
