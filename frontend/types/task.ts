export interface Task {
  id: string;
  backendId?: string;
  title: string;
  type: 'patent' | 'file';
  status: 'pending' | 'processing' | 'completed' | 'error' | 'failed' | 'cancelled';
  progress: number;
  currentStep: string;
  downloadUrl?: string;
  error?: string;
  createdAt: number;
  updatedAt: number;
}

export interface CreateTaskInput {
  patentNumber?: string;
  file?: File;
}

export interface TaskProgress {
  progress: number;
  step: string;
  status: string;
  downloadUrl?: string;
  error?: string;
}

export type TaskStatus = Task['status'];
