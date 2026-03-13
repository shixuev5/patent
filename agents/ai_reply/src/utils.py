"""
工具函数
包含文件处理、错误处理和缓存机制等辅助功能
"""

import re
import hashlib
from pathlib import Path
from loguru import logger
from agents.common.utils.cache import StepCache
from agents.ai_reply.src.state import WorkflowConfig


def is_patent_document(document_number: str) -> bool:
    """判断是否为专利文献"""
    patent_patterns = [
        r"^CN\d+[A-Z]?\d*$",  # 中国专利 (如 CN110000000A, CN202210000001.1)
        r"^US\d+[A-Z]?\d*$",  # 美国专利 (如 US1234567B1, US20200000001A1)
        r"^WO\d+[A-Z]?\d*$",  # 国际专利 (如 WO2020000001A1, WO2020000001)
        r"^EP\d+[A-Z]?\d*$",  # 欧洲专利 (如 EP1234567B1, EP1234567)
        r"^JP\d+[A-Z]?\d*$",  # 日本专利 (如 JP2020000001A, JP2020000001)
        r"^KR\d+[A-Z]?\d*$",  # 韩国专利 (如 KR2020000001A, KR2020000001)
    ]

    for pattern in patent_patterns:
        if re.match(pattern, document_number.strip(), re.IGNORECASE):
            return True

    return False


def get_file_hash(file_path: str) -> str:
    """
    计算文件哈希值

    Args:
        file_path: 文件路径

    Returns:
        文件哈希值
    """
    hash_obj = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def validate_file_type(file_path: str, allowed_types: list) -> bool:
    """
    验证文件类型

    Args:
        file_path: 文件路径
        allowed_types: 允许的文件类型列表

    Returns:
        是否为允许的文件类型
    """
    file_extension = Path(file_path).suffix.lower()
    return file_extension in allowed_types


def ensure_dir_exists(dir_path: str):
    """
    确保目录存在

    Args:
        dir_path: 目录路径
    """
    dir_path = Path(dir_path)
    if not dir_path.exists():
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"创建目录: {dir_path}")


def remove_temp_files(dir_path: str):
    """
    删除临时文件

    Args:
        dir_path: 临时文件目录
    """
    try:
        dir_path = Path(dir_path)
        if dir_path.exists():
            import shutil

            shutil.rmtree(dir_path)
            logger.info(f"删除临时文件目录: {dir_path}")
    except Exception as e:
        logger.warning(f"删除临时文件失败: {e}")


def get_node_cache(config: WorkflowConfig, node_name: str) -> StepCache:
    """
    获取节点缓存管理器

    Args:
        config: 工作流配置
        node_name: 节点名称

    Returns:
        节点缓存管理器实例
    """
    cache_file = Path(config.cache_dir) / f"{node_name}_cache.json"
    logger.debug(f"节点 {node_name} 缓存文件路径: {cache_file}")
    return StepCache(cache_file)
