from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseSearchClient(ABC):
    """
    所有专利检索平台的抽象基类
    """

    @abstractmethod
    def search(self, query: str, limit: int = 50) -> Dict[str, Any]:
        """
        执行检索
        :param query: 标准化的检索式
        :param limit: 数量限制
        :return: 包含总数和结果列表的字典
                 {
                    "total": 1000,
                    "results": [{'id': '...', ...}]
                 }
        """
        pass
    
    @abstractmethod
    def search_semantic(self, text: str, to_date: str = "", limit: int = 50) -> Dict[str, Any]:
        """
        语义/自然语言检索
        :param text: 自然语言文本
        :param to_date: 截止日期 (YYYYMMDD)，用于查新
        :param limit: 数量限制
        :return: { "total": int, "results": List[Dict] }
        """
        pass
