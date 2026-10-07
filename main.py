from __future__ import annotations

import os
from typing import Any

from app import create_app


# False startet den eingebauten Entwicklungsserver nur auf diesem Computer.
# True macht ihn im lokalen Netzwerk erreichbar; alternativ RASPBERRY_MODE=1 setzen.
RASPBERRY_MODE = False


def server_settings(raspberry_mode: bool) -> dict[str, Any]:
    return {
        "host": "0.0.0.0" if raspberry_mode else "127.0.0.1",
        "port": int(os.getenv("FLASK_PORT", "5000")),
        "debug": False
        if raspberry_mode
        else os.getenv("FLASK_DEBUG", "0") == "1",
    }


def main() -> None:
    raspberry_mode = RASPBERRY_MODE or os.getenv("RASPBERRY_MODE", "0") == "1"
    mode_name = "Raspberry Modus" if raspberry_mode else "lokalen Modus"
    settings = server_settings(raspberry_mode)
    print(
        f"Medienserver startet im {mode_name} auf "
        f"http://{settings['host']}:{settings['port']}"
    )
    create_app().run(**settings)


if __name__ == "__main__":
    main()
