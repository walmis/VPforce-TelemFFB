"""Bench for the profile-matching situations a user can land in.

Every scenario is generated fresh from the repo's own defaults.xml, with
named curated patterns removed to stand for "this profile had not shipped
yet", paired with a hand-built userconfig.  Nothing is stored as a static
copy, so the fixtures cannot go stale as defaults.xml changes.

    py tools/scenarios/scenario.py             the menu: pick, verify, quit
    py tools/scenarios/scenario.py list        the scenarios, one line each
    py tools/scenarios/scenario.py use <name>  swap one in (backs the real files up once)
    py tools/scenarios/scenario.py verify      what the app should show for the active one
    py tools/scenarios/scenario.py restore     put the real files back

A dev build reads defaults.xml, userconfig_v2.xml and match_history.json
from the repo root, so "swap in" means writing those three files.  The
originals are copied aside on the first `use` and put back by `restore`;
anything the app writes while a scenario is active is discarded with it.
"""
import json
import os
import shutil
import sys
import textwrap
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BACKUP = os.path.join(HERE, "_scenario_backup")
FILES = ("defaults.xml", "userconfig_v2.xml", "match_history.json")
ACTIVE = os.path.join(HERE, "_active_scenario.txt")

#: Menu width, wide enough for the longest description on two lines and
#: still comfortable in a default terminal.
WIDTH = 86

#: The title the scenarios are written around.  Any livery of the same
#: aircraft works - they all match C172SP Classic.* - so the menu can point
#: this at whichever one is actually installed.
AC = "C172SP Classic Passengers"          # curated C172SP.* : 9 settings + a trim calibration
SIM, CLS, DEV = "MSFS", "PropellerAircraft", "joystick"


def rows(*specs):
    """(pattern, setting, value, device, profile) tuples -> <models> XML."""
    out = []
    for pattern, name, value, device, profile in specs:
        out.append(f""" <models>
  <name>{name}</name>
  <model>{pattern}</model>
  <value>{value}</value>
  <sim>{SIM}</sim>
  <device>{device}</device>
  <profile>{profile}</profile>
 </models>""")
    return out


def profile_for(pattern, profile="Auto User"):
    return [f""" <profileMappings>
  <sim>{SIM}</sim>
  <cls>{CLS}</cls>
  <model>{pattern}</model>
  <active_profile>{profile}</active_profile>
 </profileMappings>"""]


def user_xml(*blocks):
    body = "\n".join(b for group in blocks for b in group)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<TelemFFB>\n{body}\n</TelemFFB>\n'


def profile_row(pattern, profile, notes=None):
    """The row that registers a User Profile, with its notes if any."""
    note = f"\n  <notes>{notes}</notes>" if notes else ""
    return [f""" <models>
  <name>profile</name>
  <model>{pattern}</model>
  <value>{CLS}</value>
  <sim>{SIM}</sim>
  <device>{DEV}</device>
  <profile>{profile}</profile>{note}
 </models>"""]


def override(pattern, name, var, unit="bool"):
    return [f""" <sc_overrides>
  <name>{name}</name>
  <model>{pattern}</model>
  <var>{var}</var>
  <sc_unit>{unit}</sc_unit>
  <sim>{SIM}</sim>
 </sc_overrides>"""]


def added(pattern, *settings, profiles=None, active="User Default", notes=None, overrides=()):
    """A User Default Aircraft Profile: an aircraft the user added themselves.
    Its type row and its base settings carry "User Default" - that is what
    the wizard writes, and the user edits it directly without ever needing
    a User Profile on top.  ``profiles`` stacks User Profiles on it,
    {name: [(setting, value, device), ...]}; ``active`` is the one the
    aircraft flies with; ``notes`` is {profile: text}; ``overrides`` is
    [(name, var), ...].  Everything here is what a merge has to carry."""
    p = "User Default"
    out = (rows((pattern, "type", CLS, DEV, p)) + profile_for(pattern, active)
           + rows(*[(pattern, n, v, d, p) for n, v, d in settings]))
    for name, rows_ in (profiles or {}).items():
        out += profile_row(pattern, name, (notes or {}).get(name))
        out += rows(*[(pattern, n, v, d, name) for n, v, d in rows_])
    for name, var in overrides:
        out += override(pattern, name, var)
    return out


