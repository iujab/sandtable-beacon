"""Checks the hand-written SandtableBeacon.xcodeproj/project.pbxproj without Xcode.

THIS IS A PARSE AND CROSS-REFERENCE CHECK, NOT AN XCODE BUILD. It proves the file is
valid OpenStep-plist syntax that the pure-Python `pbxproj` package can load, that
every object reference resolves, and that every Swift file in SandtableBeacon/ is in
the Sources build phase. Whether Xcode accepts every build setting is unchecked.

Run in a throwaway environment (do not add pbxproj to the project's requirements):

    uv run --with pbxproj python apps/ios/tools/pbxproj_check.py
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from pbxproj import XcodeProject

HERE = Path(__file__).resolve().parent
IOS = HERE.parent
PROJECT = IOS / "SandtableBeacon.xcodeproj" / "project.pbxproj"
SOURCES = IOS / "SandtableBeacon"


def section(project: XcodeProject, name: str) -> list:
    return list(project.objects.get_objects_in_section(name))


def main() -> int:
    errors: list[str] = []
    project = XcodeProject.load(str(PROJECT))
    print(f"loaded {PROJECT.relative_to(IOS.parent.parent)} with pbxproj (a parse, not an Xcode build)")
    print(f"objectVersion = {project['objectVersion']}")
    if str(project["objectVersion"]) != "56":
        errors.append("objectVersion is not 56")

    ids = {o.get_id() for o in project.objects.get_objects_in_section(*project.objects.get_sections())}
    root = project["rootObject"]
    if root not in ids:
        errors.append(f"rootObject {root} does not resolve")

    # Every id-looking value inside every object must resolve to an object.
    def walk(value, owner):
        if isinstance(value, str):
            if len(value) == 24 and all(c in "0123456789ABCDEF" for c in value) and value not in ids:
                errors.append(f"{owner}: dangling reference {value}")
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, owner)
        elif hasattr(value, "get_keys"):
            for k in value.get_keys():
                if k != "isa":
                    walk(value[k], owner)

    for obj in project.objects.get_objects_in_section(*project.objects.get_sections()):
        walk(obj, f"{obj.isa} {obj.get_id()}")

    refs = {r.get_id(): r for r in section(project, "PBXFileReference")}
    groups = section(project, "PBXGroup")
    app_group = [g for g in groups if g.get("path", None) == "SandtableBeacon"]
    if len(app_group) != 1:
        errors.append("expected one PBXGroup with path SandtableBeacon")
    in_group = set(app_group[0].children) if app_group else set()

    # File references in the app group must exist on disk.
    for ref_id in in_group:
        ref = refs.get(ref_id)
        if ref is None:
            continue
        if not (SOURCES / ref.path).exists():
            errors.append(f"file reference {ref.path} does not exist on disk")

    build_files = {b.get_id(): b for b in section(project, "PBXBuildFile")}
    sources_phase = section(project, "PBXSourcesBuildPhase")
    resources_phase = section(project, "PBXResourcesBuildPhase")
    def phase_paths(phases: list) -> set:
        paths = set()
        for phase in phases:
            for build_id in phase.files:
                build_file = build_files.get(build_id)
                ref = refs.get(build_file.fileRef) if build_file is not None else None
                if ref is None:
                    errors.append(f"{phase.isa}: build file {build_id} does not lead to a file reference")
                else:
                    paths.add(ref.path)
        return paths

    compiled = phase_paths(sources_phase)
    resources = phase_paths(resources_phase)

    on_disk = sorted(p.name for p in SOURCES.glob("*.swift"))
    for name in on_disk:
        mark = "compiled" if name in compiled else "MISSING from Sources phase"
        print(f"  {name:28s} {mark}")
        if name not in compiled:
            errors.append(f"{name} is not in the Sources build phase")
    for name in sorted(compiled):
        if name not in on_disk:
            errors.append(f"Sources phase lists {name}, which is not on disk")
    print(f"  resources: {sorted(resources)}")
    if "Info.plist" in resources:
        errors.append("Info.plist must not be in Copy Bundle Resources")

    # Every file in SandtableBeacon/ must have a file reference (nothing on disk forgotten).
    ref_paths = {refs[r].path for r in in_group if r in refs}
    for path in sorted(SOURCES.iterdir()):
        if path.name.startswith("."):
            continue
        if path.name not in ref_paths:
            errors.append(f"{path.name} is on disk but has no file reference")

    # The target's build settings the owner relies on.
    expected = {
        "GENERATE_INFOPLIST_FILE": "NO",
        "INFOPLIST_FILE": "SandtableBeacon/Info.plist",
        "CODE_SIGN_STYLE": "Automatic",
        "DEVELOPMENT_TEAM": "",
        "TARGETED_DEVICE_FAMILY": "1",
        "IPHONEOS_DEPLOYMENT_TARGET": "16.0",
        "SWIFT_VERSION": "5.0",
    }
    target_configs = 0
    for cfg in section(project, "XCBuildConfiguration"):
        bs = cfg.buildSettings
        if "INFOPLIST_FILE" not in bs.get_keys():
            continue
        target_configs += 1
        for key, want in expected.items():
            got = str(bs[key]) if key in bs.get_keys() else None
            if got != want:
                errors.append(f"target {cfg.name}: {key} = {got!r}, expected {want!r}")
        if "ASSETCATALOG_COMPILER_APPICON_NAME" in bs.get_keys():
            errors.append(f"target {cfg.name}: names an app icon but the project has no asset catalog")
        plist = IOS / str(bs["INFOPLIST_FILE"])
        if not plist.exists():
            errors.append(f"INFOPLIST_FILE {plist.name} missing")
        print(f"  target config {cfg.name}: bundle id {bs['PRODUCT_BUNDLE_IDENTIFIER']}, "
              f"iOS {bs['IPHONEOS_DEPLOYMENT_TARGET']}, Swift {bs['SWIFT_VERSION']}, "
              f"device family {bs['TARGETED_DEVICE_FAMILY']}, Info.plist generated: {bs['GENERATE_INFOPLIST_FILE']}")
    if target_configs != 2:
        errors.append(f"expected 2 target build configurations, found {target_configs}")

    # Shared scheme and workspace data: valid XML, pointing at the native target.
    targets = section(project, "PBXNativeTarget")
    target_id = targets[0].get_id() if targets else None
    scheme = PROJECT.parent / "xcshareddata" / "xcschemes" / "SandtableBeacon.xcscheme"
    workspace = PROJECT.parent / "project.xcworkspace" / "contents.xcworkspacedata"
    try:
        root_el = ET.parse(scheme).getroot()
        blueprints = {el.get("BlueprintIdentifier") for el in root_el.iter("BuildableReference")}
        names = {el.get("BuildableName") for el in root_el.iter("BuildableReference")}
        if blueprints != {target_id}:
            errors.append(f"scheme BlueprintIdentifier {blueprints} does not match target {target_id}")
        if names != {"SandtableBeacon.app"}:
            errors.append(f"scheme BuildableName {names} is not SandtableBeacon.app")
        print(f"  shared scheme: {scheme.name} -> target {target_id}")
    except (OSError, ET.ParseError) as exc:
        errors.append(f"shared scheme unreadable: {exc}")
    try:
        ET.parse(workspace)
        print(f"  workspace data: {workspace.name} parses")
    except (OSError, ET.ParseError) as exc:
        errors.append(f"workspace data unreadable: {exc}")

    for e in errors:
        print("ERROR:", e)
    print(f"{len(on_disk)} Swift files on disk, {len(compiled)} in Sources phase, {len(errors)} error(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
