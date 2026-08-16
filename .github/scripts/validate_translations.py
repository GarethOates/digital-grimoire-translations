#!/usr/bin/env python3
"""Validate translation files against the English source of truth.

Two severities:

  ERROR   - the file would break the app if merged. Fails CI, but only for
            files the pull request actually touches.
  WARNING - the file is incomplete or has drifted. Annotated on the PR so the
            contributor can see what is left to do, never fails the build.

The split matters: the repository carries a lot of pre-existing drift (most
languages predate the :reminder: placeholder and the newest roles). Hard
failing on that would make every PR red for reasons the contributor did not
cause, and the signal would be ignored within a week.

Usage:
    validate_translations.py                 # advisory sweep, always exits 0
    validate_translations.py FILE [FILE ...] # gate the listed files
    validate_translations.py @list.txt       # gate paths listed one per line

The @list form exists because 'User Interface/' contains a space, which makes
piping `git diff --name-only` through a shell argument list unreliable.
"""

import json
import os
import re
import sys

ROLES_DIR = "Roles"
UI_DIR = "User Interface"
BASE_ROLES = os.path.join(ROLES_DIR, "roles.json")
BASE_UI = os.path.join(UI_DIR, "en-US.json")

# Entries every language file carries that are not characters: the night phase
# bookends and the two info handouts. They are absent from roles.json but drive
# the night order sheet, so a file without them shows English mid-game.
SPECIAL_IDS = ("dusk", "dawn", "minioninfo", "demoninfo")

ROLES_NAME = re.compile(r"^[a-z]{2}_[A-Z]{2}\.json$")
UI_NAME = re.compile(r"^[a-z]{2}-[A-Z]{2}\.json$")

# Mirrors _REMINDER_PLACEHOLDER in api/roles/loader.py. The API rewrites each
# match to a purple marker, so a count that disagrees with English means the
# storyteller loses a prompt or gains a phantom one.
REMINDER = re.compile(r"(?<!\S):[^\s:][^:\n]{0,20}:")
BRACE = re.compile(r"\{[^}]*\}")

ISO_3166 = set(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ
    BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU
    CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB
    GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL
    IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI
    LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV
    MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN
    PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR
    SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US
    UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split()
)

errors = []
warnings = []
gated = None  # set of paths a failure may be reported against


def rel(path):
    return path.replace(os.sep, "/")


def report(bucket, path, message, line=None):
    bucket.append((rel(path), message, line))


def error(path, message, line=None):
    # Only fail for files this run is gating; everything else is advisory.
    if gated is None or rel(path) in gated:
        report(errors, path, message, line)
    else:
        report(warnings, path, message, line)


def warn(path, message, line=None):
    report(warnings, path, message, line)


def load_json(path):
    """Parse a file, turning the two failure modes contributors actually hit
    into messages that say what to do."""
    with open(path, encoding="utf-8-sig") as handle:
        raw = handle.read()
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        hint = ""
        line = raw.splitlines()[exc.lineno - 1] if exc.lineno <= len(raw.splitlines()) else ""
        if "//" in line or "/*" in line:
            hint = (
                " — this line has a JavaScript-style comment. JSON does not "
                "support comments; delete it (put the note in the PR instead)."
            )
        elif re.search(r",\s*[}\]]", line):
            hint = " — trailing comma before a closing bracket."
        return None, f"invalid JSON at line {exc.lineno}: {exc.msg}{hint}"


def flatten(node, prefix=""):
    out = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def check_filename(path):
    name = os.path.basename(path)
    directory = os.path.dirname(path)
    if directory == ROLES_DIR:
        if name == "roles.json":
            return
        if not ROLES_NAME.match(name):
            error(
                path,
                f"'{name}' does not match the Roles/ naming convention 'xx_YY.json' "
                "(underscore, lowercase language, uppercase region). The API loads "
                "/api/languages/<code>.json, so a mis-named file is never read.",
            )
            return
        region = name[3:5]
    elif directory == UI_DIR:
        if not UI_NAME.match(name):
            error(
                path,
                f"'{name}' does not match the User Interface/ naming convention "
                "'xx-YY.json' (hyphen, lowercase language, uppercase region).",
            )
            return
        region = name[3:5]
    else:
        return

    if region not in ISO_3166:
        error(
            path,
            f"'{region}' is not a current ISO 3166-1 alpha-2 country code. "
            "Use the country the language is spoken in (Czech is CZ, not CS).",
        )


