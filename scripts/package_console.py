"""Package the existing SAM application for Console deployment; makes no AWS calls."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "boto3": "boto3", "botocore": "botocore", "s3transfer": "s3transfer",
    "jmespath": "jmespath", "python-dateutil": "dateutil", "urllib3": "urllib3",
    "six": "six.py",
}


def package(dependencies, bucket, output):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("A valid private artifact bucket name is required")
    output.mkdir(parents=True, exist_ok=True)
    distributions = {
        d.metadata["Name"].lower().replace("_", "-"): d
        for d in importlib.metadata.distributions(path=[str(dependencies)])
    }
    pinned = (ROOT / "backend/requirements.txt").read_text().strip()
    name, version = pinned.split("==")
    if distributions[name].version != version:
        raise ValueError("Installed boto3 does not match backend/requirements.txt")
    if distributions["botocore"].version != version:
        raise ValueError("Use the botocore version matching the pinned boto3")
    files = {}

    def include(source, base):
        candidates = [source] if source.is_file() else sorted(source.rglob("*"))
        for file in candidates:
            if not file.is_file() or "__pycache__" in file.parts or file.suffix in {".pyc", ".pyo"}:
                continue
            if file.suffix.lower() in {".pyd", ".so", ".dll", ".exe"}:
                raise ValueError("Console package only supports the reviewed pure Python dependencies")
            files[file.relative_to(base).as_posix()] = file

    include(ROOT / "backend/cloudhub", ROOT / "backend")
    versions = {}
    for distribution, import_name in PACKAGES.items():
        metadata = distributions[distribution]
        source = dependencies / import_name
        if not source.exists():
            raise ValueError("Missing dependency: " + distribution)
        include(source, dependencies)
        for relative in metadata.files or []:
            file = dependencies / relative
            if ".dist-info" in relative.parts[0] and file.is_file():
                files[relative.as_posix()] = file
        versions[distribution] = metadata.version
    archive = output / "backend.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for relative, file in sorted(files.items()):
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, file.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    key = "backend-" + digest + ".zip"
    archive.rename(output / key)
    template = json.loads((ROOT / "infra/control-plane.json").read_text())
    for resource in template["Resources"].values():
        if resource["Type"] == "AWS::Serverless::Function":
            resource["Properties"]["CodeUri"] = {"Bucket": bucket, "Key": key}
        if resource["Type"] == "AWS::Serverless::StateMachine":
            properties = resource["Properties"]
            definition = ROOT / "infra" / properties.pop("DefinitionUri")
            properties["Definition"] = json.loads(definition.read_text())
    packaged = output / "control-plane.packaged.json"
    packaged.write_text(json.dumps(template, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    manifest = {
        "mode": "LOCAL_PACKAGE_NOT_UPLOADED", "artifact_bucket": bucket,
        "artifact_key": key, "sha256": digest, "bytes": (output / key).stat().st_size,
        "entries": len(files), "dependencies": versions,
        "template_sha256": hashlib.sha256(packaged.read_bytes()).hexdigest(),
        "runtime": "python3.13", "architecture": "x86_64",
        "changes": "S3 CodeUri and inline existing workflow only; resources and policies unchanged",
        "blocked_until": "AWS account/service checks, valid parameters and action-time IAM confirmation",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with zipfile.ZipFile(output / key) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("Package CRC verification failed")
        if sum(i.file_size for i in zipped.infolist()) > 250 * 1024 * 1024:
            raise ValueError("Package exceeds Lambda uncompressed limit")
    print(json.dumps(manifest, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependencies", required=True, type=Path)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    package(args.dependencies.resolve(), args.bucket, args.out.resolve())


if __name__ == "__main__":
    main()
