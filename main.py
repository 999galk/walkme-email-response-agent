"""
main.py
orchestration logic entrypoint
"""

from runtime.orchestrator import run


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass