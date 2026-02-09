"""
TaskStorage 使用示例

展示如何在主程序中集成 SQLite 任务存储模块。
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from src.storage import (
    get_pipeline_manager,
    PipelineTaskManager,
    TaskStatus,
)


def example_basic_usage():
    """基础用法示例"""
    print("=" * 60)
    print("基础用法示例")
    print("=" * 60)

    # 获取任务管理器（单例模式）
    manager = get_pipeline_manager()

    # 1. 创建任务
    task = manager.create_task(
        pn="CN116745575A",
        title="智能停车系统专利分析",
        auto_create_steps=True,  # 自动创建标准步骤
    )
    print(f"\n1. 任务创建成功:")
    print(f"   任务ID: {task.id}")
    print(f"   专利号: {task.pn}")
    print(f"   状态: {task.status.value}")

    # 2. 开始任务
    manager.start_task(task.id)
    print(f"\n2. 任务已开始")

    # 3. 更新进度（模拟处理过程）
    steps_progress = [
        (10, "下载专利文档", "download"),
        (25, "解析 PDF 文件", "parse"),
        (40, "提取结构化数据", "transform"),
        (55, "分析技术特征", "extract"),
        (70, "处理附图信息", "vision"),
        (85, "生成分析报告", "generate"),
    ]

    for progress, step_name, step_key in steps_progress:
        manager.update_progress(
            task.id,
            progress=progress,
            step=step_name,
            step_status="running" if progress < 85 else "completed",
        )
        print(f"   进度: {progress}% - {step_name}")

    # 4. 完成任务
    manager.complete_task(
        task.id,
        output_files={
            "pdf": f"output/{task.pn}/{task.pn}.pdf",
            "md": f"output/{task.pn}/{task.pn}.md",
        }
    )
    print(f"\n4. 任务完成!")

    # 5. 查询任务
    retrieved_task = manager.get_task(task.id, include_steps=True)
    print(f"\n5. 查询任务:")
    print(f"   标题: {retrieved_task.title}")
    print(f"   总进度: {retrieved_task.progress}%")
    print(f"   步骤数: {len(retrieved_task.steps)}")

    return task.id


def example_task_queries():
    """任务查询示例"""
    print("\n" + "=" * 60)
    print("任务查询示例")
    print("=" * 60)

    manager = get_pipeline_manager()

    # 列出最近的5个任务
    print("\n最近5个任务:")
    tasks = manager.list_tasks(limit=5)
    for t in tasks:
        print(f"   [{t.status.value:12}] {t.pn or 'N/A':20} {t.title[:30]}")

    # 按状态筛选
    print("\n进行中的任务:")
    running = manager.list_tasks(status=TaskStatus.PROCESSING)
    for t in running:
        print(f"   {t.id}: {t.current_step} ({t.progress}%)")

    # 统计信息
    print("\n任务统计:")
    stats = manager.storage.get_statistics()
    print(f"   总任务数: {stats['total']}")
    print(f"   按状态: {stats['by_status']}")
    print(f"   今日创建: {stats['today_created']}")
    print(f"   平均处理时间: {stats['avg_duration_minutes']:.1f} 分钟")


def example_error_handling():
    """错误处理示例"""
    print("\n" + "=" * 60)
    print("错误处理示例")
    print("=" * 60)

    manager = get_pipeline_manager()

    # 创建一个会失败的任务
    task = manager.create_task(pn="CN123456789", title="测试失败任务")
    print(f"\n创建任务: {task.id}")

    # 模拟开始处理
    manager.start_task(task.id)
    print("任务开始处理...")

    # 模拟处理到一半出错了
    manager.update_progress(task.id, progress=50, step="解析PDF")
    print("处理到 50% 时出错...")

    # 标记失败
    manager.fail_task(task.id, "PDF解析失败: 文件格式不兼容")
    print(f"任务已标记为失败")

    # 查询失败原因
    failed_task = manager.get_task(task.id)
    print(f"\n失败原因: {failed_task.error_message}")


def example_integration_with_pipeline():
    """
    与 PatentPipeline 集成的示例

    展示如何在 main.py 中使用 TaskStorage
    """
    print("\n" + "=" * 60)
    print("与 Pipeline 集成示例 (代码片段)")
    print("=" * 60)

    code_example = '''
# main.py 集成示例

from src.storage import get_pipeline_manager, TaskStatus

class PatentPipeline:
    def __init__(self, pn: str, task_id: str = None):
        self.pn = pn
        self.paths = settings.get_project_paths(pn)

        # 初始化任务管理器
        self.task_manager = get_pipeline_manager()

        # 如果提供了 task_id，使用现有任务；否则创建新任务
        if task_id:
            self.task = self.task_manager.get_task(task_id)
        else:
            self.task = self.task_manager.create_task(
                pn=pn,
                title=f"专利分析 - {pn}",
            )

    def run(self) -> dict:
        """执行处理流程"""
        try:
            # 标记任务开始
            self.task_manager.start_task(self.task.id)

            # Step 0: 下载
            self._update_step("下载专利文档", 5)
            self._step_download()

            # Step 1: 解析
            self._update_step("解析 PDF 文件", 15)
            if not self.paths["raw_md"].exists():
                PDFParser.parse(...)

            # Step 2: 转换
            self._update_step("专利结构化转换", 30)
            # ...

            # Step 3: 知识提取
            self._update_step("知识提取", 45)
            # ...

            # ... 更多步骤 ...

            # 标记任务完成
            self.task_manager.complete_task(
                self.task.id,
                output_files={
                    "pdf": str(self.paths["final_pdf"]),
                    "md": str(self.paths["final_md"]),
                }
            )

            return {"status": "success", "task_id": self.task.id}

        except Exception as e:
            logger.exception(f"[{self.pn}] Pipeline failed: {str(e)}")
            # 标记任务失败
            self.task_manager.fail_task(self.task.id, str(e))
            return {"status": "failed", "error": str(e)}

    def _update_step(self, step_name: str, progress: int):
        """辅助方法：更新步骤进度"""
        self.task_manager.update_progress(
            self.task.id,
            progress=progress,
            step=step_name,
            step_status="running",
        )
'''
    print(code_example)


def main():
    """运行所有示例"""
    print("=" * 60)
    print("TaskStorage 使用示例")
    print("=" * 60)

    # 基础用法
    task_id = example_basic_usage()

    # 查询示例
    example_task_queries()

    # 错误处理
    example_error_handling()

    # 集成示例
    example_integration_with_pipeline()

    # 清理示例数据
    print("\n" + "=" * 60)
    print("清理示例数据")
    print("=" * 60)

    manager = get_pipeline_manager()

    # 删除测试任务
    test_tasks = manager.list_tasks(pn="CN123456789")
    for t in test_tasks:
        manager.delete_task(t.id, delete_output=False)
        print(f"  已删除测试任务: {t.id}")

    print("\n示例运行完成！")


if __name__ == "__main__":
    main()