def edited(pattern, *settings, profile="Auto User"):
    """A User Profile: settings rows and a mapping under a pattern the user
    did NOT add, so no type row.  "Auto User" is the one TelemFFB creates the
    moment a slider moves on a built-in, which cannot itself be changed."""
    return (rows((pattern, "profile", CLS, DEV, profile)) + profile_for(pattern, profile)
            + rows(*[(pattern, n, v, d, profile) for n, v, d in settings]))


SCENARIOS = {
    "baseline": dict(
        why="Nothing of the user's. The shipped profile names the aircraft.",
        expect="Matched Model C172SP.*, Active Profile Built-In, no pill.",
        drop=[], user=user_xml()),

    "wizard": dict(
        why="The curated C172SP profile has not shipped yet.",
        expect="No match: the new-aircraft wizard fires. The suggested match string should "
               "start on 'C172SP Classic.*' (the title minus its last word), not the whole title.",
        drop=["C172SP.*"], user=user_xml()),

    "user-broad": dict(
        why="The user made a broad C172.* profile before the curated C172SP.* shipped.",
        expect="C172SP.* (curated, prefix 6) beats C172.* (prefix 4), so the shipped profile wins "
               "and nothing under C172.* applies. PILL: merge - COPIES both profiles onto C172SP.*: "
               "User Default becomes Auto User (aileron 0.77, elevator 0.66) and Night comes as "
               "Night (aileron 0.5). C172.* was flying with Night, so C172SP.* must be too: Active "
               "Profile reads Night, aileron_expo 0.5. The dropdown lists Built-In, Auto User and "
               "Night; both new profiles' notes say they were copied from C172.*. C172.* itself stays, "
               "since it may name other aircraft. No permanent no is offered here: nothing under "
               "C172.* reaches this aircraft, so there is nothing to keep. Only Merge and Not now. "
               "TWO ROWS DISAGREE and are marked: max_aileron_coeff (built-in 0.38, yours 0.11) and "
               "the ParkBrake override (built-in L:CURATED_PARK_BRAKE, yours L:MY_PARK_BRAKE). The "
               "built-in's value applies today and theirs takes over after the merge, so these are "
               "the one row type that both changes and conflicts.",
        drop=[], user=user_xml(added("C172.*", ("aileron_expo", "0.77", DEV),
                                     ("elevator_expo", "0.66", DEV),
                                     ("max_aileron_coeff", "0.11", DEV),
                                     profiles={"Night": [("aileron_expo", "0.5", DEV),
                                                         ("max_aileron_coeff", "0.11", DEV)]},
                                     active="Night",
                                     overrides=[("GearHandle", "L:MY_GEAR_LEVER"),
                                                ("ParkBrake", "L:MY_PARK_BRAKE")])),
        add_overrides=[("C172SP.*", "ParkBrake", "L:CURATED_PARK_BRAKE", "bool")]),

    "user-specific": dict(
        why="The user forked a profile of their own for this exact aircraft.",
        expect="C172SP Classic.* (prefix 14) beats the curated C172SP.* (prefix 6), so THEIR profile "
               "wins and none of the curated settings apply. PILL: merge - MOVES everything onto "
               "C172SP.* and removes C172SP Classic.*: User Default becomes Auto User (aileron 0.77), "
               "Night comes as Night (elevator 0.4) with its note intact plus a 'Migrated from' line, "
               "and the ParkBrake override follows. The aircraft keeps flying with Auto User, now with "
               "the curated rows underneath: max_aileron_coeff 0.38, aileron_expo 0.77. Or Keep mine.",
        drop=[], user=user_xml(added("C172SP Classic.*", ("aileron_expo", "0.77", DEV),
                                     profiles={"Night": [("elevator_expo", "0.4", DEV)]},
                                     notes={"Night": "softer elevator for night circuits"},
                                     overrides=[("ParkBrake", "L:MY_PARK_BRAKE")])),
        add_overrides=[("C172SP.*", "ParkBrake", "L:CURATED_PARK_BRAKE", "bool"),
                       ("C172SP.*", "APMaster", "L:CURATED_AP_MASTER", "bool")]),

    "edited": dict(
        why="The user moved a slider on the built-in C172SP.*: Auto User rows under the same "
            "string, no type row of their own. The commonest shape in every real config.",
        expect="The built-in C172SP.* names the aircraft and the Auto User rows apply on top: all "
               "curated settings present, only max_aileron_coeff overridden to 0.99. No pill: "
               "nothing of the user's competes.",
        drop=[], user=user_xml(edited("C172SP.*", ("max_aileron_coeff", "0.99", DEV)))),

    "same-string": dict(
        why="The user added C172SP.* themselves, and now a curated C172SP.* ships under the very "
            "same string.",
        expect="A tie on the same string goes to the shipped file, but both trees' rows apply "
               "anyway: all curated settings present, max_aileron_coeff 0.99 from the user's "
               "User Default on top. PILL: merge - NOTHING MOVES here, the rows are already under "
               "this string: it drops their type row and renames User Default to Auto User (0.99), "
               "Night stays Night (0.5), still flying with Auto User. The resolved values do not "
               "change at all and the dropdown loses its User Default entry. Or Don't ask again.",
        drop=[], user=user_xml(added("C172SP.*", ("max_aileron_coeff", "0.99", DEV),
                                     profiles={"Night": [("max_aileron_coeff", "0.5", DEV)]}))),

    "same-claim": dict(
        why="The user added the bare C172SP, which matches exactly what the curated C172SP.* "
            "matches - three real configs have this shape (AH-6J against AH-6J.*).",
        expect="An equal match goes to the shipped file, so C172SP.* names the aircraft and nothing "
               "under C172SP applies. PILL: merge - MOVES both profiles onto C172SP.* and removes "
               "C172SP, which claimed nothing the built-in does not. C172SP was flying with Night, so "
               "afterwards Active Profile reads Night and aileron_expo is 0.5, with the curated rows "
               "underneath. No permanent no is offered here: C172SP claims nothing the built-in does not, "
               "so its rows reach nothing at all. Only Merge and Not now.",
        drop=[], user=user_xml(added("C172SP", ("aileron_expo", "0.77", DEV),
                                     profiles={"Night": [("aileron_expo", "0.5", DEV)]},
                                     active="Night")),
        add_overrides=[("C172SP.*", "APMaster", "L:CURATED_AP_MASTER", "bool")]),

    "dismissed": dict(
        why="Same as user-specific, but the user already answered Keep mine.",
        expect="Their profile still wins, and NO pill appears: the answer is remembered per "
               "pattern pair in match_history.json, against the built-in as it stood. "
               "Profiles > Reset Dismissed Profile Prompts is enabled; taking it brings the "
               "pill straight back, and the dialog reads as it does for user-specific.",
        drop=[], user=user_xml(added("C172SP Classic.*", ("aileron_expo", "0.77", DEV))),
        declined=[("C172SP Classic.*", "C172SP.*")]),

    "dismissed-stale": dict(
        why="The user answered Keep mine, and a later release changed the built-in: C172SP.* "
            "now ships an APMaster override it did not have when they answered.",
        expect="Their profile still wins, but the PILL IS BACK: the answer was given against a "
               "different version of the built-in, so it has lapsed. The dialog's table shows the "
               "APMaster override as something the built-in brings that they do not have. Keep "
               "mine again and it stays quiet until the built-in changes once more.",
        drop=[], user=user_xml(added("C172SP Classic.*", ("aileron_expo", "0.77", DEV))),
        declined=[("C172SP Classic.*", "C172SP.*")],
        then_ship=dict(overrides=[("C172SP.*", "APMaster", "L:CURATED_AP_MASTER", "bool")])),

    "dismissed-renoted": dict(
        why="The user answered Keep mine, and a later release only reworded the built-in's "
            "notes. Nothing about what C172SP.* does has changed.",
        expect="Their profile still wins and NO pill appears: notes are not part of what a "
               "built-in does, so rewording them does not reopen an answered pair.",
        drop=[], user=user_xml(added("C172SP Classic.*", ("aileron_expo", "0.77", DEV))),
        declined=[("C172SP Classic.*", "C172SP.*")],
        then_ship=dict(notes={"C172SP.*": "Reworded in a later release; the profile is unchanged."})),

    "curated-moved": dict(
        why="A tighter curated pattern ships and takes the aircraft off a broader curated one; "
            "the user has nothing of their own.",
        expect="The tighter curated profile wins and NO pill appears - the shipped file's own "
               "decisions are not second-guessed.",
        drop=[], user=user_xml(), add_curated=[("C172SP Classic.*", "type", CLS, "any"),
                                               ("C172SP Classic.*", "max_aileron_coeff", "0.11", DEV)]),
}