def check_roles_file(path, canon):
    data, problem = load_json(path)
    if problem:
        error(path, problem)
        return
    if not isinstance(data, list):
        error(path, "expected a JSON array of role objects.")
        return

    seen = {}
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            error(path, f"entry {index} is not an object.")
            continue
        role_id = entry.get("id")
        if not role_id:
            error(path, f"entry {index} has no 'id'.")
            continue
        if role_id in seen:
            error(path, f"duplicate id '{role_id}' (also at entry {seen[role_id]}).")
            continue
        seen[role_id] = index
        if not entry.get("name"):
            error(path, f"'{role_id}' has no 'name'.")
        if role_id not in SPECIAL_IDS and role_id in canon and not entry.get("ability"):
            error(path, f"'{role_id}' has no 'ability'.")

    missing = [i for i in canon if i not in seen]
    if missing:
        warn(
            path,
            f"{len(missing)} role(s) from roles.json not translated: "
            f"{', '.join(sorted(missing)[:8])}"
            + (" …" if len(missing) > 8 else ""),
        )

    missing_specials = [i for i in SPECIAL_IDS if i not in seen]
    if missing_specials:
        warn(
            path,
            f"missing night-order entries: {', '.join(missing_specials)}. "
            "Without these the night sheet shows English. Copy the shape from "
            "Roles/hu_HU.json.",
        )

    drift = []
    for role_id, entry in ((k, data[v]) for k, v in seen.items()):
        reference = canon.get(role_id)
        if not reference:
            continue
        for field in ("firstNightReminder", "otherNightReminder"):
            expected = len(REMINDER.findall(reference.get(field) or ""))
            actual = len(REMINDER.findall(entry.get(field) or ""))
            if expected != actual:
                drift.append(f"{role_id}.{field} (en={expected}, here={actual})")
    if drift:
        warn(
            path,
            f"{len(drift)} :reminder: placeholder count mismatch(es) vs English: "
            f"{'; '.join(drift[:5])}" + (" …" if len(drift) > 5 else ""),
        )


def check_ui_file(path, base):
    data, problem = load_json(path)
    if problem:
        error(path, problem)
        return
    if not isinstance(data, dict):
        error(path, "expected a JSON object.")
        return

    flat = flatten(data)

    for key, value in flat.items():
        if key not in base or not isinstance(value, str) or not isinstance(base[key], str):
            continue
        if sorted(BRACE.findall(base[key])) != sorted(BRACE.findall(value)):
            error(
                path,
                f"'{key}' has different {{placeholders}} to English "
                f"(en: {sorted(BRACE.findall(base[key]))}, "
                f"here: {sorted(BRACE.findall(value))}). "
                "These are substituted at runtime and must match exactly.",
            )

    missing = [k for k in base if k not in flat]
    if missing:
        warn(
            path,
            f"{len(missing)} key(s) missing vs en-US.json: "
            f"{', '.join(missing[:8])}" + (" …" if len(missing) > 8 else ""),
        )

    extra = [k for k in flat if k not in base]
    if extra:
        warn(
            path,
            f"{len(extra)} key(s) not in en-US.json (removed upstream?): "
            f"{', '.join(extra[:8])}" + (" …" if len(extra) > 8 else ""),
        )


def annotate(bucket, level):
    for path, message, line in bucket:
        location = f"file={path}" + (f",line={line}" if line else "")
        # Collapse newlines: GitHub annotations are single-line.
        print(f"::{level} {location}::{message.replace(chr(10), ' ')}")


def main():
    global gated
    argv = [a for a in sys.argv[1:] if a.strip()]
    if len(argv) == 1 and argv[0].startswith("@"):
        listing = argv[0][1:]
        if os.path.exists(listing):
            with open(listing, encoding="utf-8") as handle:
                argv = [line.strip() for line in handle if line.strip()]
        else:
            argv = []
    if argv:
        gated = {rel(a) for a in argv}

    if not os.path.isdir(ROLES_DIR) or not os.path.isdir(UI_DIR):
        print("::error::run this from the repository root", file=sys.stderr)
        return 1

    canon_data, problem = load_json(BASE_ROLES)
    if problem:
        print(f"::error file={BASE_ROLES}::{problem}")
        return 1
    canon = {r["id"]: r for r in canon_data}

    base_data, problem = load_json(BASE_UI)
    if problem:
        print(f"::error file={BASE_UI}::{problem}")
        return 1
    base = flatten(base_data)

    targets = []
    for directory in (ROLES_DIR, UI_DIR):
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                targets.append(os.path.join(directory, name))

    for path in targets:
        check_filename(path)
        if rel(path) == BASE_UI:
            continue
        if os.path.dirname(path) == ROLES_DIR:
            if os.path.basename(path) != "roles.json":
                check_roles_file(path, canon)
        else:
            check_ui_file(path, base)

    annotate(warnings, "warning")
    annotate(errors, "error")

    scope = f"{len(gated)} changed file(s)" if gated else "all files (advisory)"
    print(f"\nvalidate-translations: {scope} — {len(errors)} error(s), {len(warnings)} warning(s)")

    if errors:
        label = "Errors" if gated else "Pre-existing errors (not failing an advisory run)"
        print(f"\n{label}:")
        for path, message, _ in errors:
            print(f"  {path}: {message}")
        # An advisory sweep reports history it did not cause; only a gated run
        # (a pull request) may fail the build.
        return 1 if gated else 0

    print("No blocking problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
