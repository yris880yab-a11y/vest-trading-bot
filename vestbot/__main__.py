import logging

from .bot import Bot
from .config import Config


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        Bot(Config.from_env()).run_forever()
    except KeyboardInterrupt:
        logging.getLogger("vestbot").info("Stopped by user")


if __name__ == "__main__":
    main()