def dev_mode_problem():
    """Why the bench cannot be used, or None when it can.

    The bench swaps the three files a DEV build reads from the repo root.
    With dev mode off the app reads %LOCALAPPDATA% instead, so a swap would
    do nothing visible - and the app would be writing the user's real config
    while the bench believed it owned a disposable copy."""
    flags = {}
    try:
        with open(os.path.join(REPO, "telemffb", "globals.py"), encoding="utf-8") as f:
            for line in f:
                if "=" not in line:
                    continue
                # The name must match exactly: dev_build_str starts with
                # dev_build and would otherwise answer for it.
                name = line.split("=", 1)[0].split(":")[0].strip()
                if name in ("dev_build", "dev_userconfig"):
                    flags[name] = line.split("=", 1)[1].split("#")[0].strip()
    except OSError as e:
        return f"could not read telemffb/globals.py ({e})"
    off = [f for f in ("dev_build", "dev_userconfig") if flags.get(f) != "True"]
    if off:
        return ("TelemFFB is not in dev mode: " + " and ".join(off) + " must be True in "
                "telemffb/globals.py.\nWithout it the app reads your real config in "
                "%LOCALAPPDATA% and the bench would be swapping files nothing looks at.")
    if not os.path.exists(os.path.join(REPO, "userconfig_v2.xml")):
        return ("there is no userconfig_v2.xml in the repo root yet.\nStart TelemFFB once with "
                "dev mode on: it copies your real config here, and that copy is what the bench "
                "swaps.")
    return None


