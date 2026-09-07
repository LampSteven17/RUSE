"""Readiness/selection only; run with LibreOffice's system Python UNO binding.

Content and Save As remain keyboard operations in the owning GUI workflow.
The caller bounds even a blocked UNO call by its original readiness deadline.
"""

import sys
import time
from pathlib import Path


def prepare_editor_profile(profile: Path) -> str:
    """Suppress the startup modal in this invocation's private profile only."""
    user = profile / "user"
    user.mkdir()
    (user / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Misc">'
        '<prop oor:name="ShowTipOfTheDay" oor:op="fuse">'
        '<value>false</value></prop></item></oor:items>',
        encoding="utf-8",
    )
    return profile.name


def select_ready_editor(document, toolkit, kind):
    """Require the real model/controller and drain startup GUI events first."""
    service = {"writer": "com.sun.star.text.TextDocument",
               "calc": "com.sun.star.sheet.SpreadsheetDocument"}[kind]
    if document is None or not document.supportsService(service):
        return False
    if document.hasControllersLocked():
        return False
    controller = document.getCurrentController()
    if controller is None:
        return False
    window = controller.getFrame().getComponentWindow()
    if window is None or not window.isVisible() or not window.isEnabled():
        return False
    toolkit.processEventsToIdle()
    if kind == "writer":
        cursor = controller.getViewCursor()
        cursor.gotoRange(document.Text.getStart(), False)
    else:
        sheet = document.getSheets().getByIndex(0)
        controller.setActiveSheet(sheet)
        controller.select(sheet.getCellRangeByName("A1"))
    window.setFocus()
    toolkit.processEventsToIdle()
    if kind == "writer":
        cursor = controller.getViewCursor()
        return cursor.getText() == document.Text and cursor.isCollapsed()
    address = controller.getSelection().getCellAddress()
    return (address.Sheet, address.Column, address.Row) == (0, 0, 0)


def wait_for_editor(pipe, kind, deadline):
    import uno
    from com.sun.star.connection import NoConnectException

    context = uno.getComponentContext()
    resolver = context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", context
    )
    remote = None
    while time.monotonic() < deadline:
        if remote is None:
            try:
                remote = resolver.resolve(
                    f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext"
                )
            except NoConnectException:
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                continue
        manager = remote.ServiceManager
        desktop = manager.createInstanceWithContext("com.sun.star.frame.Desktop", remote)
        toolkit = manager.createInstanceWithContext("com.sun.star.awt.Toolkit", remote)
        if select_ready_editor(desktop.getCurrentComponent(), toolkit, kind):
            return
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"{kind} document editor/input destination not ready")


if __name__ == "__main__":
    wait_for_editor(sys.argv[1], sys.argv[2], float(sys.argv[3]))
