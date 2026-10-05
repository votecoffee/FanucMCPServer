from fanuc_workbench.services.backup import remote_to_relative


def test_device_path():
    assert str(remote_to_relative("md:", "md:TEST.TP")).replace("\\", "/") == "md_/TEST.TP"


def test_nested_path():
    assert str(remote_to_relative("mc:", "mc:/folder/file.dat")).replace("\\", "/") == "mc_/folder/file.dat"
