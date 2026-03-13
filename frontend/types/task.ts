export type TaskType = 'patent_analysis' | 'ai_review' | 'ai_reply';

export interface Task {
  id: string;
  backendId?: string;
  title: string;
  taskType: TaskType;
  pn?: string;
  status: 'pending' | 'processing' | 'completed' | 'error' | 'failed' | 'cancelled';
  progress: number;
  currentStep: string;
  downloadUrl?: string;
  error?: string;
  createdAt: number;
  updatedAt: number;
}

export type CreateTaskInput =
  | {
      taskType: 'patent_analysis';
      patentNumber?: string;
      file?: File;
    }
  | {
      taskType: 'ai_review';
      patentNumber?: string;
      file?: File;
    }
  | {
      taskType: 'ai_reply';
      officeActionFile: File;
      responseFile: File;
      claimsFile?: File;
      comparisonDocs?: File[];
    };

export interface TaskProgress {
  taskType?: TaskType;
  progress: number;
  step: string;
  status: string;
  pn?: string;
  downloadUrl?: string;
  error?: string;
}

export type TaskStatus = Task['status'];
