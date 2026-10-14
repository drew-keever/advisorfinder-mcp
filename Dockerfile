FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
# fastmcp checks PyPI for a newer version (sync HTTP call) before uvicorn binds
# the port; the container is ephemeral, so it would run on every boot.
ENV FASTMCP_CHECK_FOR_UPDATES=off
WORKDIR /app
COPY pyproject.toml README.md LICENSE constraints-server.txt ./
COPY src ./src
# Pinned set for the server image only (pyproject ranges stay loose for PyPI).
RUN pip install --no-cache-dir -c constraints-server.txt ".[server]"
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "advisorfinder_mcp.server"]
