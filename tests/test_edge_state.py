"""Edge cases for describe() / device_state(): the tray tooltip text for every combination
of level, charging, charging_known, online, polling_paused and approx, in every language.
No tray, no hardware.

Run from the repository root:

    python -m unittest discover -s tests
"""
import itertools
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers

from halo import i18n  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402
from test_hide_rename import hb  # noqa: E402

LEFT = "about 5 h of use left"
LEVELS = (None, 0, 1, 50, 99, 100)
APPROX = (
    ("", "", {}),
    ("about 55% (medium)", "state.approx_grade", {"level": 55, "grade": "medium"}),
    ("about 20%", "state.approx", {"level": 20}),
    ("about 55% (medium)", "", {}),                                   # English text only
    ("charging (about 40% before it was plugged in)", "state.charging_was", {"level": 40, "charging": True}),
    ("connected, battery level not reported yet", "state.pending", {"bluetooth": True, "last_known": True}),
)


def make(level=50, **kw):
    return DeviceStatus("k", kw.pop("name", "Mouse"), level, **kw)


def states():
    for level, charging, known, online, paused, (approx, message, params) in itertools.product(
            LEVELS, (False, True), (True, False), (True, False), (False, True), APPROX):
        yield make(level, charging=charging, charging_known=known, online=online, polling_paused=paused,
                   approx=approx, ui_message=message, ui_params=dict(params))


class EveryCombinationTests(unittest.TestCase):
    def test_never_raises_never_blank_never_leaves_a_placeholder(self):
        count = 0
        for code in i18n.LANGUAGES:
            for st in states():
                for left in ("", LEFT):
                    count += 1
                    with self.subTest(language=code, level=st.level, charging=st.charging,
                                      known=st.charging_known, online=st.online, paused=st.polling_paused,
                                      approx=st.approx, left=bool(left)):
                        text = hb.describe(st, left=left, language=code)
                        self.assertIsInstance(text, str)
                        self.assertTrue(text.strip())
                        self.assertNotRegex(text, r"[{}]")
                        self.assertTrue(text.startswith("Mouse"))             # French puts a space before the colon
                        tip = i18n.tooltip(text)
                        self.assertLessEqual(len(tip.encode("utf-16-le")), 254)
        self.assertGreater(count, 1000)

    def test_english_invariants(self):
        for st in states():
            for left in ("", LEFT):
                with self.subTest(level=st.level, charging=st.charging, known=st.charging_known,
                                  online=st.online, paused=st.polling_paused, approx=st.approx,
                                  left=bool(left)):
                    text = hb.device_state(st, left)
                    plain = not st.polling_paused and not st.approx and st.level is not None
                    if st.polling_paused:
                        self.assertEqual(text, "not reported yet" if st.level is None else f"{st.level}%")
                    elif st.approx:
                        self.assertEqual(text, st.approx)         # a coarse device never gets "NN%"
                    elif st.level is None:
                        self.assertEqual(text, "no link (off or asleep)")
                    if plain:
                        self.assertTrue(text.startswith(f"{st.level}%"))
                        self.assertIs(bool(re.search(r", charging(?! status)", text)), st.charging)
                        self.assertIs("charging status unknown" in text,
                                      not st.charging and not st.charging_known)
                        self.assertIs("device asleep" in text, not st.online)
                        shows_left = (bool(left) and st.online and not st.charging and st.charging_known)
                        self.assertIs(LEFT in text, shows_left)
                    else:
                        self.assertNotIn(LEFT, text)


class SpecificTests(unittest.TestCase):
    def test_english_table(self):
        for kwargs, left, expected in (
                (dict(level=85), "", "85%"),
                (dict(level=0), "", "0%"),                               # zero is a level, not "no link"
                (dict(level=100), LEFT, "100%, " + LEFT),
                (dict(level=85, charging=True), LEFT, "85%, charging"),
                (dict(level=85, charging_known=False), LEFT, "85%, charging status unknown"),
                (dict(level=85, online=False), LEFT, "85% (last known value, device asleep)"),
                (dict(level=85, charging=True, online=False), "", "85%, charging (last known value, device asleep)"),
                (dict(level=None), LEFT, "no link (off or asleep)"),
                (dict(level=None, charging=True, online=False), "", "no link (off or asleep)"),
                (dict(level=40, polling_paused=True, charging=True, online=False), LEFT, "40%"),
                (dict(level=None, polling_paused=True), "", "not reported yet"),
                (dict(level=None, approx="not reported yet"), "", "not reported yet"),
                (dict(level=55, approx="about 55% (medium)"), LEFT, "about 55% (medium)")):
            with self.subTest(kwargs=kwargs, left=bool(left)):
                self.assertEqual(hb.device_state(make(**kwargs), left), expected)

    def test_translated_approx_follows_ui_message_and_wraps(self):
        st = make(None, approx="about 55% (medium)", ui_message="state.approx_grade",
                  ui_params={"level": 55, "grade": "medium", "charging": True, "last_known": True,
                             "bluetooth": True})
        english = hb.device_state(st, language="en")
        self.assertEqual(english, "about 55% (medium)")                  # English keeps the provider text
        for code in i18n.LANGUAGES:
            if code == "en":
                continue
            with self.subTest(language=code):
                text = hb.device_state(st, language=code)
                self.assertIn("55", text)
                self.assertNotEqual(text, english)
                grade = i18n.translate("level.medium", language=code)
                self.assertIn(grade, text)

    def test_translated_approx_without_ui_message_keeps_the_english_text(self):
        st = make(None, approx="some provider text", ui_message="")
        for code in i18n.LANGUAGES:
            with self.subTest(language=code):
                self.assertEqual(hb.device_state(st, language=code), "some provider text")

    def test_unknown_language_is_english(self):
        st = make(50, charging=True)
        for code in ("xx", "", None, "EN"):
            with self.subTest(language=code):
                self.assertEqual(hb.device_state(st, language=code), "50%, charging")

    def test_describe_names(self):
        st = make(50, name="G502")
        for name, expected in ((None, "G502: 50%"), ("", "G502: 50%"), ("My mouse", "My mouse: 50%"),
                               ("{state} %s {0}", "{state} %s {0}: 50%"),
                               ("Écouteurs \U0001F3A7", "Écouteurs \U0001F3A7: 50%")):
            with self.subTest(name=name):
                self.assertEqual(hb.describe(st, name), expected)
        self.assertEqual(hb.describe(make(50, name=""), None), ": 50%")     # nothing to show but still no crash

    def test_long_names_are_cut_by_the_tooltip_not_by_describe(self):
        st = make(50, name="電" * 300)
        text = hb.describe(st, language="ja")
        self.assertGreater(len(text), 127)
        tip = i18n.tooltip(text)
        self.assertEqual(len(tip.encode("utf-16-le")) // 2, 127)

    def test_missing_message_params_are_a_loud_error_not_a_wrong_text(self):
        st = make(None, approx="x", ui_message="state.approx", ui_params={})   # {level} missing
        with self.assertRaises(KeyError):
            hb.device_state(st, language="fr")


if __name__ == "__main__":
    unittest.main()
