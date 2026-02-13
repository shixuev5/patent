# 使用 Python 3.11 官方镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js（Playwright 需要）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# 复制项目文件
COPY . .

# 安装 Python 依赖（使用 uv）
RUN pip install --upgrade pip uv && \
    uv sync --frozen --no-dev

# 安装 Playwright 浏览器（使用 uv 虚拟环境）
RUN uv run playwright install chromium

# 创建必要的目录
RUN mkdir -p output uploads data assets

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV APP_STORAGE_ROOT=/app
ENV TASK_STORAGE_BACKEND=d1
ENV PDF_PARSER=local
ENV OCR_ENGINE=local
ENV MINERU_MODEL_SOURCE=modelscope

# 暴露端口
EXPOSE 7860

# 启动应用（使用 uv）
CMD ["uv", "run", "python", "api.py"]
