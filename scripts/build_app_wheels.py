"""Build both app distributions from fresh staging, without installing them."""
import argparse
from email.parser import BytesParser
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from build_robot_plugin import verify_release


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    release = verify_release(source)
    if output.is_relative_to(source / "fanuc_workbench"):
        raise ValueError("Keep release output outside the application source package")
    if output.exists():
        raise FileExistsError("Preserve earlier artifacts; choose a fresh wheel output directory")
    output.mkdir(parents=True)
    results = []
    for name, project, distribution in (("desktop", source / "pyproject.toml", "fanuc-mcp-server"),
            ("bridge", source / "packaging/bridge/pyproject.toml", "fanuc-mcp-server-bridge")):
        stage = output / "stages" / name
        stage.mkdir(parents=True)
        shutil.copytree(source / "fanuc_workbench", stage / "fanuc_workbench",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        shutil.copyfile(project, stage / "pyproject.toml")
        shutil.copyfile(source / "LICENSE", stage / "LICENSE")
        if name == "desktop":
            shutil.copyfile(source / "README.md", stage / "README.md")
        destination = output / name
        subprocess.run([sys.executable, "-B", "-m", "build", "--wheel", "--no-isolation",
                        "--outdir", str(destination), str(stage)], check=True)
        wheel, = destination.glob("*.whl")
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata_path, = [value for value in names if value.endswith(".dist-info/METADATA")]
            metadata = BytesParser().parsebytes(archive.read(metadata_path))
            if metadata["Name"] != distribution or metadata["Version"] != release["package_version"]:
                raise ValueError("Built distribution differs from the fixed release identity")
            for path in names:
                if path.startswith("fanuc_workbench/"):
                    original = source / path
                    if (not original.is_file() or "__pycache__" in Path(path).parts
                            or Path(path).suffix in {".pyc", ".pyo"}
                            or archive.read(path) != original.read_bytes()):
                        raise ValueError("Wheel contains stale or changed package bytes: " + path)
        results.append(str(wheel))
    return {"release": release, "wheels": results}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.output), indent=2))


if __name__ == "__main__":
    main()
