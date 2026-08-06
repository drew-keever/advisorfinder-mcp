FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "advisorfinder_mcp.server"]
