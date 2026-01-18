/**
 * TypeScript type definitions for the Review Dashboard
 */

export interface ExtractedField {
    value: any;
    confidence: number;
    is_locked: boolean;
}

export interface ReviewItem {
    item_id: string;
    document_id: string;
    workflow_id: string;
    extraction_result: Record<string, ExtractedField>;
    document_preview_url: string;
    low_confidence_fields: string[];
    priority: number;
    priority_factors: Record<string, number>;
    created_at: string;
    sla_deadline: string;
    sla_remaining_seconds: number;
    status: ReviewStatus;
    assigned_to: string | null;
    assigned_at: string | null;
    review_attempts: number;
    document_type: string;
    source_system: string;
    is_at_risk: boolean;
}

export type ReviewStatus =
    | 'pending'
    | 'assigned'
    | 'in_review'
    | 'completed'
    | 'escalated'
    | 'expired';

export type ReviewDecision = 'approve' | 'correct' | 'reject';

export type RejectionCategory =
    | 'illegible'
    | 'invalid'
    | 'duplicate'
    | 'incomplete'
    | 'other';

export interface FieldCorrection {
    field_name: string;
    original_value: any;
    corrected_value: any;
    correction_type: string;
    reviewer_notes?: string;
}

export interface ReviewResult {
    result_id: string;
    item_id: string;
    document_id: string;
    reviewer_id: string;
    decision: ReviewDecision;
    corrections: FieldCorrection[];
    rejection_reason?: string;
    rejection_category?: RejectionCategory;
    review_started_at: string;
    review_completed_at: string;
    review_duration_seconds: number;
}

export interface QueueStats {
    total_pending: number;
    total_assigned: number;
    by_priority: Record<number, number>;
    by_document_type: Record<string, number>;
    sla_at_risk: number;
    sla_breached: number;
    avg_wait_time_minutes: number;
}

export interface ReviewerStats {
    reviewer_id: string;
    active_items_count: number;
    completed_today: number;
    approved_today: number;
    corrected_today: number;
    rejected_today: number;
    avg_review_time_seconds: number;
}

export interface QueueResponse {
    items: ReviewItem[];
    total: number;
    page: number;
    limit: number;
    has_more: boolean;
}

export interface ClaimResponse {
    success: boolean;
    item: ReviewItem;
    expires_at: string;
}

export interface SubmitReviewRequest {
    decision: ReviewDecision;
    corrections: FieldCorrection[];
    rejection_reason?: string;
    rejection_category?: RejectionCategory;
}

export interface SubmitReviewResponse {
    success: boolean;
    result_id: string;
    decision: string;
    corrections_count: number;
    duration_seconds: number;
}
