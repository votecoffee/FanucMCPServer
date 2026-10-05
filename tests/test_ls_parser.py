from pathlib import Path

from fanuc_workbench.parsers.ls import parse_positions, parse_program_info, set_program_comment

SAMPLE = r'''/PROG TEST_PROG
/ATTR
COMMENT = "Old comment";
/MN
   1:  ! TEST ;
/POS
P[1]{
   GP1:
      UF : 2, UT : 3, CONFIG : 'F U T, 0, 0, 0',
      X = 100.000 mm, Y = 200.000 mm, Z = 300.000 mm,
      W = 10.000 deg, P = 20.000 deg, R = 30.000 deg
};
/END
'''


def test_comment_roundtrip(tmp_path: Path):
    p = tmp_path / "TEST_PROG.LS"
    p.write_text(SAMPLE, encoding="ascii")
    assert parse_program_info(p).comment == "Old comment"
    set_program_comment(p, "New comment")
    assert parse_program_info(p).comment == "New comment"


def test_positions(tmp_path: Path):
    p = tmp_path / "TEST_PROG.LS"
    p.write_text(SAMPLE, encoding="ascii")
    positions = parse_positions(p)
    assert len(positions) == 1
    pos = positions[0]
    assert pos.program == "TEST_PROG"
    assert pos.index == 1
    assert pos.user_frame == "2"
    assert pos.user_tool == "3"
    assert pos.values["X"] == 100.0
    assert pos.values["R"] == 30.0
