import logging

from .bot import Bot
from .config import Config
from .smc_bot import SMCBot


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = Config.from_env()
        (SMCBot if cfg.strategy == "smc" else Bot)(cfg).run_forever()
    except KeyboardInterrupt:
        logging.getLogger("vestbot").info("Stopped by user")


if __name__ == "__main__":
    main()
