"""ShineHeKnowledge 桌面应用入口"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.app import KnowledgeBaseApp
from src.utils.config import Config


def main():
    Config.load()
    app = KnowledgeBaseApp(sys.argv)
    app.run()


if __name__ == "__main__":
    main()
