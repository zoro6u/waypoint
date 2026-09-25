FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==8.*

# no other packages, no app code copied in — everything the sandbox
# executes is bind-mounted read-only at container run time.
