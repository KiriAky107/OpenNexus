"""PyInstaller entry; the app package is included by the build script."""
from app.sidecar import main

if __name__ == "__main__":
    raise SystemExit(main())
