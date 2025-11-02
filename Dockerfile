FROM python:3.11-slim

# Install system deps needed by Playwright/Chromium and common tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates curl gnupg unzip build-essential \
    libnss3 libatk1.0-0 libx11-xcb1 libgbm1 libxcomposite1 libxdamage1 libxrandr2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Install playwright browsers (best-effort; may enlarge image)
# If you don't need Playwright, you can remove this RUN line to keep image smaller.
RUN playwright install chromium

# Copy app files
COPY . /app

ENV PYTHONUNBUFFERED=1

# Command to run the bot
CMD ["python", "bot.py"]
