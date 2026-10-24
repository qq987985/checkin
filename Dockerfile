FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai HOST=0.0.0.0 PORT=8505 PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
COPY checkin.py .
COPY static ./static
EXPOSE 8505
CMD ["python", "-u", "checkin.py"]
