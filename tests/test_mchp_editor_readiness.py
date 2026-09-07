import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from brains.mchp.app.utility.libreoffice_editor import (
    prepare_editor_profile, select_ready_editor,
)
from brains.mchp.app.utility import libreoffice_gui
from tests import test_phase_workflow_runtime as runtime_tests


class EditorReadinessTests(unittest.TestCase):
    def test_modal_closure_precedes_editor_probe_with_no_deadline_reset(self):
        for closes in (True, False):
            with self.subTest(closes=closes):
                clock = [10.0]
                modules, Window, focus = runtime_tests.LibreOfficeReadinessTests._interactive_xlib_modules(
                    lambda: [calc, dialog] if clock[0] < 10.5 or not closes else [calc]
                )
                modules["Xlib"].X.IsViewable = 2
                calc = Window(1, "LibreOffice Calc", ("libreoffice-calc",))
                dialog = Window(2, "Format Cells", ("soffice",))
                dialog.get_attributes = lambda: SimpleNamespace(map_state=2)
                probes = []

                def probe(*args, **kwargs):
                    probes.append(clock[0])
                    self.assertGreaterEqual(clock[0], 10.5)
                    self.assertEqual(kwargs["timeout"], 11.0 - clock[0])
                    self.assertEqual(args[0][-1], "11.0")

                with patch.dict("sys.modules", modules), patch.object(
                    libreoffice_gui, "_focus_window", return_value=True
                ) as focus_window, patch.object(libreoffice_gui.subprocess, "run", side_effect=probe):
                    kwargs = dict(
                        editor_pipe="owned-calc", absent_dialog="Format Cells",
                        deadline=11.0, monotonic=lambda: clock[0],
                        sleeper=lambda delay: clock.__setitem__(0, clock[0] + delay),
                    )
                    if closes:
                        libreoffice_gui.wait_for_focused_window("LibreOffice Calc", **kwargs)
                        self.assertEqual(probes, [10.5])
                        focus_window.assert_called_once()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Format Cells"):
                            libreoffice_gui.wait_for_focused_window("LibreOffice Calc", **kwargs)
                        self.assertEqual(clock[0], 11.0)
                        self.assertEqual(probes, [])
                        focus_window.assert_not_called()

    def editor(self):
        document = Mock()
        document.supportsService.return_value = True
        document.hasControllersLocked.return_value = False
        controller = document.getCurrentController.return_value
        window = controller.getFrame.return_value.getComponentWindow.return_value
        window.isVisible.return_value = window.isEnabled.return_value = True
        cursor = controller.getViewCursor.return_value
        cursor.getText.return_value = document.Text
        cursor.isCollapsed.return_value = True
        controller.getSelection.return_value.getCellAddress.return_value = SimpleNamespace(
            Sheet=0, Column=0, Row=0,
        )
        return document, controller, window, Mock()

    def test_profile_suppresses_tip_before_launch_and_is_private(self):
        with tempfile.TemporaryDirectory() as td:
            profiles = [Path(td) / name for name in ("writer-owned", "calc-owned")]
            for profile in profiles:
                profile.mkdir()
                self.assertEqual(prepare_editor_profile(profile), profile.name)
                xml = ElementTree.parse(profile / "user/registrymodifications.xcu")
                item = xml.getroot()[0]
                self.assertEqual(item.attrib["{http://openoffice.org/2001/registry}path"],
                                 "/org.openoffice.Office.Common/Misc")
                self.assertEqual(item[0].attrib["{http://openoffice.org/2001/registry}name"],
                                 "ShowTipOfTheDay")
                self.assertEqual(item[0][0].text, "false")

    def test_focusable_window_without_ready_document_is_insufficient(self):
        document, controller, window, toolkit = self.editor()
        self.assertFalse(select_ready_editor(None, toolkit, "writer"))
        document.hasControllersLocked.return_value = True
        self.assertFalse(select_ready_editor(document, toolkit, "writer"))
        document.hasControllersLocked.return_value = False
        document.getCurrentController.return_value = None
        self.assertFalse(select_ready_editor(document, toolkit, "writer"))
        window.setFocus.assert_not_called()

    def test_disabled_or_hidden_editor_is_not_ready(self):
        for attribute in ("isVisible", "isEnabled"):
            with self.subTest(attribute=attribute):
                document, controller, window, toolkit = self.editor()
                getattr(window, attribute).return_value = False
                self.assertFalse(select_ready_editor(document, toolkit, "writer"))
                window.setFocus.assert_not_called()

    def test_writer_selects_document_body_without_writing_content(self):
        document, controller, window, toolkit = self.editor()
        events = Mock()
        events.attach_mock(toolkit.processEventsToIdle, "idle")
        cursor = controller.getViewCursor.return_value
        events.attach_mock(cursor.gotoRange, "body")
        events.attach_mock(window.setFocus, "focus")
        self.assertTrue(select_ready_editor(document, toolkit, "writer"))
        cursor.gotoRange.assert_called_once_with(document.Text.getStart(), False)
        self.assertEqual([call[0] for call in events.mock_calls],
                         ["idle", "body", "focus", "idle"])
        document.Text.setString.assert_not_called()
        document.storeAsURL.assert_not_called()

    def test_calc_selects_and_verifies_a1_not_screen_center(self):
        document, controller, window, toolkit = self.editor()
        self.assertTrue(select_ready_editor(document, toolkit, "calc"))
        sheet = document.getSheets.return_value.getByIndex.return_value
        controller.setActiveSheet.assert_called_once_with(sheet)
        sheet.getCellRangeByName.assert_called_once_with("A1")
        controller.select.assert_called_once_with(sheet.getCellRangeByName.return_value)
        window.setFocus.assert_called_once_with()
        controller.getSelection.return_value.getCellAddress.return_value.Row = 20
        self.assertFalse(select_ready_editor(document, toolkit, "calc"))
        sheet.getCellRangeByName.return_value.setString.assert_not_called()

    def test_editor_probe_uses_only_remaining_original_readiness_deadline(self):
        modules, Window, focus = runtime_tests.LibreOfficeReadinessTests._interactive_xlib_modules(
            lambda: [window]
        )
        window = Window(1, "LibreOffice Writer", ("libreoffice-writer",))
        clock = [100.0]

        def focus_window(*args):
            clock[0] += 7
            return True

        with patch.dict("sys.modules", modules), patch.object(
            libreoffice_gui, "_focus_window", side_effect=focus_window
        ), patch.object(libreoffice_gui.subprocess, "run") as run:
            libreoffice_gui.wait_for_focused_window(
                "LibreOffice Writer", editor_pipe="owned-writer",
                monotonic=lambda: clock[0], timeout_s=30,
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 23)
        self.assertEqual(run.call_args.args[0][-3:], ["owned-writer", "writer", "130.0"])

    def test_editor_probe_failure_or_timeout_prevents_typing_boundary(self):
        for error in (subprocess.TimeoutExpired("probe", 30),
                      subprocess.CalledProcessError(1, "probe", stderr="editor not ready")):
            with self.subTest(error=type(error).__name__):
                modules, Window, focus = runtime_tests.LibreOfficeReadinessTests._interactive_xlib_modules(
                    lambda: [window]
                )
                window = Window(1, "LibreOffice Calc", ("libreoffice-calc",))
                with patch.dict("sys.modules", modules), patch.object(
                    libreoffice_gui.subprocess, "run", side_effect=error
                ), self.assertRaisesRegex(RuntimeError, "editor_readiness="):
                    libreoffice_gui.wait_for_focused_window(
                        "LibreOffice Calc", editor_pipe="owned-calc",
                    )


if __name__ == "__main__":
    unittest.main()
