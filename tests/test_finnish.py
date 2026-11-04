import unittest

from villa_kuppis.finnish import fi_text, translate_payload


class FinnishTextTests(unittest.TestCase):
    def test_known_messages(self):
        self.assertEqual(fi_text("value must be between 18 and 30"), "Arvon on oltava 18–30.")
        self.assertEqual(fi_text("state read failed: HTTP 503"), "Tilan luku epäonnistui: HTTP 503")
        self.assertEqual(
            fi_text("cloud read failed: A MELCloud Home request timed out."),
            "Pilvipalvelun luku epäonnistui: MELCloud Home -pyyntö aikakatkaistiin.")
        self.assertEqual(fi_text("Home Assistant is not connected"), "Home Assistantiin ei ole yhteyttä.")
        self.assertEqual(fi_text("ClientConnectorError: boom"), "Yhteysvirhe Home Assistantiin.")

    def test_unknown_and_finnish_stay(self):
        for text in ("pending", "Laite ei vastaa", "internal_error", "", "Some new English"):
            self.assertEqual(fi_text(text), text)

    def test_payload(self):
        body = {"error": "out_of_bounds", "detail": "value must be a number",
                "events": [{"actor": "web", "detail": "another command for this device is pending"}]}
        out = translate_payload(body)
        self.assertEqual(out["error"], "out_of_bounds")
        self.assertEqual(out["detail"], "Arvon täytyy olla luku.")
        self.assertEqual(out["events"][0]["actor"], "Verkkosovellus")
        self.assertEqual(out["events"][0]["detail"], "Toinen komento on vielä kesken.")


if __name__ == "__main__":
    unittest.main()
