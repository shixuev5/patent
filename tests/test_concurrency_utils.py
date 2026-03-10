from concurrent.futures import ThreadPoolExecutor

from agents.common.utils.concurrency import submit_with_current_context
from backend import task_usage_tracking


def test_submit_with_current_context_propagates_task_usage_context():
    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-ctx-submit",
        owner_id="authing:user-submit",
        task_type="patent_analysis",
    )

    with task_usage_tracking.task_usage_collection(collector):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = submit_with_current_context(
                executor,
                task_usage_tracking.get_current_task_usage_context,
            )
            context = future.result()

    assert context["task_id"] == "task-ctx-submit"
    assert context["task_type"] == "patent_analysis"
