from abc import ABC, abstractmethod
from pathlib import Path


class BaseParser(ABC):
    """
    文档解析器抽象基类
    所有文档解析器都应该继承自这个基类
    """

    @abstractmethod
    def parse(self, file_path: Path, output_dir: Path) -> Path:
        """
        解析文档文件

        Args:
            file_path: 要解析的文件路径
            output_dir: 输出目录

        Returns:
            解析后生成的主要文件路径（通常是 markdown 文件）
        """
        pass
