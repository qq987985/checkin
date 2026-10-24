FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai HOST=0.0.0.0 PORT=8505 PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY checkin.py cookiecloud.py notifications.py ./
COPY static ./static
EXPOSE 8505
CMD ["python", "-u", "checkin.py"]
