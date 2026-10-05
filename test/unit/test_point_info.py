import unittest

from holoswarm_client.gui.map.point_info import gnss_origin_text


class GnssOriginTextTest(unittest.TestCase):
    def test_format(self):
        text = gnss_origin_text(50.0905258, 14.6327381, "33U", 473864.7412, 5548732.2249, 300.04)
        self.assertEqual(text, (
            "#lat: 50.0905258\n"
            "#lon: 14.6327381\n"
            '#utm_zone: "33U"\n'
            "utm_x: 473864.74\n"
            "utm_y: 5548732.22\n"
            "amsl: 300.0\n"
        ))


if __name__ == "__main__":
    unittest.main()
