"""
Search clients for patent document retrieval.
This module provides interfaces and implementations for various patent search APIs.
"""

from patent_agents.common.search_clients.base import BaseSearchClient
from patent_agents.common.search_clients.factory import SearchClientFactory
from patent_agents.common.search_clients.zhihuiya import ZhihuiyaClient

__all__ = ["BaseSearchClient", "SearchClientFactory", "ZhihuiyaClient"]