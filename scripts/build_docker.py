"""构建 Docker 容器镜像

用法: python scripts/build_docker.py
需要: 已安装 Docker
"""
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.version import APP_NAME, VERSION

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IMAGE_NAME = "shinehe/knowledge-base"


def run(cmd):
    print(f"> {cmd}")
    subprocess.run(cmd, shell=True, cwd=ROOT, check=True)


def main():
    tag = f"{IMAGE_NAME}:{VERSION}"
    tag_latest = f"{IMAGE_NAME}:latest"
    print(f"\n{'='*50}")
    print(f"构建 Docker 镜像: {tag}")
    print(f"{'='*50}\n")
    run(f'docker build -t "{tag}" -t "{tag_latest}" .')
    print(f"\n[OK] 镜像构建完成!")
    print(f"  {tag}")
    print(f"  {tag_latest}")
    print(f"\n启动: docker-compose up -d shinehe-api")
    print(f"推送: docker push {tag}")


if __name__ == "__main__":
    main()
