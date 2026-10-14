FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE constraints-server.txt ./
COPY src ./src
# Pinned set for the server image only (pyproject ranges stay loose for PyPI).
RUN pip install --no-cache-dir -c constraints-server.txt ".[server]"
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "advisorfinder_mcp.server"]
