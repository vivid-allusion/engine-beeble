"""Profile-derived output settings for Beeble."""



class BeebleProfileHelper:
    """Resolve output defaults from the engine profile."""

    def _default_extension(self, media_type: str) -> str:
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"
