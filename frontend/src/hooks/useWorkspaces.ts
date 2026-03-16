import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

interface Workspace {
  workspaceId: string;
  workspaceName: string;
}

interface CreateWorkspaceRequest {
  workspaceName: string;
}

interface CreateWorkspaceResponse {
  workspace: Workspace;
}

// API functions
const fetchWorkspaces = async (): Promise<Workspace[]> => {
  const response = await fetch('http://localhost:3000/api/workspace', {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem('token')}`,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || `HTTP ${response.status}`);
  }

  const data = await response.json();
  const workspaces = data.workspaces || [];

  // Validate and sanitize workspace data
  return workspaces.map((ws: any) => ({
    workspaceId: ws._id || ws.workspaceId || ws.id || 'unknown',
    workspaceName: ws.workspaceName || ws.name || 'Unnamed Workspace'
  }));
};

const createWorkspace = async (workspaceData: CreateWorkspaceRequest): Promise<CreateWorkspaceResponse> => {
  const response = await fetch('http://localhost:3000/api/workspace', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${localStorage.getItem('token')}`,
    },
    body: JSON.stringify(workspaceData),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || `HTTP ${response.status}`);
  }

  const data = await response.json();
  // Ensure the response has the correct structure
  return {
    workspace: {
      workspaceId: data.workspace?._id || data.workspace?.workspaceId || data.workspace?.id || 'unknown',
      workspaceName: data.workspace?.workspaceName || data.workspace?.name || workspaceData.workspaceName
    }
  };
};

// API functions for documents
const fetchDocuments = async (workspaceId: string): Promise<any[]> => {
  const response = await fetch(`http://localhost:3000/api/workspace/${workspaceId}/documents`, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem('token')}`,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.documents || [];
};

// React Query hooks
export const useWorkspaces = () => {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: fetchWorkspaces,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });
};

export const useCreateWorkspace = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: createWorkspace,
    onSuccess: (data) => {
      // Invalidate and refetch workspaces
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      return data;
    },
  });
};

export const useDocuments = (workspaceId: string) => {
  return useQuery({
    queryKey: ['documents', workspaceId],
    queryFn: () => fetchDocuments(workspaceId),
    enabled: !!workspaceId,
  });
};