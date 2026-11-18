from romanfeed.cli import main

# Guarded: the journey renderer starts worker processes with "spawn", which
# re-imports the main module in each one.
if __name__ == "__main__":
    raise SystemExit(main())
