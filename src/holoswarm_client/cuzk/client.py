import httpx
from pyproj import Transformer


CUZK_WMS_URL = "https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer"


class CuzkOrtofotoClient:
    """
    Simple client for ČÚZK Ortofoto WMS.

    Input coordinates: WGS84 lon/lat, EPSG:4326
    Request coordinates: Web Mercator, EPSG:3857
    Output: PNG image
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.transformer = Transformer.from_crs(
            "EPSG:4326",
            "EPSG:3857",
            always_xy=True,  # lon, lat order
        )

    async def get_square_image(
        self,
        lon: float,
        lat: float,
        size_m: tuple[float,float],
        size_pixels: tuple[int,int],
        layer: str = "0"
    ) -> bytes:
        """
        Download a square ortofoto image centered at lon/lat.

        lon, lat: WGS84 coordinates
        size_m: square size in meters
        pixels: output image width and height in pixels
        layer: WMS layer name/id, usually "0" for this ArcGIS WMS
        """

        x, y = self.transformer.transform(lon, lat)

        half_x = size_m[0] / 2
        half_y = size_m[1] / 2
        bbox = (
            x - half_x,
            y - half_y,
            x + half_x,
            y + half_y,
        )

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": layer,
            "STYLES": "",
            "CRS": "EPSG:3857",
            "BBOX": ",".join(str(v) for v in bbox),
            "WIDTH": str(size_pixels[0]),
            "HEIGHT": str(size_pixels[1]),
            "FORMAT": "image/png",
            "TRANSPARENT": "false",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                CUZK_WMS_URL,
                params=params,
                headers={
                    "User-Agent": "cuzk-ortofoto-python-example/1.0"
                },
            )

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type.lower():
            raise RuntimeError(
                f"Expected image response, got {content_type}:\n"
                f"{response.text[:1000]}"
            )

        # output_path = Path(output_path)
        # output_path.write_bytes(response.content)
        # return output_path
        return response.content

    async def get_capabilities_xml(self) -> str:
        """
        Fetch WMS capabilities XML, useful for checking layer names,
        supported CRS values, formats, etc.
        """

        params = {
            "SERVICE": "WMS",
            "REQUEST": "GetCapabilities",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                CUZK_WMS_URL,
                params=params,
                headers={
                    "User-Agent": "cuzk-ortofoto-python-example/1.0"
                },
            )
        response.raise_for_status()
        return response.text


# if __name__ == "__main__":
#     client = CuzkOrtofotoClient()
# 
#     # Prague city center
#     lat = 50.0755
#     lon = 14.4378
# 
#     path = client.get_square_image(
#         lon=lon,
#         lat=lat,
#         size_m=300,      # 300m x 300m square
#         pixels=768,      # 768px x 768px image
#         output_path="prague_ortofoto.png",
#     )
# 
#     print(f"Saved image to: {path}")
# 
#     # Optional: verify it opens
#     img = Image.open(path)
#     print(img.size, img.mode) 