def require_dev_mode():
    problem = dev_mode_problem()
    if problem:
        sys.exit("The scenario bench cannot run: " + problem)


def pristine_defaults():
    """Always build from the untouched defaults.xml, never from whichever
    scenario is currently swapped in - otherwise they compound."""
    backed = os.path.join(BACKUP, "defaults.xml")
    return backed if os.path.exists(backed) else os.path.join(REPO, "defaults.xml")


def ship(root, curated=(), overrides=(), notes=None):
    """Add shipped rows to a defaults tree: settings rows as (pattern,
    setting, value, device), overrides as (pattern, name, var, unit), and
    notes as {pattern: text} on the pattern's type row."""
    for pattern, setting, value, device in curated:
        e = ET.SubElement(root, "models")
        for tag, text in (("name", setting), ("model", pattern), ("value", value),
                          ("sim", SIM), ("device", device)):
            ET.SubElement(e, tag).text = text
    for pattern, name, var, unit in overrides:
        e = ET.SubElement(root, "sc_overrides")
        for tag, text in (("name", name), ("model", pattern), ("var", var), ("sc_unit", unit), ("sim", SIM)):
            ET.SubElement(e, tag).text = text
    for pattern, text in (notes or {}).items():
        row = root.find(f'models[sim="{SIM}"][model="{pattern}"][name="type"]')
        assert row is not None, f"no shipped type row for {pattern!r} to put notes on"
        note = row.find("notes")
        if note is None:
            note = ET.SubElement(row, "notes")
        note.text = text
    ET.indent(root, " ")


def build(name):
    spec = SCENARIOS[name]
    tree = ET.parse(pristine_defaults())
    root = tree.getroot()
    for elem in list(root.findall("models")):
        if elem.findtext("model") in spec.get("drop", []):
            root.remove(elem)
    ship(root, spec.get("add_curated", []), spec.get("add_overrides", []))
    return tree, spec


