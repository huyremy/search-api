# Sử dụng image Python slim làm nền tảng
FROM python:3.11-slim

# Cài đặt các thư viện hệ thống cần thiết cho Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    libnss3 \
    libatk-bridge2.0-0 \
    libxkbcommon0 \
    libgtk-3-0 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc trong container
WORKDIR /app

# Sao chép file requirements và cài đặt Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Quan trọng: Cài đặt trình duyệt Chromium cho Playwright
# Biến môi trường này đảm bảo Playwright tìm đúng đường dẫn browser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps chromium

# Sao chép toàn bộ mã nguồn ứng dụng vào container
COPY . .

# Cổng Render sẽ gán tự động qua biến môi trường $PORT, nhưng vẫn khai báo EXPOSE
EXPOSE 10000

# Lệnh khởi chạy ứng dụng FastAPI bằng uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
