FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv pip install --system fastapi uvicorn[standard] python-multipart pydantic-settings openai python-docx openpyxl xlrd tenacity aiofiles langchain langchain-openai docling
COPY app ./app
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