def telemffb_running():
    """A running dev build writes the very files a swap replaces, so it has
    to be closed first - otherwise it can save over a scenario, or over the
    real config a restore has just put back."""
    try:
        import subprocess
        out = subprocess.run(["wmic", "process", "get", "CommandLine"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return False
    return any("main.py" in ln and "python" in ln.lower() for ln in out.splitlines())


#: defaults.xml carries this in its header; a scenario copy, written by
#: ElementTree, does not.  It is how a leaked scenario is recognised.
PRISTINE_MARK = "TelemFFB Configuration Schema"


def do_use(name):
    require_dev_mode()
    if name not in SCENARIOS:
        sys.exit(f"unknown scenario {name!r}; try: py scenario.py list")
    live = os.path.join(REPO, "defaults.xml")
    if not os.path.isdir(BACKUP) and PRISTINE_MARK not in open(live, encoding="utf-8-sig").read(600):
        # No backup to build from, and the live file is already a scenario
        # copy: a previous run was never restored.  Backing this up would
        # bake the damage in, and defaults.xml is tracked, so git has it.
        sys.exit("defaults.xml in the repo is a leftover scenario copy, not the real file."
                 "\nPut it back first:  git checkout -- defaults.xml")
    if telemffb_running():
        sys.exit("TelemFFB looks like it is still running - close it first, or it will "
                 "write over the scenario (and over your real config on restore).")
    if not os.path.isdir(BACKUP):
        os.makedirs(BACKUP)
        for f in FILES:
            src = os.path.join(REPO, f)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(BACKUP, f))
        print(f"backed up your real files to {BACKUP}")
    tree, spec = build(name)
    tree.write(os.path.join(REPO, "defaults.xml"), encoding="utf-8", xml_declaration=True)
    with open(os.path.join(REPO, "userconfig_v2.xml"), "w", encoding="utf-8") as f:
        f.write(spec["user"])
    hp = os.path.join(REPO, "match_history.json")
    if spec.get("declined"):
        with open(hp, "w", encoding="utf-8") as f:
            json.dump(seeded_history(spec["declined"]), f, indent=1, sort_keys=True)
    elif os.path.exists(hp):
        os.remove(hp)
    if spec.get("then_ship"):
        # The answer above was given against the file as first written; this
        # is the release that came after it.
        ship(tree.getroot(), **spec["then_ship"])
        tree.write(os.path.join(REPO, "defaults.xml"), encoding="utf-8", xml_declaration=True)
    with open(ACTIVE, "w") as f:
        f.write(name)
    print(f"\nscenario '{name}' is active.")
    brief(name)
    do_verify()
    print(f"\nStart TelemFFB, load {AC!r}, and compare it with the above.\n")


def seeded_history(pairs):
    """An already-answered collision, as the app would have written it.  A
    decline carries a fingerprint of the shipped profile it was given
    against, so it has to be read off the scenario's own defaults.xml
    rather than made up here."""
    sys.path.insert(0, REPO)
    import telemffb.globals as G
    from telemffb import match_history, xmlutils
    G.userconfig_path = os.path.join(REPO, "userconfig_v2.xml")
    G.defaults_path = os.path.join(REPO, "defaults.xml")
    xmlutils.update_vars(DEV, G.userconfig_path, G.defaults_path)
    xmlutils.update_roots()
    out = {}
    for user, curated in pairs:
        shipped = match_history.fingerprint(xmlutils.curated_rows_for_fingerprint(SIM, curated))
        out.setdefault(user, {})[curated] = {"how": match_history.DECLINED, "shipped": shipped}
    return {"matches": {}, "collisions": {SIM: out}}


def brief(name):
    """The scenario in words: the situation it stands for and what the app
    is expected to do with it."""
    spec = SCENARIOS[name]
    print()
    for label, key in (("Situation", "why"), ("Expect", "expect")):
        head = f"  {label:9} "
        for j, line in enumerate(textwrap.wrap(spec[key], WIDTH - len(head))):
            print((head if j == 0 else " " * len(head)) + line)


def do_restore():
    if not os.path.isdir(BACKUP):
        sys.exit("no backup to restore - nothing was swapped in")
    if telemffb_running():
        sys.exit("TelemFFB looks like it is still running - close it first, or it will "
                 "write over your real config the moment it is put back.")
    for f in FILES:
        src, dst = os.path.join(BACKUP, f), os.path.join(REPO, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.remove(dst)
    shutil.rmtree(BACKUP)
    if os.path.exists(ACTIVE):
        os.remove(ACTIVE)
    print("your real defaults.xml, userconfig_v2.xml and match_history.json are back")


def do_verify():
    """Resolve the active scenario headlessly and say what the app should do."""
    if not os.path.exists(ACTIVE):
        sys.exit("no scenario active")
    name = open(ACTIVE).read().strip()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, REPO)
    os.chdir(REPO)
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import telemffb.globals as G
    from telemffb import xmlutils
    G.userconfig_path = os.path.join(REPO, "userconfig_v2.xml")
    G.defaults_path = os.path.join(REPO, "defaults.xml")
    G.device_type, G.master_instance = DEV, True
    xmlutils.update_vars(DEV, G.userconfig_path, G.defaults_path)
    xmlutils.update_roots()
    r = xmlutils._resolver()
    win = r.get_pattern_by_sim_fullname(SIM, AC)
    print(f"\n  What the app should show for {AC!r}\n  {'-'*(WIDTH - 2)}")
    if not win:
        print("  no pattern names it: the new-aircraft wizard fires")
        words = AC.split()
        pats = [' '.join(words[:i]) + ".*" for i in range(len(words), 0, -1)]
        from telemffb.NewAircraftWizard import NewAircraftWizard
        stub = type("S", (), {"clone_from": None,
                              "_MIN_SUGGESTED_WORDS": NewAircraftWizard._MIN_SUGGESTED_WORDS})()
        i = NewAircraftWizard._default_suggestion(stub, pats, AC)
        print(f"  wizard suggestions : {pats}")
        print(f"  preselected        : {pats[i]!r}")
        return
    cls, pattern, settings = xmlutils.read_single_model(SIM, AC, "", DEV)
    prof = r.get_active_profile_for_model(SIM, cls, pattern)
    col = r.collision(SIM, AC)
    # whose the winner is: on the identical string both trees hold a type row
    # and the tie went to the shipped file, so "yours" would be wrong there
    mine = col["winner"] == "user" if col else r.is_user_pattern(SIM, pattern)
    vals = {s["name"]: s["value"] for s in settings}
    print(f"  Matched Model  : {pattern!r}  ({'yours' if mine else 'built-in'})")
    print(f"  Class / Profile: {cls} / {prof}")
    for k in ("aileron_expo", "elevator_expo", "max_aileron_coeff", "elevator_droop_moment",
              "joystick_trim_follow_use_curve_y"):
        if k in vals:
            print(f"     {k:34} = {vals[k]}")
    import telemffb.match_history as mh
    if not col:
        print("  Collision      : none - nothing of the user's competes with a shipped pattern")
        print("  PILL           : none")
    else:
        who = "yours" if col["winner"] == "user" else "the built-in"
        print(f"  Collision      : yours {col['user']!r} vs built-in {col['curated']!r}; "
              f"{who} names it" + (" (same claim)" if col["same_claim"] else ""))
        shipped = mh.fingerprint(xmlutils.curated_rows_for_fingerprint(SIM, col["curated"]))
        answered = mh.resolution(SIM, col["user"], col["curated"], shipped)
        if answered:
            print(f"  PILL           : none - this pair was already answered ({answered})")
        else:
            keep = col["winner"] == "curated" and not col["same_claim"]
            from telemffb.ProfileOfferDialog import offers_decline
            if not offers_decline(col):
                answers = "none - their rows reach nothing, so Merge and Not now only"
            elif col["winner"] == "user":
                answers = "Keep mine"
            else:
                answers = chr(39).join(["Don", "t ask again"])
            print(f"  PILL           : merge {'COPIES' if keep else 'MOVES'} {col['user']!r} onto "
                  f"{col['curated']!r}; other answer: {answers}")
            pv = r.merge_preview(SIM, AC, col["user"], col["curated"])
            print(f"  Dialog         : in effect now: {pv['in_effect']}; merging applies {pv['restores']} "
                  f"of yours that are shut out, leaves {pv['keeps']} working as they are, adds "
                  f"{pv['gains']} from the built-in"
                  + (f"; arriving as: {', '.join(pv['other_profiles'])}" if pv['other_profiles'] else ""))
            print(f"                   {'entry':34} {'built-in':28} {'yours':28} post merge")
            short = lambda v: v if len(v) <= 26 else v[:23] + "..."      # a trim curve is thousands of chars
            for e in pv["entries"]:
                tag = " (override)" if e["kind"] == "override" else ""
                cell = lambda v: short(v) if v is not None else "-"
                mark = "*" if e["changes"] else ("!" if e["conflict"] else " ")
                print(f"                 {mark} {e['name'] + tag:34} {cell(e['built_in']):28} "
                      f"{cell(e['yours']):28} {cell(e['after'])}")
            if pv["conflicts"]:
                print(f"                   ! on {pv['conflicts']} row(s) the two set the same thing "
                      f"to different values; theirs stands")


def active_name():
    return open(ACTIVE).read().strip() if os.path.exists(ACTIVE) else None


def run():
    """The whole session: pick a scenario, fly it, pick the next, quit."""
    require_dev_mode()
    global AC
    names = list(SCENARIOS)
    while True:
        active = active_name()
        print("\n" + "=" * WIDTH)
        print(f" TelemFFB profile-matching scenarios        aircraft: {AC!r}")
        print("=" * WIDTH)
        if active:
            print(f" ACTIVE: {active}   (your real config is safe in _scenario_backup)\n")
        else:
            print(" nothing swapped in - your real config is live\n")
        for i, n in enumerate(names, 1):
            mark = "*" if n == active else " "
            head = f" {mark}{i:2}  {n:17} "
            # Wrapped, not cut: the description is the only thing that says
            # what a scenario is for, and half of one says nothing.
            for j, line in enumerate(textwrap.wrap(SCENARIOS[n]["why"], WIDTH - len(head))):
                print((head if j == 0 else " " * len(head)) + line)
        print("\n   v  verify - what the app should show for the active scenario")
        print("   a  change the aircraft title used for verifying")
        print("   q  quit and put your real config back")
        print("\n Start TelemFFB however you normally do; come back here to switch scenario.")
        try:
            choice = input("\n choice> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "q"
        if choice == "q":
            if os.path.isdir(BACKUP):
                do_restore()
            else:
                print("nothing to restore")
            return
        if choice == "v":
            if active:
                do_verify()
            else:
                print("  no scenario active - pick one first")
        elif choice == "a":
            try:
                new = input(f"  aircraft title [{AC}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                new = ""
            if new:
                AC = new
                print(f"  verifying against {AC!r} from now on")
        elif choice.isdigit() and 1 <= int(choice) <= len(names):
            name = names[int(choice) - 1]
            if telemffb_running():
                print("\n  TelemFFB is still running - close it first, then pick again.")
                continue
            do_use(name)
        elif choice:
            print(f"  '{choice}'?")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "list":
        active = open(ACTIVE).read().strip() if os.path.exists(ACTIVE) else None
        if active:
            print(f"\n  *** scenario {active!r} is SWAPPED IN - your real config is aside in"
                  f"\n      {BACKUP}"
                  f"\n      run 'restore' before doing any real work ***")
        print(f"\naircraft under test: {AC!r}\n")
        for n, s in SCENARIOS.items():
            mark = " <- ACTIVE" if n == active else ""
            print(f"  {n:17} {s['why']}{mark}")
        print("\n  py scenario.py use <name> | verify | restore")
    elif cmd == "use":
        do_use(sys.argv[2])
    elif cmd == "verify":
        do_verify()
    elif cmd == "restore":
        do_restore()
    else:
        sys.exit(f"unknown command {cmd!r}")
