FROM python:3.12-slim

WORKDIR /app

COPY advisorfinder_mcp.py .
RUN pip install --no-cache-dir "fastmcp>=3.4,<4.0"

EXPOSE 8080

CMD ["python", "advisorfinder_mcp.py", "--http"]
