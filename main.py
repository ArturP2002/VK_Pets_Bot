
import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(description="ExoCare VK Bot")
    parser.add_argument(
        "mode",
        nargs="?",
        default="bot",
        choices=["bot", "webhook", "seed"],
        help="Run mode: bot (default), webhook, or seed",
    )
    args = parser.parse_args()

    if args.mode == "seed":
        from migrations.seed import run_seed
        run_seed()
        return 0
    if args.mode == "webhook":
        from api.webhook import run_webhook_server
        run_webhook_server()
        return 0
    from bot.longpoll import run_bot
    run_bot()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
