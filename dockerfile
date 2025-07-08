# 1. استفاده از ایمیج رسمی پایتون
FROM python:3.10-slim

# 2. تنظیم متغیر محیطی برای جلوگیری از بافر لاگ‌ها
ENV PYTHONUNBUFFERED=1

# 3. نصب کتابخانه‌های موردنیاز سیستم
RUN apt-get update && apt-get install -y gcc libsqlite3-dev && apt-get clean

# 4. ساخت دایرکتوری کاری
WORKDIR /app

# 5. کپی فایل‌ها
COPY . /app

# 6. نصب کتابخانه‌های پایتون
RUN pip install --no-cache-dir -r requirements.txt

# 7. اجرای برنامه با gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
