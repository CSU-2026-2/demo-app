FROM python:3.12-slim
WORKDIR /workspace
COPY requirements-test.txt ./
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir -r requirements-test.txt
COPY app app
COPY common common
COPY worker worker
COPY tests tests
ENV PYTHONPATH=/workspace:/workspace/app
CMD ["pytest", "-q", "tests"]
