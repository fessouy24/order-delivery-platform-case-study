"""Container readiness probe without leaking connection information."""

import os
import sys
from urllib.request import Request, urlopen


def main():
    host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip()
    try:
        request = Request("http://127.0.0.1:8000/health/ready", headers={"Host": host})
        with urlopen(request, timeout=8) as response:
            if response.status != 200:
                sys.exit(1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
