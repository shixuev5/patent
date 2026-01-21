from abc import ABC, abstractmethod
from typing import List, Dict, Optional

class BaseSearchClient(ABC):
    """
    所有专利检索平台的抽象基类
    """

    @abstractmethod
    def search(self, query: str, db: str = "ALL", limit: int = 50) -> List[Dict]:
        """
        执行检索
        :param query: 标准化的检索式
        :param db: 目标数据库 (对于智慧芽可能是 'patents')
        :param limit: 数量限制
        :return: 统一格式的专利列表
                 [{'id': '...', 'title': '...', 'abstract': '...', 'cpc': [...], ...}]
        """
        pass
    
    @abstractmethod
    def search_semantic(self, text: str, to_date: str = "", limit: int = 50) -> List[Dict]:
        """
        语义/自然语言检索
        :param text: 自然语言文本
        :param to_date: 截止日期 (YYYYMMDD)，用于查新
        :param limit: 数量限制
        """
        pass

    @abstractmethod
    def get_citations(self, patent_ids: List[str], direction: str = 'both') -> List[Dict]:
        """
        获取引证文献
        """
        pass