"""python -m secondlook.web [--stub] [--port N] [--samples DIR]: serve the page on 127.0.0.1 only."""
import argparse
import os

import uvicorn


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m secondlook.web", description="Second Look local page (in development)")
    p.add_argument("--stub", action="store_true", help="canned demo answers chosen by file name; the ladder does not run")
    p.add_argument("--port", type=int, default=int(os.environ.get("SECONDLOOK_PORT", "8765")))
    p.add_argument("--samples", metavar="DIR", help="folder holding the N1 demo samples; adds the 'Try a sample' gallery")
    a = p.parse_args(argv)
    if a.samples:
        os.environ["SECONDLOOK_SAMPLES"] = a.samples
    if a.stub:
        os.environ["SECONDLOOK_STUB"] = "1"
    uvicorn.run("secondlook.web.app:app", host="127.0.0.1", port=a.port, log_level="info")


if __name__ == "__main__":
    main()
