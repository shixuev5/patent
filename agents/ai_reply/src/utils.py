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


_PATENT_PUBLICATION_PATTERNS = [
    r"^CN\d{6,}[A-Z]\d*$",  # 中国公开号/公告号
    r"^US\d{6,}[A-Z]\d*$",  # 美国公开/授权号
    r"^WO\d{6,}[A-Z]?\d*$",  # PCT/WO 公开号
    r"^EP\d{6,}[A-Z]\d*$",  # 欧洲公开/授权号
    r"^JP\d{6,}[A-Z]?\d*$",  # 日本现代格式
    r"^JP[HS]\d{4,}[A-Z]?\d*$",  # 日本旧纪年格式，如 JPH115534A / JPS62123456A
    r"^KR\d{6,}[A-Z]?\d*$",  # 韩国公开/授权号
    r"^TW\d{6,}[A-Z]?\d*$",  # 台湾公开/授权号
    r"^DE\d{6,}[A-Z]\d*$",  # 德国公开/授权号
    r"^FR\d{6,}[A-Z]\d*$",  # 法国公开/授权号
    r"^GB\d{6,}[A-Z]\d*$",  # 英国公开/授权号
    r"^AU\d{6,}[A-Z]\d*$",  # 澳大利亚公开/授权号
    r"^CA\d{6,}[A-Z]\d*$",  # 加拿大公开/授权号
]

_PATENT_APPLICATION_PATTERNS = [
    r"^\d{4}\d{7,8}\.\d$",  # 中国申请号，如 202211411308.6 / 202310001234.5
    r"^PCT[A-Z]{2}\d{4}\d{5,}$",  # PCT 申请号简化归一化格式，如 PCTCN2024123456
]


def normalize_patent_identifier(value: str) -> str:
    """统一专利标识符的常见分隔符与大小写。"""
    return re.sub(r"[\s\-/]+", "", str(value or "").strip()).upper()


def is_patent_document(document_number: str) -> bool:
    """判断是否为专利公开号/公告号等文献号。"""
    normalized = normalize_patent_identifier(document_number)
    if not normalized:
        return False

    for pattern in _PATENT_PUBLICATION_PATTERNS:
        if re.match(pattern, normalized, re.IGNORECASE):
            return True

    return False


def is_patent_application_number(value: str) -> bool:
    """判断是否为常见专利申请号。"""
    normalized = normalize_patent_identifier(value)
    if not normalized:
        return False

    for pattern in _PATENT_APPLICATION_PATTERNS:
        if re.match(pattern, normalized, re.IGNORECASE):
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
