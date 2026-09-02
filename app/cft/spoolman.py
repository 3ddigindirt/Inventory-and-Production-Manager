import httpx
from .config import settings


class SpoolmanClient:
    def __init__(self):
        self.base_url = settings.spoolman_url.rstrip("/")
        self.timeout = settings.spoolman_timeout_seconds

    async def _get(self, path: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()

    async def health(self):
        spools = await self._get("/api/v1/spool")
        return {"connected": True, "spool_count": len(spools) if isinstance(spools, list) else None}

    async def spools(self):
        return await self._get("/api/v1/spool")

    async def filaments(self):
        return await self._get("/api/v1/filament")

    async def inventory(self):
        """Return Spoolman spools in a stable shape for the CFT UI.

        Spoolman versions/plugins can expose a few fields under slightly
        different names, so this intentionally accepts common variants.
        """
        raw = await self.spools()
        if not isinstance(raw, list):
            return []

        def pick(obj, *names, default=None):
            if not isinstance(obj, dict):
                return default
            for name in names:
                value = obj.get(name)
                if value is not None and value != "":
                    return value
            return default

        def number(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        result = []
        for spool in raw:
            if not isinstance(spool, dict):
                continue
            filament = spool.get("filament") if isinstance(spool.get("filament"), dict) else {}
            vendor = filament.get("vendor") if isinstance(filament.get("vendor"), dict) else {}

            initial_weight = number(pick(spool, "initial_weight", default=pick(filament, "weight")))
            remaining_weight = number(pick(spool, "remaining_weight"))
            if remaining_weight is None:
                used_weight = number(pick(spool, "used_weight"))
                if initial_weight is not None and used_weight is not None:
                    remaining_weight = max(initial_weight - used_weight, 0)

            percent_remaining = None
            if initial_weight and remaining_weight is not None:
                percent_remaining = max(0.0, min(100.0, remaining_weight / initial_weight * 100.0))

            color = pick(filament, "color_hex", "color", default="") or ""
            color = str(color).strip()
            if color and not color.startswith("#") and len(color) in (3, 6, 8):
                color = f"#{color}"

            result.append({
                "spool_id": pick(spool, "id"),
                "filament_id": pick(filament, "id"),
                "vendor": pick(vendor, "name", default=pick(filament, "vendor_name", "vendor", default="Unknown")),
                "name": pick(filament, "name", default="Filament"),
                "material": pick(filament, "material", "material_name", default=""),
                "color_name": pick(filament, "color_name", default=""),
                "color_hex": color,
                "remaining_weight": remaining_weight,
                "initial_weight": initial_weight,
                "percent_remaining": percent_remaining,
                "location": pick(spool, "location", default=""),
                "lot_nr": pick(spool, "lot_nr", "lot_number", default=""),
                "archived": bool(pick(spool, "archived", default=False)),
                "registered": pick(spool, "registered"),
                "first_used": pick(spool, "first_used"),
                "last_used": pick(spool, "last_used"),
            })

        return result
