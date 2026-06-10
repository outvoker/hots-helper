"""``hots-web`` entry point: run the FastAPI app under uvicorn."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    # HOTS_WEB_PORT for our own deploys; PORT for platforms (Railway /
    # Render) that inject one. Hugging Face Spaces defaults to 7860.
    port = int(os.environ.get("HOTS_WEB_PORT") or os.environ.get("PORT") or 7860)
    uvicorn.run(
        "hots_helper.web.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=port,
    )


if __name__ == "__main__":
    main()
