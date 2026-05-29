"""Точка входа для запуска бота: python run.py"""
import asyncio

from app.main import run


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass
