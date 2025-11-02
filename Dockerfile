# استخدام صورة بايثون أساسية مدمجة مع Playwright
FROM mcr.microsoft.com/playwright/python:v1.46.0-buster-slim

# تعيين دليل العمل
WORKDIR /app

# نسخ ملف متطلبات المكتبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود المتبقي (بما في ذلك main.py)
COPY . .

# أمر التشغيل النهائي
CMD ["python", "main.py"]
