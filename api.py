"""
专利分析系统 FastAPI 后端（简化版）
适用于快速启动和测试
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

# ==================== 数据模型 ====================

class TaskStatus(BaseModel):
    """任务状态模型"""
    id: str
    status: str
    progress: int
    step: str
    download_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str

class TaskResponse(BaseModel):
    """任务创建响应"""
    taskId: str
    status: str
    message: str

# ==================== 任务管理器 ====================

class TaskManager:
    """任务管理器 - 内存存储"""
    
    def __init__(self):
        self.tasks: Dict[str, Dict] = {}
    
    def create_task(self, patent_number: str = None, filename: str = None) -> str:
        """创建新任务"""
        task_id = str(uuid.uuid4())[:8]
        title = patent_number or filename or "未命名任务"
        
        self.tasks[task_id] = {
            "id": task_id,
            "title": title,
            "patent_number": patent_number,
            "filename": filename,
            "status": "pending",
            "progress": 0,
            "step": "等待处理",
            "download_url": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        
        return task_id
    
    def get_task(self, task_id: str) -> Optional[Dict]:
        """获取任务信息"""
        return self.tasks.get(task_id)
    
    def update_task(self, task_id: str, **kwargs):
        """更新任务状态"""
        if task_id in self.tasks:
            self.tasks[task_id].update(kwargs)
            self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
    
    def delete_task(self, task_id: str):
        """删除任务"""
        if task_id in self.tasks:
            del self.tasks[task_id]

# 全局任务管理器实例
task_manager = TaskManager()

# ==================== 模拟处理流程 ====================

async def process_patent_task(task_id: str, patent_number: Optional[str], file_path: Optional[Path]):
    """模拟专利分析处理流程"""
    try:
        print(f"[Task {task_id}] 开始处理任务")
        task_manager.update_task(
            task_id,
            status="processing",
            progress=5,
            step="初始化..."
        )
        
        await asyncio.sleep(1)
        
        # 模拟处理步骤
        steps = [
            (10, "下载专利文档..."),
            (25, "解析 PDF 文件..."),
            (40, "提取结构化数据..."),
            (55, "分析技术特征..."),
            (70, "处理附图信息..."),
            (85, "生成分析报告..."),
        ]
        
        for progress, step in steps:
            task_manager.update_task(
                task_id,
                progress=progress,
                step=step
            )
            await asyncio.sleep(1.5)
        
        # 完成
        task_manager.update_task(
            task_id,
            status="completed",
            progress=100,
            step="分析完成",
            download_url=f"/api/tasks/{task_id}/download"
        )
        
        print(f"[Task {task_id}] 任务完成")
        
    except asyncio.CancelledError:
        print(f"[Task {task_id}] 任务被取消")
        task_manager.update_task(
            task_id,
            status="error",
            error="任务被用户取消"
        )
        raise
    except Exception as e:
        print(f"[Task {task_id}] 处理失败: {str(e)}")
        task_manager.update_task(
            task_id,
            status="error",
            error=str(e)
        )

# ==================== FastAPI 应用 ====================

app = FastAPI(
    title="专利智能分析平台 API",
    description="提供专利分析任务创建、进度追踪和结果下载功能（简化版）",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== API 路由 ====================

@app.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    background_tasks: BackgroundTasks,
    patentNumber: str = Form(None),
    file: UploadFile = File(None)
):
    """创建新的专利分析任务"""
    if not patentNumber and not file:
        raise HTTPException(
            status_code=400,
            detail="必须提供专利号或上传PDF文件"
        )
    
    # 创建任务
    task_id = task_manager.create_task(
        patent_number=patentNumber,
        filename=file.filename if file else None
    )
    
    # 保存上传的文件（如果有）
    file_path = None
    if file:
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        file_path = upload_dir / f"{task_id}_{file.filename}"
        
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
    
    # 启动后台任务处理
    asyncio.create_task(
        process_patent_task(task_id, patentNumber, file_path)
    )
    
    return TaskResponse(
        taskId=task_id,
        status="pending",
        message="任务已创建并开始处理"
    )


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return task


@app.get("/api/tasks/{task_id}/progress")
async def get_task_progress(task_id: str):
    """
    获取任务进度（SSE 流）
    
    返回 Server-Sent Events 流，实时推送任务进度更新
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    async def event_stream():
        """SSE 事件流生成器"""
        last_status = None
        last_progress = -1
        
        while True:
            try:
                # 获取最新任务状态
                current_task = task_manager.get_task(task_id)
                if not current_task:
                    yield f"data: {json.dumps({'status': 'error', 'error': '任务已删除'})}\n\n"
                    break
                
                # 检查状态是否有变化
                current_status = current_task.get("status")
                current_progress = current_task.get("progress", 0)
                
                if current_status != last_status or current_progress != last_progress:
                    # 构建进度数据
                    progress_data = {
                        "progress": current_progress,
                        "step": current_task.get("step", ""),
                        "status": current_status,
                    }
                    
                    # 添加额外信息
                    if current_status == "completed":
                        progress_data["downloadUrl"] = f"/api/tasks/{task_id}/download"
                    elif current_status == "error":
                        progress_data["error"] = current_task.get("error", "未知错误")
                    
                    # 发送 SSE 事件
                    yield f"data: {json.dumps(progress_data)}\n\n"
                    
                    last_status = current_status
                    last_progress = current_progress
                
                # 检查是否完成或出错
                if current_status in ["completed", "error"]:
                    break
                
                # 等待一段时间后再次检查
                await asyncio.sleep(0.5)
                
            except asyncio.CancelledError:
                print(f"[SSE] Task {task_id} stream cancelled")
                break
            except Exception as e:
                print(f"[SSE] Error streaming task {task_id}: {str(e)}")
                yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                break
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/tasks/{task_id}/download")
async def download_result(task_id: str):
    """下载任务结果报告"""
    from fastapi.responses import FileResponse
    import os
    
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")
    
    # 构建报告文件路径（假设报告保存在 output 目录）
    patent_number = task.get("patent_number", task_id)
    pdf_path = Path(f"output/{patent_number}/{patent_number}.pdf")
    
    # 如果特定专利文件不存在，尝试使用默认报告
    if not pdf_path.exists():
        # 返回示例：可以创建一个默认的示例报告或返回错误
        return JSONResponse(
            status_code=404,
            content={
                "error": "报告文件尚未生成",
                "message": f"找不到报告文件: {pdf_path}",
                "task_id": task_id,
                "suggestion": "请稍后再试或联系管理员"
            }
        )
    
    # 返回 PDF 文件
    return FileResponse(
        path=str(pdf_path),
        filename=f"专利分析报告_{patent_number}.pdf",
        media_type="application/pdf"
    )


@app.get("/api/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "active_tasks": len([t for t in task_manager.tasks.values() if t.get("status") == "processing"])
    }


# ==================== 主入口 ====================

if __name__ == "__main__":
    import uvicorn
    
    # 确保上传目录存在
    Path("uploads").mkdir(exist_ok=True)
    
    # 启动服务
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
