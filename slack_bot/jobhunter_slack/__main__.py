import argparse
import asyncio
import logging

from pydantic import ValidationError

from .config import Settings
from .delivery import DeliveryError
from .runtime import run


def main():
    parser = argparse.ArgumentParser(description="Jobhunter private Slack coordination worker")
    parser.add_argument(
        "--check", action="store_true", help="Check credentials, participants and DB"
    )
    parser.add_argument("--env-file", default=".env.slack", help="Ignored local configuration file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # Dependency diagnostics can contain event bodies, headers, or raw provider data.
    for name in ("slack_bolt", "slack_sdk", "openai", "httpx", "httpcore", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        settings = Settings(_env_file=args.env_file)
        asyncio.run(run(settings, args.check))
    except ValidationError:
        print("configuration_invalid: check the local env file against .env.slack.example")
        return 2
    except DeliveryError as error:
        print("startup_failed: " + error.code)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("worker_stopped: runtime_unavailable; check connectivity and local configuration")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
