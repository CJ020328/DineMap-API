import uvicorn
from api import app

# 这是一个WSGI应用，用于gunicorn
def wsgi_app(environ, start_response):
    # 告诉gunicorn这不是WSGI应用
    raise ValueError(
        "FastAPI应用需要ASGI服务器，请使用: "
        "gunicorn -k uvicorn.workers.UvicornWorker api:app"
    )

# 如果直接运行此文件，则使用uvicorn启动
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 