/**
 * API client for the Review Queue service
 */

import type {
    ReviewItem,
    QueueStats,
    ReviewerStats,
    QueueResponse,
    ClaimResponse,
    SubmitReviewRequest,
    SubmitReviewResponse,
} from '../types';

const API_BASE = 'http://127.0.0.1:8000/api/v1/review';

class ReviewApi {
    private async request<T>(
        endpoint: string,
        options: RequestInit = {}
    ): Promise<T> {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Request failed: ${response.status}`);
        }

        return response.json();
    }

    async getQueue(params?: {
        status?: string;
        priority?: number;
        document_type?: string;
        sort?: string;
        page?: number;
        limit?: number;
    }): Promise<QueueResponse> {
        try {
            const searchParams = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([key, value]) => {
                    if (value !== undefined) {
                        searchParams.set(key, String(value));
                    }
                });
            }
            return await this.request<QueueResponse>(`/queue?${searchParams}`);
        } catch {
            // Return empty queue if API not available
            return { items: [], total: 0, page: 1, limit: 20, has_more: false };
        }
    }

    async getQueueStats(): Promise<QueueStats> {
        try {
            return await this.request<QueueStats>('/queue/stats');
        } catch {
            return {
                total_pending: 0,
                total_assigned: 0,
                by_priority: {},
                by_document_type: {},
                sla_at_risk: 0,
                sla_breached: 0,
                avg_wait_time_minutes: 0,
            };
        }
    }

    async getItem(itemId: string): Promise<ReviewItem> {
        return this.request<ReviewItem>(`/items/${itemId}`);
    }

    async claimItem(itemId: string): Promise<ClaimResponse> {
        return this.request<ClaimResponse>(`/items/${itemId}/claim`, {
            method: 'POST',
        });
    }

    async releaseItem(itemId: string, reason?: string): Promise<{ success: boolean }> {
        return this.request<{ success: boolean }>(`/items/${itemId}/release`, {
            method: 'POST',
            body: JSON.stringify({ reason }),
        });
    }

    async submitReview(
        itemId: string,
        data: SubmitReviewRequest
    ): Promise<SubmitReviewResponse> {
        return this.request<SubmitReviewResponse>(`/items/${itemId}/submit`, {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async escalateItem(itemId: string, reason: string): Promise<{ success: boolean }> {
        return this.request<{ success: boolean }>(`/items/${itemId}/escalate`, {
            method: 'POST',
            body: JSON.stringify({ reason }),
        });
    }

    async getMyStats(): Promise<ReviewerStats> {
        try {
            return await this.request<ReviewerStats>('/my-stats');
        } catch {
            return {
                reviewer_id: 'anonymous',
                active_items_count: 0,
                completed_today: 0,
                approved_today: 0,
                corrected_today: 0,
                rejected_today: 0,
                avg_review_time_seconds: 0,
            };
        }
    }

    async getMyItems(): Promise<{ active: ReviewItem[]; completed_today: ReviewItem[] }> {
        try {
            return await this.request<{ active: ReviewItem[]; completed_today: ReviewItem[] }>('/my-items');
        } catch (error) {
            console.error('Failed to fetch my items:', error);
            return { active: [], completed_today: [] };
        }
    }
}

export const reviewApi = new ReviewApi();
