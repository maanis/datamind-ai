import { useMutation } from '@tanstack/react-query';

interface IngestResponse {
    meeting_id: string;
    num_vectors: number;
    num_documents?: number;
}

export const callIngestAPI = async (text: string) => {
    const userStr = localStorage.getItem('user');
    if (!userStr) {
        throw new Error('User not found in localStorage');
    }
    const user = JSON.parse(userStr);

    const response = await fetch('http://localhost:3000/api/robot/ingest', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({ text }),
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const errorMessage = errorData.message || `HTTP ${response.status}`;
        const errorDetail = errorData.error?.detail || response.statusText;
        throw new Error(`${errorMessage}: ${errorDetail}`);
    }

    return response.json() as Promise<IngestResponse>;
};

export const useIngest = () => {
    return useMutation({
        mutationFn: callIngestAPI,
    });
};