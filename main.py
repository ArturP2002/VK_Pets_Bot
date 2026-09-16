
import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(description="ExoCare VK Bot")
    parser.add_argument(
        "mode",
        nargs="?",
        default="bot",
        choices=["bot", "webhook", "seed", "migrate-pg"],
        help="Run mode: bot (default), webhook, seed, or migrate-pg",
    )
    args, rest = parser.parse_known_args()

    if args.mode == "seed":
        from migrations.seed import run_seed
        run_seed()
        return 0
    if args.mode == "migrate-pg":
        from migrations.migrate_sqlite_to_postgres import main as migrate_main
        return migrate_main(rest)
    if args.mode == "webhook":
        from api.webhook import run_webhook_server
        run_webhook_server()
        return 0
    from bot.longpoll import run_bot
    run_bot()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
