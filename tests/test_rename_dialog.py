"""Opt-in Windows Forms integration checks: no hardware and no manual interaction.

Run on Windows with HALO_TEST_WINDOWS_DIALOG=1. The real Shown callbacks execute
in an invisible, automatically closed dialog; no tray or application startup.
"""
import json
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

from halo import i18n
from test_hide_rename import hb


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("HALO_TEST_WINDOWS_DIALOG") == "1",
                     "set HALO_TEST_WINDOWS_DIALOG=1 on an interactive Windows desktop")
class RenameDialogTests(unittest.TestCase):
    def run_dialog(self, language, *, automatic_variable=False):
        real_run = subprocess.run
        with mock.patch.object(hb.subprocess, "run", return_value=types.SimpleNamespace(stdout=b"")) as capture:
            hb.ask_name('Écouteurs 🎧 "Bureau"', language=language)
        args = list(capture.call_args.args[0])
        # stdin must be closed: the regression control below uses PowerShell's automatic
        # $input variable, which reads the process's standard input to its end. Inherited
        # from an interactive console or an open pipe (a developer's terminal, an IDE or an
        # agent) it blocks forever, so the run only looked flaky. A fresh PowerShell needs
        # ~1 s here; 60 s is a bound for a loaded machine, not an expectation.
        kwargs = dict(capture.call_args.kwargs, timeout=60, stdin=subprocess.DEVNULL)
        script = args[-1]
        if automatic_variable:
            script = script.replace("$tb", "$input")  # reproduce the reviewed regression
        start = script.index("if ($form.ShowDialog()")
        script = script[:start] + r"""
$script:failed = $false
$form.Opacity = 0
$form.ShowInTaskbar = $false
$form.Add_Shown({
    try {
        $box = @($layout.Controls | Where-Object { $_ -is [System.Windows.Forms.TextBox] })[0]
        # Windows may not give a hidden, just-created window the foreground at once (busy
        # desktop, CI session). Activate it and let the message loop run for up to ~3 s
        # instead of reading Focused in the very first tick.
        for ($i = 0; $i -lt 30 -and -not $box.Focused; $i++) {
            [void]$form.Activate()
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 100
        }
        if (-not $box.Focused) { throw 'TextBox did not receive focus' }
        if ($box.SelectionStart -ne 0 -or $box.SelectionLength -ne $box.Text.Length) {
            throw 'The current name was not fully selected'
        }
        $details = @{ name=$box.Text; title=$form.Text; prompt=$label.Text
                      ok=$ok.Text; cancel=$cancel.Text }
        [Console]::WriteLine(($details | ConvertTo-Json -Compress))
    } catch {
        $script:failed = $true
        [Console]::Error.WriteLine($_)
    } finally {
        $form.DialogResult = 'Cancel'
        $form.Close()
    }
})
[void]$form.ShowDialog()
$form.Dispose()
if ($script:failed -or $Error.Count) { exit 1 }
"""
        args[-1] = script
        return real_run(args, **kwargs)

    def test_real_shown_callback_focuses_and_selects_name_in_each_language(self):
        for code in i18n.LANGUAGES:
            with self.subTest(language=code):
                result = self.run_dialog(code)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                details = json.loads(result.stdout.decode("utf-8-sig"))
                self.assertEqual(details["name"], 'Écouteurs 🎧 "Bureau"')
                for field, key in (("title", "rename.title"), ("prompt", "rename.prompt"),
                                   ("ok", "rename.ok"), ("cancel", "rename.cancel")):
                    self.assertEqual(details[field], hb.i18n.translate(key, language=code))

    def test_automatic_input_variable_reproduces_the_regression(self):
        result = self.run_dialog("fr", automatic_variable=True)
        self.assertNotEqual(result.returncode, 0, "the regression control unexpectedly passed")


if __name__ == "__main__":
    unittest.main()
