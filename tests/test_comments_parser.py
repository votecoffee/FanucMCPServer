from pathlib import Path

from fanuc_workbench.parsers.comments import (
    parse_frame_comments,
    parse_io_comments,
    parse_ls_comments,
    parse_numreg_comments,
    parse_posreg_comments,
    set_comment,
)
from fanuc_workbench.parsers.ls import parse_positions


def test_numreg_comments_roundtrip(tmp_path: Path):
    p = tmp_path / "NUMREG.VA"
    p.write_text("  [1] = 42 'Part count'\n  [2] = -3.250 'Offset'\n", encoding="ascii")
    rows = parse_numreg_comments(p)
    assert [(r.identifier, r.value, r.comment) for r in rows] == [
        ("R[1]", "42", "Part count"),
        ("R[2]", "-3.250", "Offset"),
    ]
    set_comment(rows[1], "New offset")
    assert parse_numreg_comments(p)[1].comment == "New offset"
    assert "-3.250 'New offset'" in p.read_text(encoding="ascii")


def test_posreg_comments_roundtrip(tmp_path: Path):
    p = tmp_path / "POSREG.VA"
    p.write_text(
        "[1,1] = 'Home'\n"
        " Group: 1 Config: F U T, 0, 0, 0\n"
        " X: 1.000 Y: 2.000 Z: 3.000\n"
        " W: 4.000 P: 5.000 R: 6.000\n"
        "[2,7] = 'Aux group' Uninitialized\n",
        encoding="ascii",
    )
    rows = parse_posreg_comments(p)
    assert rows[0].identifier == "PR[1]"
    assert rows[0].comment == "Home"
    assert rows[1].identifier == "PR[2,7]"
    set_comment(rows[0], "Machine home")
    assert parse_posreg_comments(p)[0].comment == "Machine home"


def test_ls_program_and_point_comments(tmp_path: Path):
    p = tmp_path / "CELL.LS"
    p.write_text(
        '/PROG CELL\n/ATTR\nCOMMENT = "Main";\n/MN\n/POS\n'
        'P[1:"LOAD"]{\n GP1:\n UF : 1, UT : 2, CONFIG : \'F U T, 0, 0, 0\',\n'
        ' X = 1.000 mm, Y = 2.000 mm, Z = 3.000 mm,\n'
        ' W = 4.000 deg, P = 5.000 deg, R = 6.000 deg\n};\n/END\n',
        encoding="ascii",
    )
    comments = parse_ls_comments(p)
    assert [(r.kind, r.comment) for r in comments] == [("Program", "Main"), ("Program Position", "LOAD")]
    set_comment(comments[1], "LOAD NEW")
    assert 'P[1:"LOAD NEW"]{' in p.read_text(encoding="ascii")
    positions = parse_positions(p)
    assert len(positions) == 1
    assert positions[0].index == 1


def test_io_comments_roundtrip(tmp_path: Path):
    p = tmp_path / "IOSTATE.DG"
    p.write_text("DIN[   1] OFF Door Closed\nDOUT[2] ON Clamp Cmd\n", encoding="ascii")
    rows = parse_io_comments(p)
    assert rows[0].identifier == "DIN[1]"
    assert rows[0].comment == "Door Closed"
    set_comment(rows[0], "Gate Closed")
    assert "Gate Closed" in p.read_text(encoding="ascii")


def test_frame_comments_roundtrip(tmp_path: Path):
    p = tmp_path / "FRAME.DG"
    p.write_text(
        "Tool Frame\n"
        "1.000 2.000 3.000 4.000 5.000 6.000 Welder TCP\n"
        "Jog Frame\n"
        "0.000 0.000 0.000 0.000 0.000 0.000 Cell Jog\n"
        "User Frame\n"
        "10.000 20.000 30.000 0.000 0.000 90.000 Fixture A\n",
        encoding="ascii",
    )
    rows = parse_frame_comments(p)
    assert [r.identifier for r in rows] == ["UTOOL[1]", "JOG[1]", "UFRAME[1]"]
    set_comment(rows[2], "Fixture B")
    assert "Fixture B" in p.read_text(encoding="ascii")
