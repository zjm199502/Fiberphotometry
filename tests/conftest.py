"""Synthetic ABET schedule shared by parser and desktop integration tests."""
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture
def abet_xml(tmp_path):
    path = tmp_path / "test.xml"
    root = ET.Element("AbetSession")
    data = ET.SubElement(root, "SessionData")
    ET.SubElement(data, "Name").text = "Synthetic cue and tray"
    info = ET.SubElement(ET.SubElement(data, "SessionVariables"), "SessionVariable")
    ET.SubElement(info, "Name").text = "Animal ID"
    ET.SubElement(info, "Value").text = "sample"
    rows = ET.SubElement(root, "DataEntries")

    def add(t, kind, effect, **args):
        row = ET.SubElement(rows, "DataEntry")
        for name, value in (("Time", t), ("Auto", len(rows)-1), ("EventText", kind), ("EffectText", effect)):
            ET.SubElement(row, name).text = str(value)
        a = ET.SubElement(row, "Args")
        for k, v in args.items():
            arg = ET.SubElement(a, "Arg")
            ET.SubElement(arg, "Name").text = k
            ET.SubElement(arg, "Value").text = str(v)

    def window(t):
        add(t, "Group Change Event", "Group Change", **{"New Group": "4"})

    def tray(t):
        add(t, "Input Transition On Event", "Tray #1")

    def cue(t, special=False):
        add(t, "Pulse Output Event", "Sound_On #1", Duration=6)
        add(t if special else t+6, "Output On Event", "TrayLight #1")
        window(t+6)

    def timeout(t):
        add(t, "Condition Event", "Start Delay")
        add(t, "Output Off Event", "TrayLight #1")

    add(.2, "Pulse Output Event", "start #1", Duration=1)
    cue(3, special=True)
    timeout(29)
    tray(30)
    tray(37)
    tray(45)
    tray(45.1)
    cue(52)
    tray(60)
    add(60.001, "Condition Event", "Note Tray Entry")
    add(60.001, "Output Off Event", "TrayLight #1")
    add(60.001, "Pulse Output Event", "Feeder #1", Duration=.4)
    cue(72)
    timeout(98)
    cue(120)
    # Interrupted trial: no completed response window.
    add(128, "Pulse Output Event", "start #1", Duration=1)
    ET.ElementTree(root).write(path)
    return path
